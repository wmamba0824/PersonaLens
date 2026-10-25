"""Saved creators, local-only identities and explicit document imports."""

import streamlit as st

from app.ingest.identity import parse_creator
from app.ingest.store import CreatorStore, BusyError, create_local, saved_creators
from app.ingest.documents import import_documents


def render_library():
    creators = saved_creators()
    if creators:
        with st.sidebar:
            st.subheader("我的人物")
            options = {row["uid"]: row for row in creators}
            selected = st.selectbox("已保存人物", list(options),
                format_func=lambda uid: f"{options[uid]['name']} · {options[uid]['records']}份 · " +
                ("可聊天" if options[uid]["ready"] else "待补资料/审核"), key="saved_creator")
            if st.button("打开已有人物"):
                st.session_state.creator_uid = selected
    with st.form("creator_identity"):
        link = st.text_input("B站主页或UID", placeholder="https://space.bilibili.com/数字UID")
        opened = st.form_submit_button("打开 / 创建人物")
    if opened:
        try:
            identity = parse_creator(link)
            store = CreatorStore(identity)
            if not (store.path / "snapshot.json").exists():
                store.write(store.read())
            st.session_state.creator_uid = identity.uid
            st.rerun()
        except (ValueError, OSError):
            st.error("无法打开人物，请检查B站主页/UID或本地运行目录权限。")
    with st.expander("没有B站ID？用自己的TXT/MD创建人物"):
        with st.form("local_creator"):
            name = st.text_input("本地人物名称", placeholder="例如：我的文章助手")
            local_opened = st.form_submit_button("创建本地人物")
        if local_opened:
            try:
                store = create_local(name)
                st.session_state.creator_uid = store.identity.uid
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(str(exc) if isinstance(exc, ValueError) else "创建失败，请检查运行目录权限。")
    return st.session_state.get("creator_uid")


def render_document_import(store):
    with st.expander("补充这个人物的 TXT / Markdown 资料", expanded=not store.read()["records"]):
        st.caption("这些资料会加入当前人物的同一知识库和画像样本，不再只是独立TXT问答。每次最多20份/8MB，单份2MB/20万字。")
        files = st.file_uploader("选择本人的原文资料", type=["txt", "md", "markdown"],
                                 accept_multiple_files=True, key="creator_files_" + store.identity.uid)
        consent = st.checkbox("我确认这些资料属于当前人物且有权使用；不把不同作者混为一人。",
                              key="creator_file_consent_" + store.identity.uid)
        if st.button("导入到当前人物", disabled=not files or not consent, key="creator_import_" + store.identity.uid):
            try:
                result = import_documents(store, [(file.name, file.getvalue()) for file in files], consent)
                st.session_state.document_import_notice = f"导入完成：新增/变更{result['accepted']}份，其中更新{result['updated']}份；重复跳过{result['skipped']}份。"
                st.rerun()
            except (ValueError, BusyError) as exc:
                st.error(str(exc))
            except OSError:
                st.error("本地导入失败，未显示原文或底层错误；请检查写入权限。")
    if "document_import_notice" in st.session_state:
        st.success(st.session_state.pop("document_import_notice"))
