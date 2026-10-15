"""Chat orchestration between retrieval, prompt construction, and the LLM."""

from app.config.settings import MAX_HISTORY, UI_MODE
from app.llm.client import stream_chat
from app.persona.loader import load_persona
from app.prompt.builder import build_system_prompt
from app.rag.loader import load_entries
from app.rag.context import prepare_context
from app.rag.search import search_entries, get_search_status
from app.utils.helpers import check_risk_query


def stream_response(prompt, history):
    """Yield the assistant response and its source/risk metadata."""
    entries = load_entries()
    results = search_entries(prompt, entries)
    context_entries = [result[1] for result in results]
    context_text, sources = prepare_context(context_entries, prompt)
    persona = load_persona()
    if UI_MODE == "knowledge":
        from app.persona.style import approved_style
        persona += "\n\n" + approved_style(entries)
    messages = [{"role": "system", "content": build_system_prompt(persona, context_entries, context_text=context_text)}]
    messages.extend({"role": message["role"], "content": message["content"]} for message in history)
    messages.append({"role": "user", "content": prompt})
    for chunk in stream_chat(messages):
        yield chunk
    yield {"sources": sources, "risk_rule": check_risk_query(prompt), "search_status": get_search_status()}


def trim_history(messages):
    if len(messages) > MAX_HISTORY * 2:
        return messages[-MAX_HISTORY * 2:]
    return messages
