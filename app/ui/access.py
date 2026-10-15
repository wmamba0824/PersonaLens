"""Opt-in LAN access gate; not an Internet-facing authentication system."""

import hmac
import hashlib
import os

import streamlit as st


def enforce_access():
    mode = os.getenv("MD_NETWORK_MODE", "local").lower()
    if mode == "local":
        return
    if mode != "lan":
        st.error("未知访问模式，已停止运行。请使用正式启动脚本。")
        st.stop()
    code = os.getenv("MD_LAN_ACCESS_CODE", "")
    if len(code) < 12:
        st.error("局域网访问码未配置，已关闭访问。请重新运行 start_lan.bat。")
        st.stop()
    expected = hashlib.sha256(code.encode()).hexdigest()
    if hmac.compare_digest(st.session_state.get("lan_access_digest", ""), expected):
        return
    st.title("资料助手 · 局域网访问")
    st.caption("输入电脑启动窗口显示的访问码。仅在可信 Wi-Fi 下使用，不要转发端口到公网。")
    with st.form("lan_access"):
        entered = st.text_input("访问码", type="password", key="lan_code_input")
        submitted = st.form_submit_button("进入对话")
    if submitted:
        if hmac.compare_digest(entered, code):
            st.session_state.lan_access_digest = expected
            st.session_state.pop("lan_code_input", None)
            st.rerun()
        else:
            st.error("访问码不正确。")
    st.stop()
