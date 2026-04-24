from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


@tool("get_system_time")
def get_system_time() -> str:
    """Return local system time in ISO format."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


_LLM_BASE_URL = ""
_LLM_MODEL = ""
_LLM_API_KEY = ""


def set_quick_qa_llm_config(*, base_url: str, model: str, api_key: str) -> None:
    global _LLM_BASE_URL, _LLM_MODEL, _LLM_API_KEY
    _LLM_BASE_URL = str(base_url or "").strip()
    _LLM_MODEL = str(model or "").strip()
    _LLM_API_KEY = str(api_key or "").strip()
    _build_llm.cache_clear()


def _config_error_message() -> str:
    return (
        "Quick QA LLM is not configured. Start server with "
        "--llm-base-url, --llm-model, --llm-api-key."
    )


@lru_cache(maxsize=1)
def _build_llm() -> ChatOpenAI | None:
    if not _LLM_BASE_URL or not _LLM_MODEL or not _LLM_API_KEY:
        return None
    kwargs: dict[str, Any] = {
        "model": _LLM_MODEL,
        "api_key": _LLM_API_KEY,
        "base_url": _LLM_BASE_URL,
    }
    return ChatOpenAI(**kwargs)


def quick_qa_llm_configured() -> bool:
    return bool(_LLM_BASE_URL and _LLM_MODEL and _LLM_API_KEY)


def _coerce_message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, (int, float, bool)):
        return str(content)
    if isinstance(content, list):
        parts: list[str] = []
        for it in content:
            t = _coerce_message_text(it)
            if t:
                parts.append(t)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        # Common OpenAI-compatible chunk shapes:
        # {"type":"text","text":"..."} or {"text":"..."} or nested content
        keys = ("text", "content", "output_text", "value")
        parts: list[str] = []
        for k in keys:
            if k in content:
                t = _coerce_message_text(content.get(k))
                if t:
                    parts.append(t)
        if parts:
            return "\n".join(parts).strip()
        return ""
    return str(content).strip()


def _build_agent(
    list_available_kbs: Callable[[], list[str]],
    search_in_kb: Callable[[str, str], str],
):
    llm = _build_llm()
    if llm is None:
        return None
    
    @tool("list_user_available_knowledge_bases")
    def list_user_available_knowledge_bases() -> str:
        """List all knowledge base names available to current user."""
        rows = list_available_kbs()
        if not rows:
            return "当前用户没有可用知识库。"
        return "\n".join(f"- {n}" for n in rows)

    @tool("search_specific_knowledge_base")
    def search_specific_knowledge_base(kb_name: str, query: str) -> str:
        """Search answer text from a specific knowledge base by name."""
        kb = str(kb_name or "").strip()
        q = str(query or "").strip()
        if not kb:
            return "缺少知识库名称。"
        if not q:
            return "缺少检索问题。"
        txt = str(search_in_kb(kb, q) or "").strip()
        if not txt:
            return f"知识库 {kb} 未检索到匹配内容。"
        return txt

    return create_react_agent(
        llm,
        tools=[get_system_time, list_user_available_knowledge_bases, search_specific_knowledge_base],
        prompt=(
            "你是一个RAGret服务的快速问答助手. "
            "使用基于知识库检索的工具来回答问题. "
            "当知识库目标不明确时，首先列出所有可用的知识库. "
            "使用 search_specific_knowledge_base 工具进行检索回答. "
            "回答使用用户语言，专业术语不需要翻译."
        ),
    )


def run_quick_qa(
    question: str,
    *,
    list_available_kbs: Callable[[], list[str]],
    search_in_kb: Callable[[str, str], str],
    messages: list[dict[str, str]] | None = None,
    on_tool_event: Callable[[str], None] | None = None,
    lang: str = "zh",
) -> dict[str, object]:
    use_zh = str(lang or "").lower().startswith("zh")
    q = str(question or "").strip()
    if not q:
        return {"answer": "请先输入一个问题。", "used_tool": False, "tool_name": ""}
    tool_events: list[str] = []

    def _list_available_kbs_wrapped() -> list[str]:
        msg = "正在列出可用知识库。" if use_zh else "Listing available knowledge bases."
        tool_events.append(msg)
        if on_tool_event:
            on_tool_event(msg)
        return list_available_kbs()

    def _search_in_kb_wrapped(kb_name: str, query: str) -> str:
        kb = str(kb_name or "").strip()
        if kb:
            msg = (f"正在查询{kb}知识库。") if use_zh else (f"Searching knowledge base: {kb}.")
        else:
            msg = "正在查询指定知识库。" if use_zh else "Searching the specified knowledge base."
        tool_events.append(msg)
        if on_tool_event:
            on_tool_event(msg)
        return search_in_kb(kb_name, query)

    agent = _build_agent(
        list_available_kbs=_list_available_kbs_wrapped,
        search_in_kb=_search_in_kb_wrapped,
    )
    if agent is None:
        return {"answer": _config_error_message(), "used_tool": False, "tool_name": ""}
    history_msgs: list[HumanMessage | AIMessage] = []
    for item in list(messages or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            history_msgs.append(AIMessage(content=content))
        elif role == "user":
            history_msgs.append(HumanMessage(content=content))
    history_msgs.append(HumanMessage(content=q))
    result = agent.invoke({"messages": history_msgs})
    messages = list(result.get("messages") or [])
    answer = ""
    used_tools: list[str] = []
    last_tool_output = ""
    for m in messages:
        if isinstance(m, ToolMessage):
            nm = str(getattr(m, "name", "") or "").strip()
            if nm:
                used_tools.append(nm)
            t = _coerce_message_text(getattr(m, "content", ""))
            if t:
                last_tool_output = t
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            answer = _coerce_message_text(getattr(m, "content", ""))
            if answer:
                break
    if not answer and last_tool_output:
        answer = last_tool_output
    if not answer:
        answer = "暂未生成回答，请重试。"
    return {
        "answer": answer,
        "used_tool": bool(used_tools),
        "tool_name": ",".join(used_tools),
        "tool_events": tool_events,
    }
