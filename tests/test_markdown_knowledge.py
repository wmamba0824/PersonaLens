"""Markdown knowledge contracts, using synthetic files and offline models only."""

from pathlib import Path
import subprocess

import numpy as np
import pytest
import streamlit as st

from app.rag import loader, local_index, search
from app.persona import style

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    root.mkdir()
    monkeypatch.setattr(loader, "DATA_FILE", root)
    st.cache_resource.clear()
    yield root
    st.cache_resource.clear()


def test_mixed_files_keep_markdown_text_sources_and_chunk_limits(knowledge):
    (knowledge / "nested").mkdir()
    content = "# 蓝鲸计划\n\n- 先验证需求\n- 再开发\n\n```python\nraise RuntimeError('text only')\n```\n\n[来源](https://example.invalid/no-fetch)"
    (knowledge / "notes.MD").write_text(content, encoding="utf-8")
    (knowledge / "nested/long.MarkDown").write_text("长文" * 401, encoding="utf-8")
    (knowledge / "plain.TXT").write_text("TXT已有能力保留", encoding="utf-8")
    (knowledge / "not-supported.pdf").write_bytes(b"not a document")
    (knowledge / "empty.md").write_text(" \n\n", encoding="utf-8")

    entries, origins = loader.read_entries(include_sources=True)
    assert len(loader.knowledge_files()) == 4
    assert all(0 < len(entry) <= 400 for entry in entries)
    assert "# 蓝鲸计划\n- 先验证需求\n- 再开发" in entries
    assert "# 蓝鲸计划\n```python\nraise RuntimeError('text only')\n```" in entries
    assert "# 蓝鲸计划\n[来源](https://example.invalid/no-fetch)" in entries
    assert origins["# 蓝鲸计划\n- 先验证需求\n- 再开发"] == {"Markdown · notes.MD"}
    assert origins["TXT已有能力保留"] == {"TXT · plain.TXT"}
    assert "Markdown · nested/long.MarkDown" in loader.source_label("长文" * 200)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_markdown_accepts_common_text_encodings(knowledge, encoding):
    (knowledge / "笔记.md").write_bytes("# 中文标题\n\n有效资料".encode(encoding))
    assert loader.read_entries() == ["# 中文标题\n有效资料"]


def test_invalid_text_has_actionable_error(knowledge):
    (knowledge / "bad.md").write_bytes(b"\xff")
    with pytest.raises(ValueError, match="save it as UTF-8"):
        loader.read_entries()


def test_instructions_and_hidden_paths_are_not_knowledge(knowledge):
    names = ["README.md", "README.zh-CN.MD", "nested/readme.markdown", "nested/AgEnTs.md",
             "SKILL.markdown", ".hidden.md", ".private/notes.md", "nested/.secret.txt"]
    for name in names:
        path = knowledge / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("instruction, not knowledge", encoding="utf-8")
    (knowledge / "nested/real.md").write_text("真正的资料", encoding="utf-8")
    assert [path.relative_to(knowledge).as_posix() for path in loader.knowledge_files()] == ["nested/real.md"]
    assert loader.read_entries() == ["真正的资料"]


@pytest.mark.parametrize("directory", [False, True])
def test_discovery_does_not_follow_symlinks_outside_root(knowledge, tmp_path, directory):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "secret.md"
    source.write_text("not knowledge", encoding="utf-8")
    link = knowledge / ("linked" if directory else "linked.md")
    try:
        link.symlink_to(outside if directory else source, target_is_directory=directory)
    except OSError:
        pytest.skip("Creating symlinks is not allowed on this Windows setup")
    assert loader.knowledge_files() == []


def test_single_configured_markdown_file_uses_same_decoder(tmp_path, monkeypatch):
    path = tmp_path / "single.markdown"
    path.write_bytes("# 单文件\n\n资料".encode("utf-8-sig"))
    monkeypatch.setattr(loader, "DATA_FILE", path)
    monkeypatch.setattr(loader, "XUEQIU_DIR", tmp_path / "absent-secondary")
    monkeypatch.setattr(loader, "TRANSCRIPT_DIR", tmp_path / "absent-transcripts")
    assert loader.knowledge_files() == [path]
    assert loader.read_entries() == ["# 单文件\n资料"]
    assert loader.read_entries(include_sources=True)[1]["# 单文件\n资料"] == {"Markdown · single.markdown"}


def test_discovery_refuses_resolved_outside_path_without_symlink_privileges(knowledge, tmp_path, monkeypatch):
    path = knowledge / "linked.md"
    path.write_text("must not be loaded", encoding="utf-8")
    original = type(path).resolve
    def resolved(candidate, *args, **kwargs):
        if candidate == path:
            return tmp_path / "outside.md"
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(type(path), "resolve", resolved)
    assert loader.knowledge_files() == [] and loader.read_entries() == []


def test_same_text_retains_both_txt_and_markdown_origins(knowledge):
    for name in ("a.txt", "b.md"):
        (knowledge / name).write_text("共同资料", encoding="utf-8")
    entries, origins = loader.read_entries(include_sources=True)
    assert entries == ["共同资料", "共同资料"]
    assert origins["共同资料"] == {"TXT · a.txt", "Markdown · b.md"}


def test_markdown_changes_reuse_local_vectors_and_invalidate_approved_style(knowledge, tmp_path, monkeypatch):
    class Encoder:
        def embed(self, texts, **kwargs):
            for text in texts:
                yield np.array([len(text), sum(map(ord, text)) % 97 + 1, 1], dtype=np.float32)

        def query_embed(self, texts):
            return self.embed(texts)

    monkeypatch.setattr(style, "profile_path", lambda: tmp_path / "style.json")
    path = knowledge / "notes.md"
    path.write_text("保持不变的介绍\n\n# 蓝鲸\n\n先验证需求", encoding="utf-8")
    entries = loader.read_entries()
    index_path = tmp_path / "index.npz"
    factory = lambda name: Encoder()
    first = local_index.LocalIndex(entries, index_path, model_factory=factory)
    assert first.stats["generated"] == 2
    calls = []
    def fake_style(messages):
        calls.append(messages)
        yield "# 待确认草稿\n先讲结论，再给依据。[样本1]"
    monkeypatch.setattr(style, "stream_chat", fake_style)
    draft = style.generate_style(entries)
    assert len(calls) == 1 and "Markdown · notes.md" in calls[0][1]["content"]
    assert style.approved_style(entries) == ""
    style.save_style(entries, draft, enabled=True)
    assert draft in style.approved_style(entries)

    path.write_text("保持不变的介绍\n\n# 蓝鲸\n\n先找三个用户验证需求", encoding="utf-8")
    updated = loader.read_entries()
    second = local_index.LocalIndex(updated, index_path, model_factory=factory)
    assert second.stats["generated"] == 1 and second.stats["reused"] == 1
    assert second.search("需求", updated, 2)
    assert style.read_style(updated)["stale"] and style.approved_style(updated) == ""
    path.unlink()
    assert loader.read_entries() == [] and style.approved_style([]) == ""


def test_page_chat_reload_add_edit_delete_markdown_without_real_api(knowledge, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    from app.chat import service
    from app.ui import knowledge_chat

    monkeypatch.setenv("MD_NETWORK_MODE", "local")
    monkeypatch.setattr(knowledge_chat, "DATA_FILE", knowledge)
    monkeypatch.setattr(search, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(search, "_LOCAL_INDEX", None)
    monkeypatch.setattr(search, "_EMB_MAT", None)
    monkeypatch.setattr(search, "_SEARCH_STATUS", {})
    monkeypatch.setattr(style, "profile_path", lambda: tmp_path / "style.json")
    original = LocalScriptRunner.run
    def joined(runner, *args, **kwargs):
        try:
            return original(runner, *args, **kwargs)
        finally:
            if runner._script_thread is not None:
                runner._script_thread.join(timeout=3)
                assert not runner._script_thread.is_alive()
    monkeypatch.setattr(LocalScriptRunner, "run", joined)
    calls = []
    def fake_chat(messages):
        calls.append(messages)
        yield "蓝鲸先找三个用户验证需求。[资料1]"
    monkeypatch.setattr(service, "stream_chat", fake_chat)
    path = knowledge / "notes.md"
    path.write_text("# 蓝鲸\n\n蓝鲸先找三个用户验证需求。", encoding="utf-8")

    app = AppTest.from_file(str(ROOT / "chat_app.py")).run(timeout=30)
    app.sidebar.radio[0].set_value("本地资料问答").run(timeout=30)
    assert not app.exception and not app.chat_input[0].disabled
    assert any("1 个资料文件" in item.value for item in app.markdown)
    app.chat_input[0].set_value("蓝鲸先做什么？").run(timeout=30)
    assert not app.exception and len(calls) == 1
    assert "蓝鲸先找三个用户验证需求" in calls[0][0]["content"]
    assert "Markdown · notes.md" in calls[0][0]["content"]
    assert "Markdown · notes.md" in app.session_state["messages"][-1]["content"]

    style.save_style(loader.read_entries(), "简洁的旧风格", enabled=True)
    app.session_state["style_draft_digest"] = "old-draft"
    path.write_text("# 蓝鲸\n\n蓝鲸改为先验证付费意愿。", encoding="utf-8")
    app.run(timeout=30)
    assert not app.exception and app.session_state["messages"] == []
    assert "style_draft_digest" not in app.session_state
    assert any("旧画像不再应用" in item.value for item in app.warning)
    assert style.approved_style(loader.read_entries()) == ""
    extra = knowledge / "more.markdown"
    extra.write_text("另一份资料", encoding="utf-8")
    app.run(timeout=30)
    assert not app.exception and any("2 个资料文件" in item.value for item in app.markdown)
    path.unlink()
    extra.unlink()
    app.run(timeout=30)
    assert not app.exception and app.chat_input[0].disabled
    assert loader.load_entries() == [] and len(calls) == 1


def test_git_ignores_user_text_and_markdown_but_keeps_bundled_readme():
    names = ["knowledge/a.md", "knowledge/nested/B.MD", "knowledge/a.markdown",
             "knowledge/nested/a.MarkDown", "knowledge/a.txt", "knowledge/nested/A.TXT"]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=str(ROOT),
                            input=("\n".join(names + ["knowledge/README.md"]) + "\n").encode("utf-8"),
                            capture_output=True, check=False)
    assert result.returncode == 0 and set(result.stdout.decode("utf-8").splitlines()) == set(names)


def test_release_audit_refuses_all_user_formats_without_reading_private_markdown(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("markdown_release", ROOT / "scripts/check_release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "README.md").write_text("# Bundled instructions", encoding="utf-8")
    names = ["private.MD", "private.MarkDown", "private.TXT"]
    for name in names:
        (root / name).write_bytes(b"\xff private, must not be decoded")
    result = release.audit(tmp_path)
    assert {item["file"] for item in result["findings"]} == {"knowledge/" + name for name in names}
    assert all(item["reason"] == "private data or generated artifact is a release candidate" for item in result["findings"])
    assert "must not be decoded" not in str(result)
