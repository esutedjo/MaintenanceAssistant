import httpx
import pytest

from settings import Settings
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable, normalise_base_url
from tools.mock_nightshift import mock_nightshift

TOKEN = "test-runtime-token"


def settings(token: str = TOKEN) -> Settings:
    return Settings(_env_file=None, meterless_runtime_token=token)


def unreachable(calls: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        raise httpx.ConnectError("connection refused", request=request)
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("pasted", [
    "127.0.0.1:8088", "http://127.0.0.1:8088", "http://127.0.0.1:8088/", "http://127.0.0.1:8088/v1",
    "http://127.0.0.1:8088/v1/", "http://127.0.0.1:8088/v1/chat/completions", "http://127.0.0.1:8088/v1/models",
    "http://127.0.0.1:8088/healthz",
])
def test_base_url_is_normalised_without_double_v1(pasted):
    assert normalise_base_url(pasted) == "http://127.0.0.1:8088/v1"


def test_status_distinguishes_online_unauthorized_offline():
    online = LlmClient(settings(), meterless_transport=mock_nightshift(TOKEN))
    assert online.status()[0] == "online" and "meterless/auto" in online.status()[1]
    assert LlmClient(settings("wrong"), meterless_transport=mock_nightshift(TOKEN)).status()[0] == "unauthorized"
    assert LlmClient(settings(), meterless_transport=unreachable()).status()[0] == "offline"
    assert LlmClient(settings(""), meterless_transport=mock_nightshift(TOKEN)).status()[0] == "unauthorized"


def test_chat_sends_contract_headers_and_reports_serving_model():
    seen = []
    client = LlmClient(settings(), meterless_transport=mock_nightshift(TOKEN, requests=seen))
    result = client.chat([{"role": "user", "content": "ping"}])
    assert (result.text, result.provider, result.requested_model) == ("OK", "meterless", "meterless/auto")
    assert result.model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    request = seen[-1]
    assert request.headers["meterless-app"] == "turbofan-maintenance-agent"
    assert request.headers["meterless-scope"] == "user"


def test_chat_can_route_as_a_separate_app():
    seen = []
    client = LlmClient(settings(), meterless_transport=mock_nightshift(TOKEN, requests=seen))
    client.chat([{"role": "user", "content": "ping"}], app="turbofan-maintenance-critic")
    assert seen[-1].headers["meterless-app"] == "turbofan-maintenance-critic"


def test_refusal_goes_back_to_the_caller():
    client = LlmClient(settings(), meterless_transport=mock_nightshift(TOKEN))
    with pytest.raises(LlmRefusal, match="Pick a mode"):
        client.chat([{"role": "user", "content": "x"}], model="meterless/unknown")
    with pytest.raises(LlmRefusal, match="access token"):
        LlmClient(settings("wrong"), meterless_transport=mock_nightshift(TOKEN)).chat([{"role": "user", "content": "x"}])


def test_nightshift_is_the_only_provider_and_an_outage_is_unavailable():
    """No direct NVIDIA API fallback: an unreachable Nightshift raises LlmUnavailable after one request."""
    calls = []
    client = LlmClient(settings(), meterless_transport=unreachable(calls))
    with pytest.raises(LlmUnavailable, match="Nightshift unreachable"):
        client.chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1 and all(c.url.host == "127.0.0.1" for c in calls)
    assert not any(name.startswith("nvidia") for name in Settings.model_fields)


def test_placeholder_token_is_not_sent_and_without_fallback_is_unavailable():
    for token in ("", "rt-local"):
        seen = []
        client = LlmClient(settings(token), meterless_transport=mock_nightshift(TOKEN, requests=seen))
        with pytest.raises(LlmUnavailable, match="METERLESS_RUNTIME_TOKEN"):
            client.chat([{"role": "user", "content": "x"}])
        assert seen == []
