"""Synthetic local cookies, no browser credential extraction or platform calls."""

import json
from pathlib import Path

import pytest
import requests

from app.config import bilibili as settings
from app.ingest import bilibili, video
from app.ingest.persona import redact_credentials
from app.ingest.exports import export_chat

ROOT = Path(__file__).resolve().parents[1]


def values():
    return {"SESSDATA": "test-sessdata-value", "bili_jct": "test-csrf-value",
            "buvid3": "test-device-value", "DedeUserID": "123456789"}


def test_environment_compatibility_web_override_and_disable_without_mutation(monkeypatch):
    monkeypatch.setenv("BILI_SESSDATA", "test-legacy-cookie")
    assert settings.get_cookies()["SESSDATA"] == "test-legacy-cookie"
    settings.save_cookies(values())
    assert settings.get_cookies() == values()
    settings.save_cookies(dict.fromkeys(settings.COOKIE_ENV, ""))
    assert settings.get_cookies() == values()
    assert settings.cookie_status() == {"configured": dict.fromkeys(settings.COOKIE_ENV, True), "source": "web", "verified": False}
    settings.disable_cookies()
    assert settings.get_cookies() == dict.fromkeys(settings.COOKIE_ENV, "")
    assert settings.cookie_status()["source"] == "web"
    assert settings.os.getenv("BILI_SESSDATA") == "test-legacy-cookie"
    assert not list(settings.COOKIE_FILE.parent.glob("*.tmp"))


@pytest.mark.parametrize("bad", ["value\nprivate", "SESSDATA=a; buvid3=b", '"quoted"', "space here", "非ASCII", "x" * 4097],
                         ids=["newline", "whole-header", "quotes", "space", "non-ascii", "oversize"])
def test_reject_bad_values_without_echo_or_overwrite(bad):
    settings.save_cookies(values())
    before = settings.COOKIE_FILE.read_bytes()
    payload = values()
    payload["SESSDATA"] = bad
    with pytest.raises(settings.CookieConfigError) as error:
        settings.save_cookies(payload)
    assert bad not in str(error.value) and settings.COOKIE_FILE.read_bytes() == before


def test_empty_first_save_and_unknown_fields_are_not_accepted():
    with pytest.raises(settings.CookieConfigError):
        settings.save_cookies(dict.fromkeys(settings.COOKIE_ENV, ""))
    assert not settings.COOKIE_FILE.exists()
    with pytest.raises(settings.CookieConfigError):
        settings.save_cookies({**values(), "DEEPSEEK_API_KEY": "test-key"})


def test_bad_config_fails_closed_and_can_be_repaired_or_disabled():
    settings.COOKIE_FILE.parent.mkdir(parents=True)
    settings.COOKIE_FILE.write_text("private corrupted file", encoding="utf-8")
    with pytest.raises(settings.CookieConfigError) as error:
        settings.get_cookies()
    assert "private corrupted file" not in str(error.value)
    settings.disable_cookies()
    assert not any(settings.get_cookies().values())
    settings.save_cookies(values())
    assert settings.get_cookies() == values()


def test_latest_cookie_is_scoped_to_secure_api_session_not_media_or_llm():
    from app.llm import client
    settings.save_cookies(values())
    api = video._session(auth=True)
    assert api.trust_env is False
    for cookie in api.cookies:
        assert cookie.domain == "api.bilibili.com" and cookie.secure
    prepared = api.prepare_request(requests.Request("GET", "https://api.bilibili.com/x/test"))
    assert "SESSDATA=test-sessdata-value" in prepared.headers["Cookie"]
    for url in ("https://api.bilibili.com.evil.example/test", "https://example.invalid/test", "http://api.bilibili.com/test"):
        assert "Cookie" not in api.prepare_request(requests.Request("GET", url)).headers
    media = video._session(auth=False)
    assert not media.cookies
    assert "Cookie" not in client._SESSION.prepare_request(requests.Request("POST", "https://example.invalid/chat")).headers
    changed = dict.fromkeys(settings.COOKIE_ENV, "")
    changed["SESSDATA"] = "test-new-cookie-value"
    settings.save_cookies(changed)
    newer = video._session(auth=True)
    assert newer.cookies.get("SESSDATA") == "test-new-cookie-value"
    api.close()
    media.close()
    newer.close()


def test_text_collector_reads_web_cookies_and_412_stops_after_one_call(monkeypatch):
    from app.ingest.identity import parse_creator
    settings.save_cookies(values())
    session = requests.Session()
    seen = []
    class Response:
        status_code = 412
        closed = False
        def close(self):
            self.closed = True
    response = Response()
    def get(url, **kwargs):
        seen.append((url, kwargs))
        assert session.cookies.get("SESSDATA") == "test-sessdata-value"
        return response
    monkeypatch.setattr(session, "get", get)
    monkeypatch.setattr(bilibili.requests, "Session", lambda: session)
    with pytest.raises(bilibili.CollectionError) as error:
        bilibili.collect_dynamics(parse_creator("123"))
    assert len(seen) == 1 and response.closed and seen[0][1]["allow_redirects"] is False
    assert "412" in str(error.value) and "B站采集设置" in str(error.value)
    assert "不保证" in str(error.value) and "test-sessdata-value" not in str(error.value)


def test_broken_cookie_config_does_not_send_anonymous_fallback_request(monkeypatch):
    from app.ingest.identity import parse_creator
    settings.COOKIE_FILE.parent.mkdir(parents=True)
    settings.COOKIE_FILE.write_text("invalid", encoding="utf-8")
    with pytest.raises(settings.CookieConfigError):
        bilibili.collect_dynamics(parse_creator("123"))


def test_web_cookies_are_redacted_from_profile_and_chat_exports():
    settings.save_cookies(values())
    joined = " ".join(values().values())
    redacted = redact_credentials(joined)
    assert not any(value in redacted for value in values().values())
    exported = export_chat({"name": "合成人物"}, [{"role": "user", "content": joined}])
    assert not any(value in exported for value in values().values())


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


def test_local_settings_save_hidden_values_preserve_and_disable_without_network(page):
    app = page()
    next(item for item in app.button if item.label == "B站采集设置").click().run(timeout=30)
    assert not app.exception
    assert not settings.COOKIE_FILE.exists()
    for name, value in values().items():
        field = next(item for item in app.text_input if item.label == name)
        assert field.value == "" and field.proto.type == field.proto.PASSWORD
        field.set_value(value)
    next(item for item in app.button if item.label == "保存B站配置").click().run(timeout=30)
    assert not app.exception and settings.get_cookies() == values()
    assert all(field.value == "" for field in app.text_input)
    assert not any(value in item.value for item in app.markdown for value in values().values())
    assert any("未向B站发请求" in item.value for item in app.success)
    assert next(item for item in app.button if item.label == "停用B站登录态").disabled
    next(item for item in app.checkbox if "确认停用" in item.label).check().run(timeout=30)
    next(item for item in app.button if item.label == "停用B站登录态").click().run(timeout=30)
    assert not app.exception and not any(settings.get_cookies().values())


def test_lan_has_no_cookie_form_even_with_stale_open_state(page, monkeypatch):
    settings.save_cookies(values())
    app = page()
    app.session_state["bilibili_settings_open"] = True
    monkeypatch.setenv("MD_NETWORK_MODE", "lan")
    monkeypatch.setenv("MD_LAN_ACCESS_CODE", "test-access-code-123")
    app.run(timeout=30)
    next(item for item in app.text_input if item.label == "访问码").set_value("test-access-code-123")
    next(item for item in app.button if item.label == "进入对话").click().run(timeout=30)
    assert not app.exception
    assert not any(item.label == "B站采集设置" for item in app.button)
    assert not any(item.label in settings.COOKIE_ENV for item in app.text_input)
    assert settings.get_cookies() == values()


def test_api_and_bilibili_settings_are_separate_and_always_navigable(page):
    app = page()
    next(item for item in app.button if item.label == "API 设置").click().run(timeout=30)
    assert not app.exception and any(item.label == "API Key" for item in app.text_input)
    next(item for item in app.button if item.label == "B站采集设置").click().run(timeout=30)
    assert not app.exception and not any(item.label == "API Key" for item in app.text_input)
    assert {item.label for item in app.text_input} == set(settings.COOKIE_ENV)
    next(item for item in app.button if item.label == "API 设置").click().run(timeout=30)
    assert not app.exception and {item.label for item in app.text_input}.isdisjoint(settings.COOKIE_ENV)
