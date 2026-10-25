"""First-use API form, restricted to the computer's local-only mode."""

import os
import streamlit as st

from app.config.runtime import ConfigError, environment_config, get_config, save_config


def render_settings_navigation():
    if os.getenv("MD_NETWORK_MODE", "").lower() != "local":
        return
    if st.sidebar.button("API 设置", key="open_api_settings"):
        st.session_state.api_settings_open = True
        st.session_state.bilibili_settings_open = False
    if st.sidebar.button("B站采集设置", key="open_bilibili_settings"):
        st.session_state.bilibili_settings_open = True
        st.session_state.api_settings_open = False


def render_onboarding():
    try:
        config = get_config()
        broken = False
    except ConfigError as exc:
        st.error(str(exc))
        config, broken = environment_config(), True
    configured = bool(config["api_key"]) and not broken
    local = os.getenv("MD_NETWORK_MODE", "").lower() == "local"
    if not local:
        st.sidebar.caption("API配置仅可在电脑以本机模式启动时修改。")
        if not configured:
            st.warning("请先在电脑运行 start_local.bat，完成API配置后再启用局域网。")
            st.stop()
        return
    if configured and not st.session_state.get("api_settings_open"):
        st.sidebar.caption("聊天API：已配置；网页设置优先，没有网页配置时兼容.env。")
        return
    st.title("欢迎使用 PersonaLens" if not configured else "API 设置")
    st.caption("填写一次即可。Key只保存在本机 .runtime/api-settings.json，不提交Git；原来的.env仍兼容，网页保存的设置优先。")
    st.info("此处只保存配置，不联网、不验证余额、不消耗API额度。请只填写你信任的服务商接口。")
    if configured:
        st.caption("已有Key已配置，不会回显；Key留空表示继续使用原Key。")
    with st.form("api_setup", clear_on_submit=True):
        key = st.text_input("API Key", type="password", max_chars=512, key="setup_api_key")
        with st.expander("接口和模型（默认DeepSeek，其他兼容接口可修改）"):
            url = st.text_input("完整聊天接口地址", value=config["url"], max_chars=500)
            model = st.text_input("聊天模型名", value=config["model"], max_chars=150)
            proxy = st.checkbox("使用系统代理", value=config["use_system_proxy"])
        submitted = st.form_submit_button("保存配置并开始")
    if submitted:
        try:
            save_config(key, url, model, proxy)
            st.session_state.api_settings_open = False
            st.session_state.pop("setup_api_key", None)
            st.rerun()
        except ConfigError as exc:
            st.error(str(exc))
        except OSError:
            st.error("保存失败，请检查本机运行目录写入权限；未显示Key或原始错误。")
    if configured and st.button("取消，返回助手"):
        st.session_state.api_settings_open = False
        st.rerun()
    st.stop()
