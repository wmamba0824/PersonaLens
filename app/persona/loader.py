"""Persona loading and persona-derived display data."""

import os
import re

import streamlit as st

from app.config.settings import FALLBACK_QUOTES, HOLDING_KEYWORDS, PERSONA_FILE


@st.cache_resource
def load_persona():
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        return f.read()


def get_persona_sections():
    sections = {}
    if not os.path.exists(PERSONA_FILE):
        return sections
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    current_section = "前言"
    current_content = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line.strip("# ").strip()
            current_content = []
        else:
            current_content.append(line)
    if current_content:
        sections[current_section] = "\n".join(current_content).strip()
    return sections


def get_holdings_from_persona():
    if not os.path.exists(PERSONA_FILE):
        return list(HOLDING_KEYWORDS[:7])
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    idx = text.find("### **4. ")
    if idx < 0:
        idx = text.find("### 4. ")
    section = text[idx:idx + 3000] if idx >= 0 else text[-3000:]
    return [keyword for keyword in HOLDING_KEYWORDS if keyword in section][:12]


def get_persona_keywords():
    if not os.path.exists(PERSONA_FILE):
        return []
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    words = set()
    idx = text.find("### **4. ")
    section = text[idx:idx + 2000] if idx >= 0 else ""
    for match in re.finditer(r"\*\*(.+?)\*\*", section):
        word = match.group(1).strip()
        if 2 <= len(word) <= 12:
            words.add(word)
    idx2 = text.find("### **5. ")
    section2 = text[idx2:idx2 + 1500] if idx2 >= 0 else ""
    for line in section2.split("\n"):
        for match in re.finditer(r"\*\*(.+?)\*\*", line):
            word = match.group(1).strip()
            if 4 <= len(word) <= 20:
                words.add(word)
    exclude = {"最新持仓", "重点关注", "博主身份", "投资哲学", "选股逻辑", "重点关注领域与持仓", "思维框架", "语言风格", "人格特质", "时间线"}
    return [word for word in words if word not in exclude][:20]


def get_holdings_timeline():
    if not os.path.exists(PERSONA_FILE):
        return []
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    entries = {}
    idx = text.find("最新持仓")
    section = text[idx:idx + 1500] if idx >= 0 else ""
    for line in section.split("\n"):
        line = line.strip()
        match = re.match(r"\*\s+\*\*(\d{4}年[^\*]*?)\*\*", line)
        if match:
            rest = line[match.end():].strip().lstrip(":： ")[:80]
            key = match.group(1).split("(")[0].strip()
            if key not in entries:
                holdings = re.findall(r"\*\*(.+?)\*\*", rest)
                if holdings:
                    rest = " → ".join(holdings[:4])
                entries[key] = rest
    idx2 = text.find("### **8. 时间线")
    if idx2 < 0:
        idx2 = text.find("### 8. 时间线")
    section2 = text[idx2:idx2 + 2000] if idx2 >= 0 else ""
    for line in section2.split("\n"):
        line = line.strip()
        match = re.match(r"\*\s+\*\*(\d{4}年[^\*]*?)\*\*", line)
        if match:
            rest = line[match.end():].strip().lstrip(":： ")[:80]
            key = match.group(1).split("(")[0].strip()
            if key not in entries:
                entries[key] = rest
    return sorted(entries.items(), key=lambda item: int(item[0][:4]) if item[0][:4].isdigit() else 9999)


def get_risk_rules():
    if not os.path.exists(PERSONA_FILE):
        return []
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    rules = []
    idx = text.find("### **2. ")
    section = text[idx:idx + 2000] if idx >= 0 else ""
    for line in section.split("\n"):
        line = line.strip()
        for keyword in ["杠杆", "融资", "满仓", "风险", "仓位", "止损", "纪律", "满仓微融资"]:
            if keyword in line:
                index = line.index(keyword)
                rules.append(line[max(0, index - 10):index + 80].strip())
                break
    return rules[:5]


def get_quotes():
    if not os.path.exists(PERSONA_FILE):
        return list(FALLBACK_QUOTES)
    with open(PERSONA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    quotes = []
    idx = text.find("### **6. ")
    section = text[idx:idx + 2000] if idx >= 0 else ""
    for match in re.finditer(r'[“”]([^“”]{4,40})[“”]', section):
        quote = match.group(1).strip()
        if quote and len(quote) > 4 and quote not in quotes:
            quotes.append(quote)
    for match in re.finditer(r'[“]([^“”]{6,40})[”]', text):
        quote = match.group(1).strip()
        if quote and quote not in quotes and not quote.startswith("20"):
            quotes.append(quote)
    return quotes[:15] if quotes else list(FALLBACK_QUOTES)
