"""One person, multiple sources, reusable history, safe exports; no real APIs."""

import io
import json
from pathlib import Path

import numpy as np
import pytest

from app.ingest.identity import parse_creator, parse_identity
from app.ingest.store import CreatorStore, BusyError, create_local, saved_creators
from app.ingest.documents import import_documents, prepare_documents
from app.ingest.persona import generate_portrait, portrait_metrics, chunks_for
from app.ingest.conversation import build_index, stream_creator_response, prepare_creator_context
from app.ingest.exports import export_profile, export_chat
from app.config import runtime


def files():
    return [("one.md", ("# 蓝鲸项目\n\n代号是蓝鲸，先验证需求，再做小工具。" * 15).encode("utf-8")),
            ("two.txt", ("先找用户了解问题，再确认是否愿意使用。" * 20).encode("utf-8")),
            ("three.markdown", ("# 使用边界\n\n只引用原文支持的事实，资料不足就明确说明。" * 15).encode("utf-8"))]


def approved_store():
    store = create_local("样例人物")
    import_documents(store, files(), True)
    profile = generate_portrait(store, consent=True, chat=lambda messages: iter(["先给结论，再讲依据。[样本1] "]))
    store.approve(profile["text"])
    return store


def test_local_identity_is_isolated_and_not_a_bilibili_fetch_identity():
    one, two = create_local("人物甲"), create_local("人物甲")
    assert one.path != two.path and one.identity.uid.startswith("local_")
    assert parse_identity(one.identity.uid).key == one.path.name
    with pytest.raises(ValueError):
        parse_creator(one.identity.uid)
    assert {row["uid"] for row in saved_creators()} == {one.identity.uid, two.identity.uid}


def test_merge_bilibili_and_documents_idempotently_without_mixing_people():
    store = CreatorStore(parse_creator("123"))
    record = {"id": "bili-1", "author_uid": "123", "author_name": "样例作者", "published_at": "2025-01-01",
              "url": "https://t.bilibili.com/100", "type": "dynamic", "text": "B站已有原文" * 30}
    store.save_collection({"name": "样例作者", "records": [record], "complete": False, "warning": "", "skipped": 0})
    result = import_documents(store, files(), True)
    first = store.read()
    assert result["accepted"] == 3 and len(first["records"]) == 4
    assert first["records"][0]["author_uid"] == "123"
    again = import_documents(store, files(), True)
    assert again["accepted"] == 0 and again["skipped"] == 3
    assert store.read()["corpus_digest"] == first["corpus_digest"]
    other = CreatorStore(parse_creator("456"))
    assert other.read()["records"] == []
    assert all(row.get("published_at") == "日期未知" for row in first["records"] if row["type"] == "document")


def test_import_requires_consent_and_rejects_duplicates_as_extra_evidence():
    store = create_local("本地人物")
    with pytest.raises(ValueError, match="确认"):
        import_documents(store, files(), False)
    assert store.read()["records"] == []
    text = ("同一份资料" * 80).encode("utf-8")
    result = import_documents(store, [("a.md", text), ("b.md", text), ("c.txt", text)], True)
    assert result["accepted"] == 1 and portrait_metrics(store.read())[0] == 1
    with pytest.raises(ValueError, match="资料不足"):
        generate_portrait(store, consent=True, chat=lambda messages: pytest.fail("Paid call"))


@pytest.mark.parametrize("name", ["../private.md", "C:\\private.md", "sub/private.md", "data.json", ".hidden.md", "README.md", "AGENTS.markdown", "x:stream.md"])
def test_unsafe_or_instruction_files_are_rejected_before_write(name):
    store = create_local("人物")
    before = (store.path / "snapshot.json").read_bytes()
    with pytest.raises(ValueError):
        import_documents(store, [(name, b"test text")], True)
    assert (store.path / "snapshot.json").read_bytes() == before


@pytest.mark.parametrize("raw", [b"", b"\xff", b"binary\x00text", b"x" * (2 * 1024 * 1024 + 1)],
                         ids=["empty", "invalid-encoding", "binary", "oversized"])
def test_invalid_or_large_documents_are_rejected(raw):
    with pytest.raises(ValueError):
        prepare_documents([("notes.md", raw)], parse_creator("123"), "test", True)


def test_a_bad_file_does_not_partially_import_the_batch():
    store = create_local("人物")
    with pytest.raises(ValueError):
        import_documents(store, [files()[0], ("bad.pdf", b"bad")], True)
    assert store.read()["records"] == []


def test_same_filename_updates_and_invalidates_profile_history_and_index():
    store = approved_store()
    data = store.read()
    messages = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
    store.save_messages(data, messages)
    assert store.load_messages(data) == messages
    changed = [("one.md", ("# 蓝鲸项目\n\n现在先验证新需求。" * 25).encode("utf-8"))]
    result = import_documents(store, changed, True)
    updated = store.read()
    assert result["updated"] == 1 and len(updated["records"]) == 3
    assert not store.profile_fresh(updated) and store.load_messages(updated) == []
    with pytest.raises(ValueError, match="变化"):
        store.save_messages(data, messages)
    with pytest.raises(ValueError, match="确认"):
        list(stream_creator_response(store, "新问题", [], chat=lambda messages: pytest.fail("Paid call")))


def test_history_is_persisted_bounded_clearable_and_not_in_snapshot():
    store = approved_store()
    data = store.read()
    before = (store.path / "snapshot.json").read_bytes()
    messages = [{"role": "user", "content": str(number)} for number in range(30)]
    store.save_messages(data, messages)
    assert store.load_messages(data) == messages[-20:]
    assert (store.path / "snapshot.json").read_bytes() == before
    with store.operation("busy"):
        with pytest.raises(BusyError):
            store.save_messages(data, [])
    store.save_messages(data, [])
    assert store.load_messages(data) == []
    (store.path / "conversation.json").write_text("not json", encoding="utf-8")
    assert store.load_messages(data) == []


def test_saved_list_tolerates_corrupt_or_unrelated_directories():
    from app.ingest import store as storage
    good = create_local("有效人物")
    bad = storage.DEFAULT_ROOT / "bilibili_999"
    bad.mkdir()
    (bad / "snapshot.json").write_text("broken", encoding="utf-8")
    assert [row["uid"] for row in saved_creators()] == [good.identity.uid]


def test_exports_do_not_bundle_corpus_configuration_or_secret():
    store = approved_store()
    runtime.save_config("test-export-secret", "https://example.invalid/chat", "test-model")
    data = store.read()
    data["profile"]["text"] += " test-export-secret"
    profile = export_profile(data)
    chat = export_chat(data, [{"role": "user", "content": "test-export-secret"}])
    assert "test-export-secret" not in profile + chat and "[凭据已隐藏]" in profile + chat
    assert files()[1][1].decode("utf-8") not in profile + chat
    assert "api-settings.json" not in profile + chat


def test_neighbors_are_bounded_to_same_document_and_heading():
    store = create_local("人物")
    content = "# 同章\n\n" + "。".join("段落" + str(index) + "甲" * 60 for index in range(25))
    import_documents(store, [("a.md", content.encode("utf-8")), ("b.md", b"# Same\n\nother source")], True)
    chunks = chunks_for(store.read())
    target = chunks[2]
    class Index:
        def search(self, *args):
            return [(1, target["text"])]
    context, sources, fallback = prepare_creator_context(store.read(), "段落", Index())
    assert not fallback and 1 < len(sources) <= 3 and len(context) <= 6000
    assert all(source["source_file"] == "a.md" and source["heading"] == target["heading"] for source in sources)


def test_local_person_full_web_flow_import_review_chat_reopen_and_clear(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    from app.ingest import ui, conversation
    from app.llm import client
    import streamlit as st
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
    class Upload(io.BytesIO):
        def __init__(self, name, raw):
            super().__init__(raw)
            self.name = name
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: [Upload(name, raw) for name, raw in files()])
    monkeypatch.setattr(ui, "collect_dynamics", lambda *args, **kwargs: pytest.fail("B站 request for local person"))
    class Encoder:
        def embed(self, texts, **kwargs):
            return iter([np.array([1, 2, 3], dtype=np.float32) for text in texts])
        def query_embed(self, texts):
            return self.embed(texts)
    monkeypatch.setattr(conversation, "get_local_model", lambda name: Encoder())
    calls = []
    def model(messages):
        calls.append(messages)
        yield "先给结论。[样本1]" if "画像草稿" in messages[0]["content"] else "先验证需求。[资料1]"
    monkeypatch.setattr(client, "stream_chat", model)
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "chat_app.py")).run(timeout=30)
    next(item for item in app.text_input if item.label == "本地人物名称").set_value("本地样例")
    next(item for item in app.button if item.label == "创建本地人物").click().run(timeout=30)
    assert not app.exception and app.session_state["creator_uid"].startswith("local_")
    uid = app.session_state["creator_uid"]
    assert not any(item.label == "采集 / 更新本人文字" for item in app.button)
    next(item for item in app.checkbox if "属于当前人物" in item.label).check().run(timeout=30)
    next(item for item in app.button if item.label == "导入到当前人物").click().run(timeout=30)
    assert not app.exception and len(calls) == 0
    next(item for item in app.checkbox if "12,000字" in item.label).check().run(timeout=30)
    next(item for item in app.button if item.label == "生成人物画像草稿").click().run(timeout=30)
    next(item for item in app.checkbox if "我已核对" in item.label).check().run(timeout=30)
    next(item for item in app.button if item.label == "确认画像，准备知识库").click().run(timeout=30)
    assert not app.exception
    next(item for item in app.checkbox if "同意提问" in item.label).check().run(timeout=30)
    app.chat_input[0].set_value("蓝鲸项目先做什么？").run(timeout=30)
    assert not app.exception and len(calls) == 2
    assert "本地原文" in calls[-1][0]["content"] and "不是本人" in calls[-1][0]["content"]
    saved = app.session_state["creator_messages"]
    assert len(app.get("download_button")) >= 2
    store = CreatorStore(parse_identity(uid))
    assert store.load_messages(store.read()) == saved and len(saved) == 2
    reopened = AppTest.from_file(str(root / "chat_app.py")).run(timeout=30)
    next(item for item in reopened.button if item.label == "打开已有人物").click().run(timeout=30)
    assert not reopened.exception and reopened.session_state["creator_messages"] == saved
    assert len(calls) == 2 and len(reopened.get("download_button")) >= 2
    next(item for item in reopened.button if item.label == "清空人物对话").click().run(timeout=30)
    assert not reopened.exception and store.load_messages(store.read()) == []
