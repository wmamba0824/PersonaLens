"""Export reviewed profile/chat only, never configuration or a whole corpus."""

from app.ingest.persona import redact_credentials


def export_profile(data):
    text = f"# {data['name']} · AI风格画像\n\nAI模拟，不是本人；仅基于有限历史资料。\n\n"
    text += data.get("profile", {}).get("text", "尚未生成画像。")
    text += "\n\n## 状态\n\n"
    fresh = data.get("profile", {}).get("corpus_digest") == data["corpus_digest"]
    text += "画像已审核启用。" if fresh and data.get("profile", {}).get("approved") else "画像未审核或已过期，不应当作当前启用版本。"
    return redact_credentials(text)


def export_chat(data, messages):
    blocks = [f"# {data['name']} · AI对话记录", "AI模拟，不是本人；回答及引用仍需人工核对。"]
    for message in messages:
        blocks.append("## " + ("我" if message["role"] == "user" else "AI助手"))
        blocks.append(message["content"])
        for source in message.get("sources", []):
            origin = source.get("url") or source.get("source_file", "来源未标记")
            blocks.append(f"[资料{source['number']}] {source.get('date', '日期未知')} · {origin}")
    return redact_credentials("\n\n".join(blocks))
