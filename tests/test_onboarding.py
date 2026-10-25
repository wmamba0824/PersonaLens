"""First-use and live API configuration, with synthetic secrets and no HTTP."""

import json
from pathlib import Path

import pytest

from app.config import runtime
from app.llm import client

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    monkeypatch.setenv("MD_NETWORK_MODE", "local")
    original = LocalScriptRunner.run
    def joined(runner, *args, **kwargs):
        try:
            return original(runner, *args, **kwargs)
        finally:
            if runner._script_thread:
                runner._script_thread.join(timeout=3)
                assert not runner._script_thread.is_alive()
    monkeypatch.setattr(LocalScriptRunner, "run", joined)
    return lambda: AppTest.from_file(str(ROOT / "chat_app.py")).run(timeout=30)


def test_web_saved_settings_override_env_without_mutating_it(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-environment-value")
    assert runtime.get_config()["api_key"] == "test-environment-value"
    runtime.save_config("test-web-value", "https://example.invalid/v1/chat/completions", "test-model")
    assert runtime.get_config()["api_key"] == "test-web-value"
    assert runtime.environment_config()["api_key"] == "test-environment-value"
    runtime.save_config("", "https://example.invalid/v1/chat/completions", "changed-model")
    assert runtime.get_config()["api_key"] == "test-web-value"
    assert not list(runtime.CONFIG_FILE.parent.glob("*.tmp"))


@pytest.mark.parametrize("url", ["http://example.invalid/api", "file:///private", "https://user:password@example.invalid/api",
                                "https://example.invalid/api?key=private", "https://example.invalid/api#part", "https://example.invalid:bad/api"])
def test_invalid_endpoints_are_rejected_without_echo(url):
    with pytest.raises(runtime.ConfigError) as error:
        runtime.save_config("test-config-key", url, "test-model")
    assert url not in str(error.value) and not runtime.CONFIG_FILE.exists()


def test_corrupt_config_is_safe_and_can_be_repaired():
    runtime.CONFIG_FILE.parent.mkdir(parents=True)
    runtime.CONFIG_FILE.write_text("private malformed configuration", encoding="utf-8")
    with pytest.raises(runtime.ConfigError) as error:
        runtime.get_config()
    assert "private malformed" not in str(error.value)
    runtime.save_config("test-repaired-value", "https://example.invalid/chat", "test-model")
    assert runtime.get_config()["api_key"] == "test-repaired-value"


def test_keyless_import_and_first_use_form_save_without_network(page, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = page()
    assert not app.exception and len(app.sidebar.radio) == 0
    key = next(item for item in app.text_input if item.label == "API Key")
    assert key.value == "" and key.proto.type == key.proto.PASSWORD
    key.set_value("test-new-user-key")
    next(item for item in app.button if item.label == "保存配置并开始").click().run(timeout=30)
    assert not app.exception and app.sidebar.radio[0].value == "人物助手"
    assert runtime.get_config()["api_key"] == "test-new-user-key"
    assert not any(item.value == "test-new-user-key" for item in app.text_input)
    assert not any("test-new-user-key" in item.value for item in app.markdown)
    next(item for item in app.button if item.label == "API 设置").click().run(timeout=30)
    assert not app.exception
    assert next(item for item in app.text_input if item.label == "API Key").value == ""


def test_missing_key_does_not_trigger_network_or_create_config(page, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = page()
    next(item for item in app.button if item.label == "保存配置并开始").click().run(timeout=30)
    assert not app.exception and app.error and not runtime.CONFIG_FILE.exists()
    with pytest.raises(client.LLMServiceError, match="尚未配置"):
        list(client.stream_chat([{"role": "user", "content": "no key"}]))


def test_lan_cannot_configure_key_even_after_access_gate(page, monkeypatch):
    app = page()
    monkeypatch.setenv("MD_NETWORK_MODE", "lan")
    monkeypatch.setenv("MD_LAN_ACCESS_CODE", "test-access-code-123")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app.run(timeout=30)
    assert not app.exception and not any(item.label == "API Key" for item in app.text_input)
    next(item for item in app.text_input if item.label == "访问码").set_value("test-access-code-123")
    next(item for item in app.button if item.label == "进入对话").click().run(timeout=30)
    assert not app.exception and any("请先在电脑" in item.value for item in app.warning)
    assert not any(item.label == "API Key" for item in app.text_input)
    assert not runtime.CONFIG_FILE.exists()


def test_client_reads_changed_configuration_on_each_request(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def iter_lines(self):
            return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]'])
        def close(self):
            pass
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr(client._SESSION, "post", post)
    for key, model in (("test-key-first", "first-model"), ("test-key-second", "second-model")):
        runtime.save_config(key, "https://example.invalid/chat", model)
        assert list(client.stream_chat([{"role": "user", "content": "test"}])) == ["ok"]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-key-first"
    assert calls[1][1]["headers"]["Authorization"] == "Bearer test-key-second"
    assert calls[1][1]["json"]["model"] == "second-model"
    assert calls[1][1]["allow_redirects"] is False


def test_config_redaction_does_not_expose_web_key():
    runtime.save_config("test-secret-web-value", "https://example.invalid/chat", "test-model")
    assert runtime.redact("echo test-secret-web-value") == "echo [凭据已隐藏]"


def test_upload_limit_is_in_source_and_secrets_stay_ignored():
    import subprocess
    assert "maxUploadSize = 2" in (ROOT / ".streamlit/config.toml").read_text(encoding="utf-8")
    result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=str(ROOT),
        input=b".streamlit/config.toml\n.streamlit/secrets.toml\n.runtime/api-settings.json\n", capture_output=True)
    assert set(result.stdout.decode("utf-8").splitlines()) == {".streamlit/secrets.toml", ".runtime/api-settings.json"}
