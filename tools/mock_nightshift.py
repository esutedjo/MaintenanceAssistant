"""Mock Meterless Runtime for tests: enforces auth and headers, serves meterless/* ids and OpenAI-shaped chat."""

import json
from collections.abc import Callable

import httpx

MODES = ["meterless/auto", "meterless/fast", "meterless/deep", "meterless/cheap", "meterless/local"]


def mock_nightshift(token: str, served_by: str = "nvidia/nemotron-3.5-lightning-30b-a3b",
                    reply: Callable[[list[dict]], str] = lambda messages: "OK",
                    requests: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.headers.get("authorization") != f"Bearer {token}":
            return httpx.Response(401, json={"error": {"message": "invalid runtime token", "code": "auth_invalid"}})
        if not request.headers.get("meterless-app"):
            return httpx.Response(400, json={"error": {"message": "meterless-app header is required", "code": "app_required"}})
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": m, "object": "model"} for m in MODES]})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if body["model"] not in MODES:
                return httpx.Response(400, json={"error": {"message": f"unknown mode {body['model']}", "code": "class_no_route"},
                                                 "meterless": {"fix": ["Pick a mode listed by GET /v1/models."]}})
            return httpx.Response(200, json={
                "id": "chatcmpl-mock", "object": "chat.completion", "model": served_by,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply(body["messages"])}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        return httpx.Response(404, json={"error": {"message": "no route"}})

    return httpx.MockTransport(handler)
