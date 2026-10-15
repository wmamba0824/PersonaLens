"""Local Bilibili creator creation workflow; never starts acquisition on rerun."""

import streamlit as st

from app.ingest.identity import parse_creator
from app.ingest.bilibili import collect_dynamics, CollectionError
from app.ingest.video import collect_video_text, transcription_status
from app.ingest.store import CreatorStore, BusyError
from app.ingest.persona import generate_portrait, redact_credentials, portrait_metrics
from app.ingest.conversation import build_index, stream_creator_response
from app.llm.client import LLMServiceError


def _safe_error(exc):
    if isinstance(exc, (ValueError, CollectionError, BusyError, LLMServiceError)):
        st.error(redact_credentials(str(exc)))
    else:
        st.error("操作失败，已保存的资料保留。请检查本地运行目录和依赖；原始错误正文不会显示。")


def render_creator_workflow():
    st.title("人物风格助手")
    st.caption("输入主页 → 文字动态 / 视频字幕与可选本地转录 → 画像审核 → 本地知识库 → 风格聊天。AI模拟，不是本人。")
    st.info("只处理你有权使用的公开内容；跳过转载、付费/试看和合作视频，不抓评论、不绕过平台限制。视频资料可能包含嘉宾、引用或识别错误，不自动认定每句话都出自本人。")
    with st.form("creator_identity"):
        link = st.text_input("B站主页或UID", placeholder="https://space.bilibili.com/数字UID")
        opened = st.form_submit_button("打开 / 创建人物")
    if opened:
        try:
            identity = parse_creator(link)
            st.session_state.creator_uid = identity.uid
        except ValueError as exc:
            _safe_error(exc)
    uid = st.session_state.get("creator_uid")
    if not uid:
        st.caption("这里只解析身份；没有点击采集前，不向B站发送请求。也可切换到TXT资料问答，导入自己的内容。")
        return
    try:
        store = CreatorStore(parse_creator(uid))
        data = store.read()
    except (ValueError, OSError) as exc:
        _safe_error(exc)
        return
    signature = (store.path.name, uid, data["corpus_digest"], data.get("profile", {}).get("revision"),
                 data.get("profile", {}).get("approved"))
    if st.session_state.get("creator_chat_signature") != signature:
        st.session_state.creator_chat_signature = signature
        st.session_state.creator_messages = []
        st.session_state.pop("creator_index", None)
        st.session_state.pop("creator_index_status", None)
    st.subheader(f"{data['name']} · UID {uid}")
    st.caption(f"资料与画像独立保存在 .runtime/creators/bilibili_{uid}/；切换人物会清空本页旧对话。")
    job = data.get("job", {})
    if job.get("status") in {"running", "failed"}:
        st.warning(f"上次任务：{job.get('stage')} / {job.get('status')}。已保存的阶段保留；不会自动重试付费请求。")

    st.markdown("### 1. 采集并预览")
    limit = st.slider("本次最多采集本人文字条数", 5, 100, 30, 5, key="creator_limit")
    pages = st.slider("最多请求页数", 1, 5, 3, key="creator_pages")
    rights = st.checkbox("我有权使用这些内容；同意在上述范围内向B站发起采集请求。", key="creator_rights_" + uid)
    if st.button("采集 / 更新本人文字", disabled=not rights, key="creator_collect"):
        try:
            with st.status("采集中…", expanded=True) as status:
                with store.operation("collect"):
                    result = collect_dynamics(store.identity, max_items=limit, max_pages=pages,
                                              progress=lambda message: status.write(message))
                    if not result["records"]:
                        raise ValueError("本次没有获取到可用本人文字。以视频为主的账号请使用下方“视频字幕 / 本地转录”，没有生成画像。")
                    store.save_collection(result)
                status.update(label=f"采集完成：本次{len(result['records'])}条。", state="complete")
            st.rerun()
        except (CollectionError, ValueError, BusyError, OSError) as exc:
            _safe_error(exc)
    with st.expander("视频字幕 / 本地转录（视频型账号用这里）"):
        video_limit = st.slider("本次最多处理视频数", 1, 5, 3, key="creator_video_limit")
        video_pages = st.slider("视频列表最多请求页数", 1, 5, 1, key="creator_video_pages")
        min_seconds = st.selectbox("跳过过短视频", [0, 30, 60, 180],
            format_func=lambda value: "不按时长过滤" if value == 0 else f"少于{value}秒", key="creator_video_min")
        asr = st.checkbox("没有可用字幕时，允许下载独立音轨并在本机转录（不调用云端语音API）。", key="creator_asr_" + uid)
        sample_seconds = st.slider("每条本地转录前多少秒（不是完整视频）", 30, 600, 180, 30, key="creator_video_sample")
        st.caption("优先用平台字幕；转录只取每条开头的所选时段。仅处理第1P，音轨上限30分钟 / 40MB，临时音频用完删除。缓存按人物隔离，重复点击不会重复转录；不会自动安装依赖或下载模型。")
        _, asr_message = transcription_status()
        st.caption(asr_message)
        if st.button("采集视频资料", disabled=not rights, key="creator_collect_video"):
            try:
                with st.spinner("读取视频资料…"):
                    status = st.empty()
                    with store.operation("video_collect"):
                        result = collect_video_text(store, max_videos=video_limit, max_pages=video_pages,
                            min_seconds=min_seconds, allow_transcribe=asr, sample_seconds=sample_seconds,
                            progress=lambda message: status.caption(message))
                        # Save coverage even for zero usable speech, but never invent text.
                        store.save_collection(result)
                    status.success(f"视频处理完成：{len(result['records'])}条可用资料。")
                st.rerun()
            except (CollectionError, ValueError, BusyError, OSError) as exc:
                _safe_error(exc)
    data = store.read()
    st.write(f"累计 {len(data['records'])} 条账号资料（不代表完整历史；每个视频只计一条）")
    for account, summary in data.get("linked_sources", {}).get("wechat", {}).items():
        st.caption(f"已关联公众号「{account}」：{summary['accepted']}篇 / {summary['chars']}字；同一人物关系由用户确认，不是平台官方认证。")
    if data.get("collection", {}).get("warning"):
        st.warning(data["collection"]["warning"])
    coverage = data.get("collection", {}).get("coverage", {})
    if coverage:
        st.caption(f"本次视频：尝试{coverage.get('attempted', 0)}条 / 字幕{coverage.get('subtitle', 0)}条 / 本地转录{coverage.get('transcript', 0)}条 / 缓存{coverage.get('cache', 0)}条")
        for skipped in coverage.get("skipped", []):
            st.caption(skipped["bvid"] + "：" + skipped["reason"])
        if not data["records"]:
            st.warning("没有足够语音或文字，不能生成可靠画像。可增加列表页数、排除过短视频，或换一个有较多公开表达的账号；不会用标题代替原话。")
    with st.expander("查看采集原文、日期和来源"):
        for record in data["records"][-20:]:
            st.caption(record["published_at"])
            if record.get("source_type"):
                st.caption(f"{record.get('title', '')} · {record['source_type']} · {record.get('sample_range', '')}")
                st.caption(record.get("uncertainty", "") + record.get("speaker_note", ""))
            st.text(record["text"])
            st.link_button("查看公众号原文" if record.get("source_type") == "wechat_article" else "查看B站原文", record["url"])

    st.markdown("### 2. 生成与审核人物画像")
    usable_records, usable_chars = portrait_metrics(data)
    enough = usable_records >= 3 and usable_chars >= 600
    st.caption(f"画像有效样本：{usable_records}条 / {usable_chars}字（不计时间码和已识别歌词）。")
    if not enough:
        st.caption("生成画像至少需要3条本人内容、合计600字有效文字。资料不足时请补充采集，不用标题或歌词凑画像。")
    fresh = store.profile_fresh(data)
    if data.get("profile") and not fresh:
        st.warning("资料已变化，旧画像已停止用于聊天；请重新生成并审核。")
    paid = st.checkbox("同意最多12,000字画像样本发送给聊天API，并消耗一次生成请求的API额度。", key="creator_paid_" + uid)
    force = st.checkbox("重新生成已有画像（额外一次API调用）", key="creator_force_" + uid) if fresh else False
    if st.button("生成人物画像草稿", disabled=not paid or not enough or (fresh and not force), key="creator_generate"):
        try:
            with st.spinner("提炼画像中；本次最多一次调用，不自动重试…"):
                generate_portrait(store, consent=paid, force=force)
            st.rerun()
        except (ValueError, BusyError, LLMServiceError, OSError) as exc:
            _safe_error(exc)
    data = store.read()
    if store.profile_fresh(data):
        profile = data["profile"]
        if profile.get("approved"):
            st.success("当前画像已审核启用，无需再次生成；可直接使用下方人物聊天。")
        editor_key = f"creator_editor_{uid}_{profile['revision']}"
        edited = st.text_area("人物画像草稿（可编辑；检查原句、观点、日期和推断）", value=profile["text"], height=300, key=editor_key)
        with st.expander("画像实际使用的样本（编号对应草稿）"):
            for number, chunk in enumerate(profile["evidence"], 1):
                st.caption(f"[样本{number}] {chunk['date']}")
                st.text(chunk["text"])
                st.link_button("样本原文", chunk["url"])
        reviewed = st.checkbox("我已核对画像，确认它仅是基于资料的AI模拟，不代表本人。", key="creator_reviewed_" + uid + profile['revision'])
        if st.button("确认画像，准备知识库", disabled=not reviewed or not edited.strip(), key="creator_approve"):
            try:
                store.approve(edited, expected_digest=data["corpus_digest"], expected_revision=profile["revision"])
                st.rerun()
            except (ValueError, BusyError, OSError) as exc:
                _safe_error(exc)
    data = store.read()
    ready = store.profile_fresh(data) and data["profile"].get("approved") is True
    if not ready:
        st.caption("完成画像审核后，才会开放人物聊天。")
        return

    render_person_chat(store, data)


def _render_sources(sources):
    with st.expander("本次实际参考资料"):
        for source in sources:
            st.caption(f"[资料{source['number']}] {source['date']}")
            if source.get("source_file"):
                st.text("来源文件：" + source["source_file"])
            if source.get("source_type"):
                if source["source_type"] == "wechat_article":
                    st.caption(f"公众号「{source.get('account_name', '')}」 · {source.get('title', '')}")
                else:
                    st.caption(f"{source['source_type']} · {source.get('bvid', '')} · {source.get('sample_range', '')}")
                st.caption(source.get("uncertainty", "") + source.get("speaker_note", ""))
            st.text(source["text"])
            if source.get("url"):
                st.link_button("查看原文", source["url"])


def render_person_chat(store, data):
    """Chat with one reviewed creator; sources and history remain isolated."""
    signature = (store.path.name, data["corpus_digest"], data["profile"]["revision"])
    if st.session_state.get("person_chat_signature") != signature:
        st.session_state.person_chat_signature = signature
        st.session_state.creator_messages = []
        st.session_state.pop("creator_index", None)
        st.session_state.pop("creator_index_status", None)
    st.markdown("### 人物知识库与聊天")
    if "creator_index_status" not in st.session_state:
        try:
            with st.spinner("准备本地知识索引（首次可能需要下载模型）…"):
                index, message = build_index(store, data)
            st.session_state.creator_index = index
            st.session_state.creator_index_status = message
        except (OSError, ValueError) as exc:
            _safe_error(exc)
            return
    st.caption(st.session_state.creator_index_status)
    if st.button("重新准备索引", key="creator_reindex"):
        st.session_state.pop("creator_index_status", None)
        st.rerun()
    st.caption("问题、人物画像、最近对话和最多6,000字检索片段会发送给聊天API并消耗额度。资料文件与索引留在电脑，不是完全离线AI。")
    st.caption("画像只用于表达风格；回答应分开标注资料依据和AI推演。引用编号会校验，具体内容仍需核对原文。")
    if st.button("清空人物对话", key="creator_clear"):
        st.session_state.creator_messages = []
        st.rerun()
    messages = st.session_state.get("creator_messages", [])
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sources"):
                _render_sources(message["sources"])
    chat_consent = st.checkbox("同意提问时发送上述内容并消耗聊天API额度。", key="creator_chat_consent_" + store.path.name)
    question = st.chat_input("和这个人物风格助手聊聊…", disabled=not chat_consent, key="creator_chat_input")
    if question:
        placeholder = None
        try:
            with st.chat_message("user"):
                st.text(question)
            with st.chat_message("assistant"):
                placeholder = st.empty()
                answer, metadata = "", {}
                for chunk in stream_creator_response(store, question, messages, st.session_state.get("creator_index")):
                    if isinstance(chunk, dict):
                        metadata = chunk
                    else:
                        answer = redact_credentials(answer + chunk)
                        placeholder.markdown(answer + "▌")
                if not answer.strip():
                    raise LLMServiceError("模型没有返回文字，未保存空回答。")
                placeholder.markdown(answer)
                if metadata.get("lexical"):
                    st.caption("本次使用关键词检索，不是向量检索。")
                _render_sources(metadata.get("sources", []))
            st.session_state.creator_messages = (messages + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer, "sources": metadata.get("sources", []),
                 "prompt_version": metadata.get("prompt_version")}])[-20:]
        except (ValueError, LLMServiceError, OSError) as exc:
            if placeholder is not None:
                placeholder.empty()
            _safe_error(exc)
