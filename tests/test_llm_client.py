import requests
import pytest

from app.llm import client


class FakeResponse:
    def __init__(self, status_code=200, text="", lines=None):
        self.status_code = status_code
        self.text = text
        self._lines = lines or []
        self.closed = False

    def iter_lines(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


def test_direct_session_ignores_system_proxy():
    assert client.create_http_session(False).trust_env is False


def test_stream_chat_reads_stream(monkeypatch):
    response = FakeResponse(
        lines=[
            b'data: {"choices":[{"delta":{"content":"ok"}}]}',
            b"data: [DONE]",
        ]
    )
    monkeypatch.setattr(client._SESSION, "post", lambda *args, **kwargs: response)

    assert list(client.stream_chat([{"role": "user", "content": "test"}])) == ["ok"]
    assert response.closed is True


def test_chat_explicitly_disables_thinking_without_increasing_cost_cap(monkeypatch):
    response = FakeResponse(lines=[b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]'])
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs["json"])
        return response

    monkeypatch.setattr(client._SESSION, "post", capture)
    assert list(client.stream_chat([{"role": "user", "content": "test"}])) == ["ok"]
    assert seen["thinking"] == {"type": "disabled"}
    assert seen["max_tokens"] == 2048


def test_usage_only_packets_do_not_break_chat(monkeypatch):
    response = FakeResponse(lines=[
        b'data: {"choices":[],"usage":{"total_tokens":2}}',
        b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
        b'data: [DONE]',
    ])
    monkeypatch.setattr(client._SESSION, "post", lambda *args, **kwargs: response)
    assert list(client.stream_chat([{"role": "user", "content": "test"}])) == ["ok"]
    assert response.closed


def test_thinking_only_response_is_safe_explicit_error(monkeypatch):
    response = FakeResponse(lines=[
        b'data: {"choices":[{"delta":{"reasoning_content":"private reasoning"}}]}',
        b'data: [DONE]',
    ])
    monkeypatch.setattr(client._SESSION, "post", lambda *args, **kwargs: response)
    with pytest.raises(client.LLMServiceError, match="没有最终回答") as error:
        list(client.stream_chat([{"role": "user", "content": "test"}]))
    assert "private reasoning" not in str(error.value)
    assert response.closed


def test_truncated_answer_is_not_accepted_as_success(monkeypatch):
    response = FakeResponse(lines=[
        b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}',
        b'data: [DONE]',
    ])
    monkeypatch.setattr(client._SESSION, "post", lambda *args, **kwargs: response)
    iterator = client.stream_chat([{"role": "user", "content": "test"}])
    assert next(iterator) == "partial"
    with pytest.raises(client.LLMServiceError, match="输出长度上限"):
        list(iterator)
    assert response.closed


def test_provider_error_body_is_not_exposed_and_connection_is_closed(monkeypatch):
    response = FakeResponse(status_code=500, text="secret-and-private-source-echo")
    monkeypatch.setattr(client._SESSION, "post", lambda *args, **kwargs: response)
    try:
        list(client.stream_chat([{"role": "user", "content": "test"}]))
    except client.LLMServiceError as exc:
        assert "500" in str(exc)
        assert "secret-and-private" not in str(exc)
    else:
        raise AssertionError("Expected provider error")
    assert response.closed


def test_connection_error_is_user_readable(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("low-level details")

    monkeypatch.setattr(client._SESSION, "post", fail)

    try:
        list(client.stream_chat([{"role": "user", "content": "test"}]))
    except client.LLMServiceError as exc:
        assert "无法连接 DeepSeek" in str(exc)
    else:
        raise AssertionError("LLMServiceError was not raised")
