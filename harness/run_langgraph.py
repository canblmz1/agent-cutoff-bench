"""LangGraph prebuilt ReAct agent + langchain-openai against the fake server.

usage: run_langgraph.py BASE_URL MODE {chat|responses} {stream|nostream}
"""
from __future__ import annotations

import sys
from importlib.metadata import version

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from common import disk_state, emit, safe_write, short_exc, workdir

base, mode, api, streaming = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
d = workdir()
calls: list[dict] = []


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    calls.append({"path": path, "content_chars": len(content)})
    return safe_write(d, path, content)


llm = ChatOpenAI(
    model="bench-model",
    base_url=f"{base}/{mode}/v1",
    api_key="sk-bench",
    streaming=(streaming == "stream"),
    use_responses_api=(api == "responses"),
    max_retries=0,
)
agent = create_react_agent(llm, [write_file])

rec = {
    "framework": "langgraph", "versions": {p: version(p) for p in ("langgraph", "langchain-core", "langchain-openai")},
    "mode": mode, "api": api, "streaming": streaming == "stream",
}
try:
    out = agent.invoke({"messages": [("user", "Write the 40-line report to report.txt.")]}, {"recursion_limit": 6})
    msgs = out["messages"]
    ai = [m for m in msgs if m.type == "ai"]
    rec["outcome"] = "returned"
    rec["final_answer"] = (ai[-1].content if ai else None) or ""
    rec["invalid_tool_calls"] = sum(len(getattr(m, "invalid_tool_calls", []) or []) for m in ai)
    rec["tool_messages"] = [str(m.content)[:200] for m in msgs if m.type == "tool"]
except Exception as e:  # noqa: BLE001
    rec["outcome"] = "raised"
    rec["error"] = short_exc(e)
rec["tool_executed"] = bool(calls)
rec["disk"] = disk_state(d)
emit(rec)
