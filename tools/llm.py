"""Model access. Meterless Nightshift is the only model provider (Meterless Runtime provider contract v4); NVIDIA
models are reached through Nightshift's Providers tab, never with a key in this app. A typed refusal from Nightshift
goes back to the caller; an unreachable Nightshift or a missing token raises LlmUnavailable, and the callers then
keep their deterministic path (the critic flags "LLM critic unavailable")."""

import logging
import re
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel

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
    provider: Literal["meterless"]
    requested_model: str
    model: str
    duration_s: float
    usage: dict[str, Any] | None = None
    receipt_id: str | None = None           # Nightshift's receipt for this call (its run details show it)
    route_provider: str | None = None       # the provider behind the lane that answered: nvidia, anthropic, google
    cost_usd: float | None = None           # what Nightshift reports the call cost


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


class LlmClient:
    def __init__(self, settings: Settings, meterless_transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._token = settings.meterless_runtime_token.get_secret_value().strip()
        self.base_url = normalise_base_url(settings.meterless_base_url)
        headers = {"meterless-app": settings.meterless_app}
        if self.configured:
            headers["Authorization"] = f"Bearer {self._token}"
        self._meterless = httpx.Client(base_url=self.base_url, timeout=settings.llm_timeout_s,
                                       transport=meterless_transport, headers=headers)

    @property
    def configured(self) -> bool:
        return self._token not in PLACEHOLDER_TOKENS

    def status(self, timeout_s: float = 5.0) -> tuple[ProviderState, list[str]]:
        """GET /v1/models decides: 401/403 unauthorized, 200 with meterless ids online, anything else offline."""
        try:
            response = self._meterless.get("/models", timeout=timeout_s)
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
             max_tokens: int | None = None, response_format: dict | None = None, app: str | None = None,
             timeout_s: float | None = None) -> LlmResponse:
        requested = model or self._settings.meterless_model
        body: dict[str, Any] = {"model": requested, "messages": messages, "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if response_format:
            body["response_format"] = response_format

        if not self.configured:
            raise LlmUnavailable("METERLESS_RUNTIME_TOKEN is not set")
        start = time.monotonic()
        try:
            headers = {"meterless-scope": "user", **({"meterless-app": app} if app else {})}
            response = self._meterless.post("/chat/completions", json=body, headers=headers,
                                            timeout=timeout_s or self._settings.llm_timeout_s)
        except httpx.TransportError as error:
            raise LlmUnavailable(f"Nightshift unreachable ({type(error).__name__})") from error
        if response.status_code >= 400:
            raise _refusal("meterless", response)
        return self._result(requested, response.json(), start)

    def _result(self, requested: str, data: dict[str, Any], start: float) -> LlmResponse:
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        meta = data.get("meterless") if isinstance(data.get("meterless"), dict) else {}
        route = meta.get("winning_route") if isinstance(meta.get("winning_route"), dict) else {}
        result = LlmResponse(text=text, provider="meterless", requested_model=requested,
                             model=data.get("model") or requested, duration_s=round(time.monotonic() - start, 3),
                             usage=data.get("usage"), receipt_id=meta.get("receipt_id"),
                             route_provider=route.get("provider"),
                             cost_usd=(meta.get("cost") or {}).get("actual_usd") if isinstance(meta.get("cost"), dict)
                             else None)
        log.info("provider=meterless requested=%s model=%s duration_s=%.2f", requested, result.model, result.duration_s)
        return result

    def close(self) -> None:
        self._meterless.close()
