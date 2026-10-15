"""Shared data, source, and risk helpers."""

import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

from app.config.settings import DATA_FILE, PERSONA_FILE, PROJECT_ROOT, TRANSCRIPT_DIR, XUEQIU_DIR
from app.rag.context import prepare_context

BJ_TZ = timezone(timedelta(hours=8))


def run_script(script_name, label, timeout=300, args=None):
    script_path = Path(script_name)
    if not script_path.is_absolute():
        script_path = PROJECT_ROOT / script_path
    cmd = [sys.executable, str(script_path)] + (args or [])
    st.session_state.running_task = label
    log_placeholder = st.empty()
    full_log = []
    log_placeholder.markdown(
        f'<div class="terminal"><span class="time">[{datetime.now(BJ_TZ).strftime("%H:%M:%S")}]</span> '
        f'<span class="info">⟳ {label}...</span>\n</div>', unsafe_allow_html=True
    )
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in iter(process.stdout.readline, ""):
        stripped = line.strip()
        timestamp = datetime.now(BJ_TZ).strftime("%H:%M:%S")
        css_class = "info"
        if "✅" in line or "完成" in line or "成功" in line:
            css_class = "success"
        elif "❌" in line or "错误" in line or "失败" in line or "Error" in line:
            css_class = "error"
        elif "⚠️" in line or "警告" in line:
            css_class = "warn"
        full_log.append((timestamp, stripped, css_class))
        display = "\n".join(f'<span class="time">[{t}]</span> <span class="{c}">{line}</span>' for t, line, c in full_log[-20:])
        log_placeholder.markdown(f'<div class="terminal">{display}\n<span class="time">[⟳]</span> <span class="info">运行中...</span></div>', unsafe_allow_html=True)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        timestamp = datetime.now(BJ_TZ).strftime("%H:%M:%S")
        final = f'<span class="time">[{timestamp}]</span> <span class="error">{"❌ " + label + " 超时! (>" + str(timeout) + "s)"}</span>'
        log_placeholder.markdown(f'<div class="terminal">{final}</div>', unsafe_allow_html=True)
        st.session_state.running_task = None
        st.cache_resource.clear()
        return False
    timestamp = datetime.now(BJ_TZ).strftime("%H:%M:%S")
    if process.returncode == 0:
        final = f'<span class="time">[{timestamp}]</span> <span class="success">{"✅ " + label + " 完成!"}</span>'
    else:
        final = f'<span class="time">[{timestamp}]</span> <span class="error">{"❌ " + label + " 失败! (code: " + str(process.returncode) + ")"}</span>'
    final_log_html = "<br>".join(f'<span class="time">[{t}]</span> <span class="{c}">{line}</span>' for t, line, c in full_log[-15:])
    log_placeholder.markdown(f'<div class="terminal">{final_log_html}<br>{final}</div>', unsafe_allow_html=True)
    st.session_state.log_messages = full_log
    st.session_state.running_task = None
    st.cache_resource.clear()
    return process.returncode == 0


def get_data_stats():
    stats = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            stats["b站发言"] = len([line for line in f.read().split("\n") if line.strip()])
    if os.path.isdir(TRANSCRIPT_DIR):
        files = os.listdir(TRANSCRIPT_DIR)
        stats["投稿视频"] = len([name for name in files if name.endswith(".txt") and "直播回放" not in name])
        stats["直播回放"] = len([name for name in files if name.endswith(".txt") and "直播回放" in name])
    if os.path.isdir(XUEQIU_DIR):
        for name in ["posts.txt", "replies.txt"]:
            path = os.path.join(XUEQIU_DIR, name)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    stats[name.replace(".txt", "")] = len([line for line in f if line.strip().startswith("[")])
    if os.path.exists(PERSONA_FILE):
        stats["persona"] = os.path.getsize(PERSONA_FILE)
    return stats


def get_file_date(path):
    if os.path.exists(path):
        return datetime.fromtimestamp(os.path.getmtime(path), tz=BJ_TZ).strftime("%Y-%m-%d %H:%M")
    return "从未"


def get_latest_video_date(dir_path):
    if not os.path.isdir(dir_path):
        return "从未"
    dates = []
    for filename in os.listdir(dir_path):
        match = re.match(r"(\d{4})(\d{2})(\d{2})", filename)
        if match:
            dates.append(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    return max(dates) if dates else "从未"


def format_sources(context_entries, query=""):
    return prepare_context(context_entries, query)[1]


def check_risk_query(query):
    for keyword in ["加杠杆", "满仓", "梭哈", "全仓", "借钱", "配资", "高杠杆"]:
        if keyword in query:
            return "不建议借钱炒股、满仓梭哈或使用高杠杆。历史个人操作不可照搬；应考虑还款压力、亏损承受能力和分散风险。"
    return None
