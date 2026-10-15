"""Local retrieval and opt-in style contracts; fake encoder, no paid API."""

import json

import numpy as np
import pytest

from app.rag import local_index, search, loader
from app.persona import style


class FakeEncoder:
    def __init__(self):
        self.embedded = []

    def embed(self, texts, **kwargs):
        self.embedded.extend(texts)
        for text in texts:
            yield np.array([len(text), sum(map(ord, text)) % 97 + 1, 1], dtype=np.float32)

    def query_embed(self, query):
        assert isinstance(query, list) and len(query) == 1
        return self.embed(query)


def test_incremental_index_add_change_delete_reorder_and_duplicates(tmp_path):
    model = FakeEncoder()
    factory = lambda name: model
    path = tmp_path / "cache.npz"
    first = local_index.LocalIndex(["甲", "乙", "甲"], path, model_factory=factory)
    assert first.stats == {"generated": 2, "reused": 0, "total": 3}
    reused = local_index.LocalIndex(["乙", "甲", "甲"], path, model_factory=factory)
    assert reused.stats["generated"] == 0
    np.testing.assert_allclose(first.matrix[[1, 0, 2]], reused.matrix, rtol=1e-6)
    added = local_index.LocalIndex(["甲", "乙", "丙"], path, model_factory=factory)
    assert added.stats["generated"] == 1
    changed = local_index.LocalIndex(["甲", "丁", "丙"], path, model_factory=factory)
    assert changed.stats["generated"] == 1 and changed.stats["reused"] == 2
    removed = local_index.LocalIndex(["丙"], path, model_factory=factory)
    assert removed.stats["generated"] == 0
    with np.load(path, allow_pickle=False) as data:
        assert data["hashes"].tolist() == [local_index.content_hash("丙")]
    assert model.embedded == ["甲", "乙", "丙", "丁"]


def test_model_change_and_corrupt_cache_rebuild(tmp_path):
    model = FakeEncoder()
    path = tmp_path / "cache.npz"
    local_index.LocalIndex(["资料"], path, model_name="model-a", model_factory=lambda name: model)
    changed = local_index.LocalIndex(["资料"], path, model_name="model-b", model_factory=lambda name: model)
    assert changed.stats["generated"] == 1
    path.write_bytes(b"PK broken npz")
    rebuilt = local_index.LocalIndex(["资料"], path, model_name="model-b", model_factory=lambda name: model)
    assert rebuilt.stats["generated"] == 1


def test_same_count_mismatch_and_empty_inputs(tmp_path):
    model = FakeEncoder()
    index = local_index.LocalIndex(["甲", "乙"], tmp_path / "cache.npz", model_factory=lambda name: model)
    with pytest.raises(ValueError, match="Knowledge changed"):
        index.search("甲", ["乙", "甲"], 1)
    assert index.search("", ["甲", "乙"], 1) == []
    assert index.search("甲", ["甲", "乙"], 0) == []
    empty = local_index.LocalIndex([], tmp_path / "empty.npz", model_factory=lambda name: pytest.fail("download"))
    assert empty.matrix is None


def test_failed_atomic_write_preserves_old_cache(tmp_path, monkeypatch):
    path = tmp_path / "cache.npz"
    factory = lambda name: FakeEncoder()
    local_index.LocalIndex(["旧资料"], path, model_factory=factory)
    previous = path.read_bytes()
    def fail(*args):
        raise OSError("simulated disk error")
    monkeypatch.setattr(local_index.os, "replace", fail)
    with pytest.raises(OSError):
        local_index.LocalIndex(["新资料"], path, model_factory=factory)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_local_search_never_calls_cloud_and_failure_is_visible(tmp_path, monkeypatch):
    entries = ["项目代号蓝鲸"]
    monkeypatch.setattr(search, "RETRIEVAL_BACKEND", "local")
    monkeypatch.setattr(loader, "load_entries", lambda: entries)
    index = local_index.LocalIndex(entries, tmp_path / "cache.npz", model_factory=lambda name: FakeEncoder())
    monkeypatch.setattr(local_index, "load_local_index", lambda entries: index)
    monkeypatch.setattr(search, "_LOCAL_INDEX", None)
    monkeypatch.setattr(search, "_EMB_MAT", None)
    monkeypatch.setattr(search, "_SEARCH_STATUS", {})
    monkeypatch.setattr(search, "encode_query", lambda query: pytest.fail("Cloud embeddings called"))
    assert search.init_search()["mode"] == "local_ready"
    assert search.search_entries("蓝鲸", entries)
    def fail(*args):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(index, "search", fail)
    assert search.search_entries("蓝鲸", entries)[0][1] == entries[0]
    assert search.get_search_status()["mode"] == "lexical_local_error"
    monkeypatch.setattr(local_index, "load_local_index", fail)
    assert search.init_search()["mode"] == "lexical_local_error"


def test_style_is_bounded_opt_in_editable_and_stale_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(style, "profile_path", lambda: tmp_path / "style.json")
    monkeypatch.setattr(style, "source_label", lambda entry: "TXT · sample.txt")
    entries = [str(i) + "文本" * 400 for i in range(100)]
    sample = style.profile_sample(entries)
    assert len(sample) <= 12000 and "99文本" in sample and "[样本24]" in sample
    assert style.approved_style(entries) == ""
    calls = []
    def fake_stream(messages):
        calls.append(messages)
        yield "# 待确认草稿\n简短、清楚。"
    monkeypatch.setattr(style, "stream_chat", fake_stream)
    draft = style.generate_style(entries)
    assert len(calls) == 1 and len(calls[0][1]["content"]) <= 12000
    assert not (tmp_path / "style.json").exists()
    style.save_style(entries, draft)
    assert style.approved_style(entries) == ""
    style.save_style(entries, "用户编辑后的风格", enabled=True)
    assert "用户编辑后的风格" in style.approved_style(entries)
    assert style.approved_style(entries[::-1]) == ""
    assert style.read_style(entries[::-1])["stale"]
    style.save_style(entries, draft, enabled=False)
    assert style.approved_style(entries) == ""


def test_invalid_style_cache_and_empty_generation(tmp_path, monkeypatch):
    path = tmp_path / "style.json"
    monkeypatch.setattr(style, "profile_path", lambda: path)
    for data in ([1], {"schema": 1, "text": 2}, {"schema": 1, "text": "x" * 6001}):
        path.write_text(json.dumps(data), encoding="utf-8")
        assert style.read_style(["资料"]) == {}
    with pytest.raises(ValueError):
        style.generate_style([])
    with pytest.raises(ValueError):
        style.save_style(["资料"], " ")
