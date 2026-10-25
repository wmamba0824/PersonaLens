"""Owner-only cookie form; all saved values stay server-side and never prefill."""

import os
import streamlit as st

from app.config.bilibili import COOKIE_ENV, CookieConfigError, cookie_status, save_cookies, disable_cookies


def render_bilibili_settings():
    local = os.getenv("MD_NETWORK_MODE", "").lower() == "local"
    try:
        status = cookie_status()
        broken = False
    except CookieConfigError:
        status = {"configured": dict.fromkeys(COOKIE_ENV, False), "source": "web", "verified": False}
        broken = True
    if not local:
        st.sidebar.caption("B站登录配置仅可在电脑本机模式修改；手机不会显示Cookie。")
        return
    if not st.session_state.get("bilibili_settings_open"):
        return
    st.title("B站采集设置")
    if "bilibili_settings_notice" in st.session_state:
        st.success(st.session_state.pop("bilibili_settings_notice"))
    st.caption("这是B站账号的登录态，不是聊天API Key，也不是扫码登录。只用TXT/MD时不用填。")
    st.warning("Cookie相当于登录凭据，只用你自己的；不要发到聊天、截图分享或提交Git。配置以明文保存在本机.runtime/bilibili-settings.json，不是加密保险库。")
    if broken:
        st.error("本机配置无法读取；请重新填写或停用。不会显示原始错误或自动请求B站。")
    source = "本机网页配置" if status["source"] == "web" else ".env / 进程环境"
    st.caption("当前来源：" + source + "；仅检查是否填写，未验证登录有效性。")
    for name, configured in status["configured"].items():
        st.text(name + "：" + ("已填写（值不回显）" if configured else "未填写"))
    with st.expander("这些值从哪里来？"):
        st.markdown("在你自己的浏览器登录B站，再到开发者工具的应用/存储 → Cookie列表，原样复制下面四个同名Cookie的**值**。不要复制整行Cookie。这里不会自动读取浏览器、登录、解验证码或绕过限制。")
    st.info("输入留空会保留原值；换账号时先停用旧配置，再完整填写新账号的值，避免混用。网页配置（包括停用状态）优先于旧.env。保存不联网；下次主动采集即时使用，无需重启。")
    with st.form("bilibili_cookie_form", clear_on_submit=True):
        values = {name: st.text_input(name, type="password", max_chars=4096 if name == "SESSDATA" else 512,
                                      key="bilibili_cookie_" + name) for name in COOKIE_ENV}
        submitted = st.form_submit_button("保存B站配置")
    if submitted:
        try:
            save_cookies(values)
            for name in COOKIE_ENV:
                st.session_state.pop("bilibili_cookie_" + name, None)
            st.session_state.bilibili_settings_notice = "已保存到本机，输入框已清空；未向B站发请求，登录有效性尚未验证。"
            st.session_state.pop("confirm_disable_bilibili", None)
            st.rerun()
        except CookieConfigError as exc:
            st.error(str(exc))
        except OSError:
            st.error("保存失败，请检查本机运行目录的写入权限；未显示Cookie或底层错误。")
    confirm = st.checkbox("确认停用本机B站登录态；不会删除.env，后续将匿名请求。", key="confirm_disable_bilibili")
    if st.button("停用B站登录态", disabled=not confirm):
        try:
            disable_cookies()
            for name in COOKIE_ENV:
                st.session_state.pop("bilibili_cookie_" + name, None)
            st.session_state.pop("confirm_disable_bilibili", None)
            st.rerun()
        except (CookieConfigError, OSError):
            st.error("停用失败，请检查本机配置路径及写入权限。")
    if st.button("返回人物助手"):
        st.session_state.bilibili_settings_open = False
        st.rerun()
    st.stop()
