"""Transport-level tests for OpenAICompatibleChatModel against a local mock
HTTP server - deterministic and offline. Model behaviour is NOT tested here
(that is the scripted provider's and the real endpoint's job); what IS
tested: retry semantics, JSON-mode fallback, error classification, key
redaction and config wiring."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app import config
from app.agent.llm import (
    LlmResponseInvalidError,
    LlmUnavailableError,
    OpenAICompatibleChatModel,
    build_chat_model,
)


def completion(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


class _MockHandler(BaseHTTPRequestHandler):
    """POST /chat/completions driven by server.script entries.

    Each entry is either ``(status, payload)`` or a callable
    ``body -> (status, payload)``. Exhausted scripts answer 200/OK.
    """

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.requests.append(body)
        entry = self.server.script.pop(0) if self.server.script else (200, completion("OK"))
        status, payload = entry(body) if callable(entry) else entry
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def mock_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.requests: list[dict] = []
    server.script: list = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def make_model(server, **overrides) -> OpenAICompatibleChatModel:
    params = dict(
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        model="mock-model",
        api_key="sk-secret-key-123",
        timeout_s=5,
        max_retries=2,
        json_mode=True,
        backoff_s=0.0,
    )
    params.update(overrides)
    return OpenAICompatibleChatModel(**params)


def test_success_first_try_sends_json_mode_and_temperature_zero(mock_server):
    model = make_model(mock_server)
    assert model.complete("system", "user") == "OK"
    assert len(mock_server.requests) == 1
    request = mock_server.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["temperature"] == 0
    assert request["model"] == "mock-model"


def test_retries_on_5xx_then_succeeds(mock_server):
    mock_server.script = [
        (500, {"error": "boom"}),
        (503, {"error": "boom"}),
        (200, completion("recovered")),
    ]
    model = make_model(mock_server)
    assert model.complete("s", "u") == "recovered"
    assert len(mock_server.requests) == 3


def test_retries_exhausted_raises_unavailable(mock_server):
    mock_server.script = [(429, {"error": "rate limited"})] * 3
    model = make_model(mock_server, max_retries=2)
    with pytest.raises(LlmUnavailableError) as excinfo:
        model.complete("s", "u")
    assert "429" in str(excinfo.value)
    assert len(mock_server.requests) == 3  # 1 + 2 retries


def test_auth_error_fails_immediately_and_never_leaks_key(mock_server):
    mock_server.script = [(401, {"error": {"message": "invalid api key"}})]
    model = make_model(mock_server)
    with pytest.raises(LlmUnavailableError) as excinfo:
        model.complete("s", "u")
    assert len(mock_server.requests) == 1  # no retry on auth errors
    assert "401" in str(excinfo.value)
    assert "sk-secret-key-123" not in str(excinfo.value)


def test_json_mode_400_falls_back_to_plain_request(mock_server):
    def reject_response_format(body):
        if "response_format" in body:
            return (400, {"error": "response_format unsupported"})
        return (200, completion("plain ok"))

    # two entries: the first request (with response_format) is rejected, the
    # fallback request (plain) succeeds through the same callable
    mock_server.script = [reject_response_format, reject_response_format]
    model = make_model(mock_server)
    assert model.complete("s", "u") == "plain ok"
    assert model.json_mode_effective is False
    assert "response_format" in mock_server.requests[0]  # first try: json mode on
    assert "response_format" not in mock_server.requests[1]  # fallback: plain
    assert len(mock_server.requests) == 2


def test_json_mode_off_never_sends_response_format(mock_server):
    model = make_model(mock_server, json_mode=False)
    assert model.complete("s", "u") == "OK"
    assert "response_format" not in mock_server.requests[0]


def test_invalid_completion_shape_raises_invalid(mock_server):
    mock_server.script = [(200, {"unexpected": "shape"})]
    model = make_model(mock_server)
    with pytest.raises(LlmResponseInvalidError):
        model.complete("s", "u")


def test_empty_content_raises_invalid(mock_server):
    mock_server.script = [(200, completion("   "))]
    model = make_model(mock_server)
    with pytest.raises(LlmResponseInvalidError):
        model.complete("s", "u")


def test_probe_returns_latency_content_and_mode(mock_server):
    model = make_model(mock_server)
    info = model.probe()
    assert info["model"] == "mock-model"
    assert info["latency_ms"] >= 0
    assert info["content"] == "OK"
    assert info["json_mode_effective"] is True


def test_build_chat_model_wires_transport_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-chat")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-x")
    monkeypatch.setattr(config, "LLM_TIMEOUT_S", 7.5)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 3)
    monkeypatch.setattr(config, "LLM_JSON_MODE", False)
    monkeypatch.setattr(config, "LLM_BACKOFF_S", 0.25)
    model = build_chat_model("openai_compatible")
    assert model.provider == "openai_compatible"
    assert model._timeout_s == 7.5
    assert model._max_retries == 3
    assert model._backoff_s == 0.25
    assert model.json_mode_effective is False
