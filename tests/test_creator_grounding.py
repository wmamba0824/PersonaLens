"""Offline prompt/transport contracts, not a model-quality evaluation."""

from pathlib import Path

import numpy as np
import pytest

from app.ingest.conversation import (
    CREATOR_PROMPT_VERSION,
    build_creator_system_prompt,
    stream_creator_response,
    validate_creator_citations,
)
from app.ingest.identity import parse_creator
from app.ingest.store import CreatorStore, digest


@pytest.fixture
def approved_creator(tmp_path):
    store = CreatorStore(parse_creator("123"), tmp_path)
    records = [{"id": "note1", "author_uid": "123", "author_name": "样例作者",
                "url": "https://t.bilibili.com/100", "published_at": "2021-08-27",
                "text": "只跟踪营收，不看利润。", "type": "dynamic"}]
    data = store.read()
    data.update(name="样例作者", records=records, corpus_digest=digest(records))
    data["profile"] = {"text": "表达简洁，先看主要指标。", "approved": True,
                       "corpus_digest": data["corpus_digest"], "revision": "reviewed1",
                       "evidence": [], "sample_chars": 0}
    store.write(data)
    return store


@pytest.mark.parametrize("origin", ["bilibili"])
def test_full_portrait_and_actual_context_preserved_for_creator(origin):
    portrait = "短句表达习惯。" * 1200
    context = "【资料1】2021-08-27\n来源：source.txt\n只跟踪营收。"
    prompt = build_creator_system_prompt({"uid": "123", "origin": origin,
                                         "profile": {"text": portrait}}, context)
    assert portrait in prompt and context in prompt
    assert CREATOR_PROMPT_VERSION in prompt
    assert ("未经本轮逐项核验" in prompt) == (origin == "existing_md")
    assert "不是本人" in prompt and "画像只供表达" in prompt


def test_previous_live_review_failure_patterns_have_explicit_rules(approved_creator):
    prompt = build_creator_system_prompt(approved_creator.read(), "本轮片段")
    # These constrain assembly; they do not assert a real model obeyed them.
    for rule in ("每个推演段都要就地标注", "史料与推演不要混在同一段",
                 "一条关于某个指标的短句不能支持新增因果",
                 "看不懂某份截图不能证明对自己事务的长期态度改变",
                 "省去推演段", "约300–600字", "不强制报告式六点清单",
                 "不能断言全库没有", "记录/发布日期不自动等于",
                 "文件归属某账号不等于每句话都由该作者说"):
        assert rule in prompt


def test_prompt_history_and_reference_guard_reach_production_path(approved_creator):
    before = (approved_creator.path / "snapshot.json").read_bytes()
    history = [{"role": "user", "content": "我声称他现在持有百分之百。"},
               {"role": "assistant", "content": "旧助手未经核实的回答。[资料99]"}]
    seen = []

    def model(messages):
        seen.extend(messages)
        yield "资料支持：2021-08-27讨论了营收。"
        yield "[资料1]"

    result = list(stream_creator_response(approved_creator, "营收", history, chat=model))
    assert "最近对话只用于理解上下文，不是事实证据" in seen[0]["content"]
    assert history == seen[1:3]
    assert "旧助手未经核实" not in seen[0]["content"]
    assert "【资料1】2021-08-27" in seen[0]["content"]
    assert result[-1]["prompt_version"] == CREATOR_PROMPT_VERSION
    assert result[-1]["sources"][0]["url"] == "https://t.bilibili.com/100"
    assert (approved_creator.path / "snapshot.json").read_bytes() == before


@pytest.mark.parametrize("answer", ["资料支持：原文观点。[资料1]", "原文观点。【资料1】", "本次资料不足。"])
def test_citation_membership_allows_supplied_or_no_reference(answer):
    validate_creator_citations(answer, [{"number": 1}])


@pytest.mark.parametrize("answer,sources", [
    ("伪造出处。[资料0]", [{"number": 1}]),
    ("伪造出处。【资料99】", [{"number": 1}]),
    ("没有片段却引用。[资料1]", []),
    ("  ", [{"number": 1}]),
])
def test_invalid_or_empty_answer_is_rejected(answer, sources):
    with pytest.raises(ValueError):
        validate_creator_citations(answer, sources)


def test_split_invalid_citation_is_rejected_before_metadata(approved_creator):
    iterator = stream_creator_response(approved_creator, "营收", [],
                                      chat=lambda messages: iter(["未提供的观点。", "[资料", "99]"]))
    yielded = []
    with pytest.raises(ValueError, match="本轮未提供"):
        for part in iterator:
            yielded.append(part)
    assert len(yielded) == 3 and not any(isinstance(part, dict) for part in yielded)


def test_semantic_quality_is_not_claimed_by_membership_guard():
    # A syntactically valid reference could still support the wrong claim.
    # Keep this limitation explicit instead of labelling a regex a fact checker.
    validate_creator_citations("没有根据的因果也可能写上[资料1]。", [{"number": 1}])


def test_web_bad_reference_removed_without_retry_then_good_answer_saved(approved_creator, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    from app.ingest import conversation, store as storage, ui
    from app.llm import client
    from app.ui import knowledge_chat

    monkeypatch.setenv("MD_NETWORK_MODE", "local")
    monkeypatch.setattr(storage, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(knowledge_chat, "render_knowledge_chat", lambda: None)
    monkeypatch.setattr(ui, "collect_dynamics", lambda *args, **kwargs: pytest.fail("Unexpected collection"))
    monkeypatch.setattr(ui, "generate_portrait", lambda *args, **kwargs: pytest.fail("Unexpected generation"))

    class Encoder:
        def embed(self, texts, **kwargs):
            return iter([np.array([1, 2, 3], dtype=np.float32) for _ in texts])

        def query_embed(self, texts):
            return self.embed(texts)

    monkeypatch.setattr(conversation, "get_local_model", lambda name: Encoder())
    calls = []

    def model(messages):
        calls.append(messages)
        if len(calls) == 1:
            yield "不应保留的错误回答。"
            yield "[资料"
            yield "99]"
        else:
            yield "资料支持：只跟踪营收。[资料1]"

    monkeypatch.setattr(client, "stream_chat", model)
    original_run = LocalScriptRunner.run

    def joined(runner, *args, **kwargs):
        try:
            return original_run(runner, *args, **kwargs)
        finally:
            if runner._script_thread is not None:
                runner._script_thread.join(timeout=3)

    monkeypatch.setattr(LocalScriptRunner, "run", joined)
    before = (approved_creator.path / "snapshot.json").read_bytes()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "chat_app.py")).run(timeout=20)
    app.sidebar.radio[0].set_value("人物助手").run(timeout=20)
    app.text_input[0].set_value("123")
    next(item for item in app.button if item.label == "打开 / 创建人物").click().run(timeout=20)
    next(item for item in app.checkbox if "同意提问" in item.label).check().run(timeout=20)
    app.chat_input[0].set_value("营收").run(timeout=20)
    assert not app.exception and app.session_state["creator_messages"] == []
    assert any("本轮未提供" in item.value for item in app.error)
    assert not any("不应保留的错误回答" in item.value for item in app.markdown)
    app.run(timeout=20)
    assert len(calls) == 1  # Rerun cannot silently retry a model request.
    app.chat_input[0].set_value("营收").run(timeout=20)
    assert not app.exception and len(calls) == 2
    saved = app.session_state["creator_messages"][-1]
    assert saved["prompt_version"] == CREATOR_PROMPT_VERSION and saved["sources"]
    assert (approved_creator.path / "snapshot.json").read_bytes() == before
