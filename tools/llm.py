"""Model access. Meterless Nightshift is the primary provider (Meterless Runtime provider contract v4).
NVIDIA's hosted API is an interim fallback used only when Nightshift cannot be reached at all; a typed
refusal from Nightshift goes back to the caller and never falls back."""

import logging
import re
import ssl
import time
from typing import Any, Literal

import httpx
import truststore
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from settings import Settings

log = logging.getLogger("llm")

ProviderState = Literal["online", "unauthorized", "offline"]
PLACEHOLDER_TOKENS = {"", "rt-local"}


def normalise_base_url(url: str) -> str:
    """Accept a pasted URL with or without scheme, /v1, /v1/chat/completions, /v1/models or /healthz."""
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "http://" + url
    url = re.sub(r"(/v1/chat/completions|/v1/models|/healthz|/v1)?/*$", "", url)
    return url + "/v1"


class LlmResponse(BaseModel):
    text: str
    provider: Literal["meterless", "nvidia"]
    requested_model: str
    model: str
    duration_s: float
    fallback_reason: str | None = None
    usage: dict[str, Any] | None = None


class LlmRefusal(RuntimeError):
    """The provider answered and refused. Carries the server's message, code and fix list."""

    def __init__(self, provider: str, status: int, message: str, code: str | None = None, fix: list[str] | None = None):
        self.provider, self.status, self.code, self.fix = provider, status, code, fix or []
        hint = " ".join(self.fix)
        if not hint and status in (401, 403):
            hint = "Paste the runtime's access token from Nightshift's Console into METERLESS_RUNTIME_TOKEN."
        super().__init__(f"{provider} refused (HTTP {status}{', ' + code if code else ''}): {message}" + (f" {hint}" if hint else ""))


class LlmUnavailable(RuntimeError):
    pass


def _refusal(provider: str, response: httpx.Response) -> LlmRefusal:
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        if isinstance(error, str):
            error = {"message": error}
        fix = (body.get("meterless") or {}).get("fix") or error.get("fix")
        return LlmRefusal(provider, response.status_code, error.get("message") or response.text[:300], error.get("code"), fix)
    except ValueError:
        return LlmRefusal(provider, response.status_code, response.text[:300])


def _retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    return isinstance(error, LlmRefusal) and (error.status == 429 or error.status >= 500)


class LlmClient:
    def __init__(self, settings: Settings, meterless_transport: httpx.BaseTransport | None = None,
                 nvidia_transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._token = settings.meterless_runtime_token.get_secret_value().strip()
        self.base_url = normalise_base_url(settings.meterless_base_url)
        headers = {"meterless-app": settings.meterless_app}
        if self.configured:
            headers["Authorization"] = f"Bearer {self._token}"
        self._meterless = httpx.Client(base_url=self.base_url, timeout=settings.llm_timeout_s,
                                       transport=meterless_transport, headers=headers)
        nvidia_key = settings.nvidia_api_key.get_secret_value()
        self._nvidia = None
        if nvidia_key:
            self._nvidia = httpx.Client(
                base_url=settings.nvidia_base_url.rstrip("/"), timeout=settings.llm_timeout_s, transport=nvidia_transport,
                verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                headers={"Authorization": f"Bearer {nvidia_key}"},
            )

    @property
    def configured(self) -> bool:
        return self._token not in PLACEHOLDER_TOKENS

    def status(self) -> tuple[ProviderState, list[str]]:
        """GET /v1/models decides: 401/403 unauthorized, 200 with meterless ids online, anything else offline."""
        try:
            response = self._meterless.get("/models", timeout=5.0)
        except httpx.HTTPError:
            return "offline", []
        if response.status_code in (401, 403):
            return "unauthorized", []
        if response.status_code == 200:
            ids = [m.get("id", "") for m in response.json().get("data", [])]
            meterless_ids = [i for i in ids if i.startswith("meterless/")]
            if meterless_ids:
                return "online", meterless_ids
        return "offline", []

    def chat(self, messages: list[dict[str, str]], model: str | None = None, temperature: float = 0.0,
             max_tokens: int | None = None, response_format: dict | None = None) -> LlmResponse:
        requested = model or self._settings.meterless_model
        body: dict[str, Any] = {"model": requested, "messages": messages, "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if response_format:
            body["response_format"] = response_format

        if not self.configured:
            return self._fallback(body, "METERLESS_RUNTIME_TOKEN is not set")
        start = time.monotonic()
        try:
            response = self._meterless.post("/chat/completions", json=body, headers={"meterless-scope": "user"})
        except httpx.TransportError as error:
            return self._fallback(body, f"Nightshift unreachable at {self.base_url} ({type(error).__name__})")
        if response.status_code >= 400:
            raise _refusal("meterless", response)
        return self._result("meterless", requested, response.json(), start)

    def _fallback(self, body: dict[str, Any], reason: str) -> LlmResponse:
        if self._nvidia is None:
            raise LlmUnavailable(f"{reason}; no fallback provider configured")
        model = body["model"] if body["model"].startswith("nvidia/") else self._settings.nvidia_model
        log.warning("fallback to nvidia (%s): %s", model, reason)
        start = time.monotonic()
        data = self._post_nvidia({**body, "model": model})
        return self._result("nvidia", body["model"], data, start, fallback_reason=reason)

    @retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8), reraise=True)
    def _post_nvidia(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self._nvidia.post("/chat/completions", json=body)
        if response.status_code >= 400:
            raise _refusal("nvidia", response)
        return response.json()

    def _result(self, provider: str, requested: str, data: dict[str, Any], start: float,
                fallback_reason: str | None = None) -> LlmResponse:
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        result = LlmResponse(text=text, provider=provider, requested_model=requested, model=data.get("model") or requested,
                             duration_s=round(time.monotonic() - start, 3), fallback_reason=fallback_reason,
                             usage=data.get("usage"))
        log.info("provider=%s requested=%s model=%s duration_s=%.2f", provider, requested, result.model, result.duration_s)
        return result

    def close(self) -> None:
        self._meterless.close()
        if self._nvidia is not None:
            self._nvidia.close()
