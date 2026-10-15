"""Local TXT/Markdown chat UI; no creator-specific assumptions."""

import streamlit as st

from app.chat.service import stream_response, trim_history
from app.config.settings import APP_NAME, CHAT_PLACEHOLDER, DATA_FILE, PROMPT_DISCLAIMER
from app.llm.client import LLMServiceError
from app.rag.loader import knowledge_files, load_entries
from app.rag.search import init_search
from app.rag.local_index import corpus_digest
from app.persona.style import generate_style, read_style, save_style


def render_style_controls(entries):
    with st.expander("可选：作者风格画像"):
        saved = read_style(entries)
        if "style_editor" not in st.session_state:
            st.session_state.style_editor = saved.get("text", "")
        st.caption("普通资料问答不需要画像。仅适合表达风格提炼，不冒充作者，也不替代事实依据。")
        if saved.get("stale"):
            st.warning("资料已变化，旧画像不再应用；需要重新生成并确认。")
        consent = st.checkbox("同意将最多 12,000 字资料样本发送给聊天 API 提取画像，会消耗额度。")
        if st.button("生成画像草稿", disabled=not entries or not consent):
            try:
                with st.spinner("正在提取有限样本中的表达风格…"):
                    draft = generate_style(entries)
                st.session_state.style_editor = draft
                st.session_state.style_draft_digest = corpus_digest(entries)
                st.session_state.style_approved = False
            except (LLMServiceError, ValueError) as exc:
                st.error(str(exc))
        text = st.text_area("检查并编辑画像草稿", key="style_editor", height=180)
        approved = st.checkbox("我已检查内容，确认仅用于表达风格。", key="style_approved")
        fresh = not saved.get("stale") or st.session_state.get("style_draft_digest") == corpus_digest(entries)
        if st.button("确认保存并启用", disabled=not entries or not text.strip() or not approved or not fresh):
            try:
                save_style(entries, text, enabled=True)
                st.session_state.messages = []
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except OSError:
                st.error("保存画像失败，请检查本地运行目录的写入权限。")
        if saved.get("enabled") and not saved.get("stale"):
            st.success("已启用确认后的风格画像")
            if st.button("停用画像，恢复通用回答"):
                try:
                    save_style(entries, saved["text"], enabled=False)
                    st.session_state.messages = []
                    st.rerun()
                except OSError:
                    st.error("停用画像失败，请检查本地运行目录的写入权限。")


def render_knowledge_chat():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    with st.sidebar:
        st.header(APP_NAME)
        st.caption("把 TXT 或 Markdown（.md / .markdown）放进资料目录（默认 knowledge/），然后点击重新加载。")
        if st.button("重新加载资料"):
            st.cache_resource.clear()
            st.session_state.messages = []
            st.rerun()
        if st.button("清空对话"):
            st.session_state.messages = []
            st.rerun()
        st.caption("重新加载会清空旧对话，避免混用不同版本资料。")

    st.title(APP_NAME)
    st.caption(PROMPT_DISCLAIMER)
    try:
        files = knowledge_files()
        signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in files)
        previous = st.session_state.get("knowledge_signature")
        if previous is not None and previous != signature:
            st.cache_resource.clear()
            st.session_state.messages = []
            for key in ("style_editor", "style_draft_digest", "style_approved"):
                st.session_state.pop(key, None)
            st.info("检测到资料变化，正在更新索引；旧对话已清空。")
        st.session_state.knowledge_signature = signature
        entries = load_entries()
        with st.spinner("准备检索索引（首次需要下载本地模型，之后复用）…"):
            status = init_search()
    except (OSError, ValueError) as exc:
        st.error(f"读取资料失败：{exc}")
        st.stop()
    with st.sidebar:
        st.write(f"{len(files)} 个资料文件 · {len(entries)} 个资料片段")
        st.caption(status["message"])
        if status["mode"] == "lexical_local_error":
            st.warning(status["message"])
        with st.expander("已加载文件"):
            for path in files:
                st.text(path.relative_to(DATA_FILE).as_posix() if DATA_FILE.is_dir() else path.name)
    render_style_controls(entries)
    if not entries:
        st.info("还没有资料：把自己的 TXT 或 Markdown 放入项目的 knowledge 文件夹，再点击“重新加载资料”。")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(CHAT_PLACEHOLDER, disabled=not entries)
    if not prompt:
        return
    history = list(trim_history(st.session_state.messages))
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    try:
        with st.chat_message("assistant"):
            placeholder = st.empty()
            answer, metadata = "", {}
            for chunk in stream_response(prompt, history):
                if isinstance(chunk, dict):
                    metadata = chunk
                else:
                    answer += chunk
                    placeholder.markdown(answer + "▌")
            if not answer.strip():
                raise LLMServiceError("模型未返回文字，请重试。")
            if metadata.get("sources"):
                answer += "\n\n---\n检索资料：" + " | ".join(metadata["sources"])
            if metadata.get("search_status", {}).get("mode") == "lexical_local_error":
                st.warning(metadata["search_status"]["message"])
            placeholder.markdown(answer)
        st.session_state.messages = trim_history(
            st.session_state.messages + [{"role": "assistant", "content": answer}]
        )
    except LLMServiceError as exc:
        st.session_state.messages.pop()
        st.error(str(exc))
