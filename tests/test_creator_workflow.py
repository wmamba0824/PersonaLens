"""Creator pipeline contracts with fake Bilibili/model/encoder, no network."""

import json
from pathlib import Path

import numpy as np
import pytest
import requests

from app.ingest.identity import parse_creator
from app.ingest.bilibili import collect_dynamics, CollectionError
from app.ingest.store import CreatorStore, BusyError
from app.ingest.persona import generate_portrait
from app.ingest.conversation import build_index, stream_creator_response


def dynamic(identifier="100", uid="123", text=None):
    return {"id_str": identifier, "type": "DYNAMIC_TYPE_WORD", "modules": {
        "module_author": {"mid": int(uid), "name": "星河作者", "pub_ts": 1735689600},
        "module_dynamic": {"desc": {"text": text or "学习要先提问再验证。星河项目代号是蓝鲸。" * 15}}}}


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload, self.closed = payload, False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class Session:
    def __init__(self, payloads):
        self.payloads, self.calls, self.responses = list(payloads), [], []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        response = Response(item)
        self.responses.append(response)
        return response


def payload(items, more=False, offset=""):
    return {"code": 0, "data": {"items": items, "has_more": more, "offset": offset}}


def populated(tmp_path, uid="123"):
    store = CreatorStore(parse_creator(uid), tmp_path)
    result = collect_dynamics(store.identity, session=Session([payload([
        dynamic(str(index), uid=uid) for index in range(100, 103)])]))
    with store.operation("collect"):
        store.save_collection(result)
    return store


@pytest.mark.parametrize("value", ["123", "https://space.bilibili.com/123", "space.bilibili.com/123/",
                                   "https://space.bilibili.com/123/dynamic?foo=bar"])
def test_identity_canonicalization(value):
    assert parse_creator(value).key == "bilibili_123"
    assert parse_creator(value).url == "https://space.bilibili.com/123"


@pytest.mark.parametrize("value", ["0", "-1", "../../../secret", "https://127.0.0.1/123",
    "https://space.bilibili.com.evil.example/123", "https://evil@space.bilibili.com/123",
    "https://space.bilibili.com:443/123", "https://b23.tv/example", "https://www.bilibili.com/video/BV1",
    "https://space.bilibili.com/123/../../x", "１２３"])
def test_identity_rejects_non_creator_urls(value):
    with pytest.raises(ValueError):
        parse_creator(value)


def test_collection_filters_other_authors_forwards_paid_metadata_and_duplicates():
    forward = dynamic("102")
    forward["type"] = "DYNAMIC_TYPE_FORWARD"
    paid = dynamic("103")
    paid["modules"]["module_dynamic"]["is_charge"] = True
    video = dynamic("104", text=" ")
    video["type"] = "DYNAMIC_TYPE_AV"
    video["modules"]["module_dynamic"]["major"] = {"type": "MAJOR_TYPE_ARCHIVE", "archive": {"title": "不是本人说过的句子"}}
    session = Session([payload([dynamic(), dynamic(), dynamic("101", uid="456"), forward, paid, video])])
    result = collect_dynamics(parse_creator("123"), session=session)
    assert len(result["records"]) == 1 and result["skipped"] == 4
    record = result["records"][0]
    assert record["author_uid"] == "123" and record["url"] == "https://t.bilibili.com/100"
    assert record["published_at"].startswith("2025-")
    assert all(response.closed for response in session.responses)
    assert session.calls[0][1]["allow_redirects"] is False


def test_partial_failure_and_first_failure_are_not_empty_success():
    with pytest.raises(CollectionError) as error:
        collect_dynamics(parse_creator("123"), session=Session([{"code": -352, "message": "private provider details"}]))
    assert "private provider details" not in str(error.value)
    session = Session([payload([dynamic()], True, "next"), requests.Timeout("secret cookie")])
    result = collect_dynamics(parse_creator("123"), session=session)
    assert len(result["records"]) == 1 and not result["complete"]
    assert "部分" in result["warning"] and "secret cookie" not in str(result)
    assert len(session.calls) == 2


def test_collection_request_and_item_bounds():
    session = Session([payload([dynamic(str(index)) for index in range(10, 30)], True, "next")])
    result = collect_dynamics(parse_creator("123"), max_items=5, session=session)
    assert len(result["records"]) == 5 and len(session.calls) == 1 and not result["complete"]
    with pytest.raises(ValueError):
        collect_dynamics(parse_creator("123"), max_items=101, session=session)


def test_store_isolation_lock_and_atomic_snapshot(tmp_path):
    one, two = populated(tmp_path), CreatorStore(parse_creator("456"), tmp_path)
    assert len(one.read()["records"]) == 3 and two.read()["records"] == []
    assert one.path != two.path
    with one.operation("test"):
        with pytest.raises(BusyError):
            with one.operation("duplicate"):
                pytest.fail("Duplicate job started")
    assert not (one.path / "operation.lock").exists()
    assert one.read()["job"]["status"] == "done"
    with pytest.raises(ValueError):
        two.save_collection({"records": one.read()["records"]})
    assert not list(one.path.glob("*.tmp"))


def test_failed_portrait_preserves_data_and_does_not_advance_profile(tmp_path):
    store = populated(tmp_path)
    with pytest.raises(ValueError, match="同意"):
        generate_portrait(store)
    def failed(messages):
        raise RuntimeError("private provider error")
    with pytest.raises(RuntimeError):
        generate_portrait(store, consent=True, chat=failed)
    data = store.read()
    assert len(data["records"]) == 3 and not data["profile"] and data["job"]["status"] == "failed"
    assert "private provider error" not in json.dumps(data)


class Encoder:
    def embed(self, texts, **kwargs):
        return iter([np.array([1, 2, 3], dtype=np.float32) for _ in texts])

    def query_embed(self, texts):
        return self.embed(texts)


def test_complete_pipeline_reuses_portrait_and_index_and_scopes_prompt(tmp_path):
    store = populated(tmp_path)
    calls = []
    def portrait(messages):
        calls.append(messages)
        yield "## 表达方式\n先提问再验证，短句解释。[样本1]"
    profile = generate_portrait(store, consent=True, chat=portrait)
    assert profile["sample_chars"] <= 12000 and not profile["approved"]
    with pytest.raises(ValueError, match="确认"):
        list(stream_creator_response(store, "星河项目", [], chat=portrait))
    generate_portrait(store, consent=True, chat=portrait)
    assert len(calls) == 1
    store.approve(profile["text"])
    index, message = build_index(store, store.read(), model_factory=lambda name: Encoder())
    assert index is not None and "向量就绪" in message
    reused, _ = build_index(store, store.read(), model_factory=lambda name: Encoder())
    assert reused.stats["generated"] == 0
    seen = []
    def answer(messages):
        seen.extend(messages)
        yield "先验证：代号是蓝鲸。[资料1]"
    chunks = list(stream_creator_response(store, "星河项目代号", [], index=reused, chat=answer))
    assert chunks[-1]["sources"] and not chunks[-1]["lexical"]
    assert "先提问再验证" in seen[0]["content"] and "https://t.bilibili.com/100" in seen[0]["content"]
    other = populated(tmp_path, "456")
    p = generate_portrait(other, consent=True, chat=portrait)
    other.approve(p["text"])
    other_prompt = []
    def other_answer(messages):
        other_prompt.extend(messages)
        yield "回答。[资料1]"
    list(stream_creator_response(other, "星河", [], chat=other_answer))
    assert "账号456" in other_prompt[0]["content"] and "账号123" not in other_prompt[0]["content"]


def test_changed_knowledge_invalidates_portrait_and_fallback_is_explicit(tmp_path):
    store = populated(tmp_path)
    profile = generate_portrait(store, consent=True, chat=lambda messages: iter(["短句。[样本1]"]))
    store.approve(profile["text"])
    def failing_model(name):
        raise RuntimeError("provider secret")
    index, status = build_index(store, store.read(), model_factory=failing_model)
    assert index is None and "降级" in status and "provider secret" not in status
    result = collect_dynamics(store.identity, session=Session([payload([dynamic("104", text="新增本人观点，新的资料内容不同。" * 20)])]))
    with store.operation("collect"):
        store.save_collection(result)
    assert not store.profile_fresh()
    with pytest.raises(ValueError):
        list(stream_creator_response(store, "新问题", [], chat=lambda messages: pytest.fail("Paid call")))


def test_profile_rejects_invalid_evidence_and_too_little_data(tmp_path):
    store = populated(tmp_path)
    with pytest.raises(ValueError, match="样本编号"):
        generate_portrait(store, consent=True, chat=lambda messages: iter(["捏造证据。[样本999] "]))
    small = CreatorStore(parse_creator("789"), tmp_path)
    result = collect_dynamics(small.identity, session=Session([payload([dynamic(uid="789")])]))
    with small.operation("collect"):
        small.save_collection(result)
    with pytest.raises(ValueError, match="资料不足"):
        generate_portrait(small, consent=True, chat=lambda messages: pytest.fail("Paid call"))


def test_item_limit_on_last_page_is_not_full_history():
    result = collect_dynamics(parse_creator("123"), max_items=1,
                             session=Session([payload([dynamic("100"), dynamic("101")])]))
    assert not result["complete"] and "条数上限" in result["warning"]


def test_stale_approval_is_rejected(tmp_path):
    store = populated(tmp_path)
    profile = generate_portrait(store, consent=True, chat=lambda messages: iter(["短句。[样本1] "]))
    with pytest.raises(ValueError, match="刷新"):
        store.approve(profile["text"], expected_digest="stale", expected_revision=profile["revision"])
    assert store.read()["profile"]["approved"] is False


def test_paid_teaser_is_excluded():
    from app.ingest.bilibili import normalize_dynamic
    item = dynamic()
    item["modules"]["module_dynamic"]["additional"] = {"type": "ADDITIONAL_TYPE_UPPOWER_COMMON"}
    assert normalize_dynamic(item, "123") is None


def test_web_creation_review_chat_switch_and_refresh_without_real_api(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    from app.ingest import store as storage, ui, conversation
    from app.llm import client
    from app.ui import knowledge_chat
    monkeypatch.setenv("MD_NETWORK_MODE", "local")
    monkeypatch.setattr(storage, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(knowledge_chat, "render_knowledge_chat", lambda: None)
    monkeypatch.setattr(conversation, "get_local_model", lambda name: Encoder())
    acquisitions, model_calls = [], []
    def acquire(identity, **kwargs):
        acquisitions.append(identity.uid)
        return collect_dynamics(identity, session=Session([payload([
            dynamic(str(number), uid=identity.uid) for number in range(100, 103)])]))
    def model(messages):
        model_calls.append(messages)
        yield "先提问再验证。[样本1]" if "画像草稿" in messages[0]["content"] else "代号是蓝鲸。[资料1]"
    monkeypatch.setattr(ui, "collect_dynamics", acquire)
    monkeypatch.setattr(client, "stream_chat", model)
    original_run = LocalScriptRunner.run
    def joined_run(runner, *args, **kwargs):
        try:
            return original_run(runner, *args, **kwargs)
        finally:
            if runner._script_thread is not None:
                runner._script_thread.join(timeout=3)
    monkeypatch.setattr(LocalScriptRunner, "run", joined_run)
    root = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(root / "chat_app.py")).run(timeout=20)
    assert not at.exception
    at.sidebar.radio[0].set_value("人物助手").run(timeout=20)
    assert not at.exception and not acquisitions and not model_calls
    def button(label):
        return next(item for item in at.button if item.label == label)
    def checkbox(fragment):
        return next(item for item in at.checkbox if fragment in item.label)
    at.text_input[0].set_value("https://space.bilibili.com/123")
    button("打开 / 创建人物").click().run(timeout=20)
    assert button("采集 / 更新本人文字").disabled
    checkbox("我有权").check().run(timeout=20)
    button("采集 / 更新本人文字").click().run(timeout=20)
    assert not at.exception and acquisitions == ["123"] and not model_calls
    assert button("生成人物画像草稿").disabled
    checkbox("12,000字").check().run(timeout=20)
    button("生成人物画像草稿").click().run(timeout=20)
    assert not at.exception and len(model_calls) == 1
    assert button("生成人物画像草稿").disabled
    assert button("确认画像，准备知识库").disabled
    at.run(timeout=20)
    assert len(model_calls) == 1 and len(acquisitions) == 1
    checkbox("我已核对").check().run(timeout=20)
    button("确认画像，准备知识库").click().run(timeout=20)
    assert not at.exception and at.chat_input[0].disabled
    checkbox("同意提问").check().run(timeout=20)
    at.chat_input[0].set_value("星河项目代号是什么").run(timeout=20)
    assert not at.exception and len(model_calls) == 2
    assert at.session_state["creator_messages"][1]["content"] == "代号是蓝鲸。[资料1]"
    at.text_input[0].set_value("456")
    button("打开 / 创建人物").click().run(timeout=20)
    assert not at.exception and at.session_state["creator_messages"] == []
    assert button("采集 / 更新本人文字").disabled
    assert not CreatorStore(parse_creator("456"), tmp_path).read()["records"]
    assert len(model_calls) == 2 and len(acquisitions) == 1
