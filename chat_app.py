"""PersonaLens: local creator portrait extraction and evidence-aware chat."""

import streamlit as st

from app.config.settings import PAGE_TITLE, PAGE_ICON
from app.ui.access import enforce_access

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
enforce_access()
with st.sidebar:
    workflow = st.radio("使用方式", ["B站人物助手", "TXT 资料问答"], key="workflow_mode")
if workflow == "B站人物助手":
    from app.ingest.ui import render_creator_workflow
    render_creator_workflow()
else:
    from app.ui.knowledge_chat import render_knowledge_chat
    render_knowledge_chat()
