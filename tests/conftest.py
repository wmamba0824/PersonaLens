"""Offline tests: fake credentials and a default ban on real HTTP requests."""
import os

os.environ.setdefault("DEEPSEEK_API_KEY", "test-placeholder-not-a-real-key")

import pytest
import requests


@pytest.fixture(autouse=True)
def isolate_private_runtime(tmp_path, monkeypatch):
    from app.config import runtime
    from app.config import bilibili
    from app.ingest import store
    monkeypatch.setattr(runtime, "CONFIG_FILE", tmp_path / "runtime" / "api-settings.json")
    monkeypatch.setattr(bilibili, "COOKIE_FILE", tmp_path / "runtime" / "bilibili-settings.json")
    for name in bilibili.COOKIE_ENV.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path / "creators")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder-not-a-real-key")


@pytest.fixture(autouse=True)
def reject_unmocked_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real HTTP is disabled in ordinary tests; use a mock")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
