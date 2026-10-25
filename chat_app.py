"""PersonaLens: local creator portrait extraction and evidence-aware chat."""

import streamlit as st

from app.config.settings import PAGE_TITLE, PAGE_ICON
from app.ui.access import enforce_access

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
enforce_access()
from app.ui.onboarding import render_onboarding, render_settings_navigation
from app.ui.bilibili_settings import render_bilibili_settings
render_settings_navigation()
render_bilibili_settings()
render_onboarding()
with st.sidebar:
    workflow = st.radio("使用方式", ["人物助手", "本地资料问答"], key="workflow_mode")
if workflow == "人物助手":
    from app.ingest.ui import render_creator_workflow
    render_creator_workflow()
else:
    from app.ui.knowledge_chat import render_knowledge_chat
    render_knowledge_chat()
