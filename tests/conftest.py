"""Offline tests: fake credentials and a default ban on real HTTP requests."""
import os

os.environ.setdefault("DEEPSEEK_API_KEY", "test-placeholder-not-a-real-key")

import pytest
import requests


@pytest.fixture(autouse=True)
def reject_unmocked_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real HTTP is disabled in ordinary tests; use a mock")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
