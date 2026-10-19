"""smolagents ToolCallingAgent against the fake server (Chat Completions).

usage: run_smolagents.py BASE_URL MODE {stream|nostream}
"""
from __future__ import annotations

import sys
from importlib.metadata import version

from smolagents import OpenAIModel, ToolCallingAgent, tool

from common import disk_state, emit, safe_write, short_exc, workdir

base, mode, streaming = sys.argv[1], sys.argv[2], sys.argv[3]
d = workdir()
calls: list[dict] = []


@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path: file name
        content: file content
    """
    calls.append({"path": path, "content_chars": len(content)})
    return safe_write(d, path, content)


model = OpenAIModel(model_id="bench-model", api_base=f"{base}/{mode}/v1", api_key="sk-bench",
                    client_kwargs={"max_retries": 0})
agent = ToolCallingAgent(tools=[write_file], model=model, stream_outputs=(streaming == "stream"),
                         max_steps=2, verbosity_level=0)
rec = {"framework": "smolagents", "versions": {"smolagents": version("smolagents")},
       "mode": mode, "api": "chat", "streaming": streaming == "stream"}
try:
    out = agent.run("Write the 40-line report to report.txt.")
    rec["outcome"] = "returned"
    rec["final_answer"] = str(out)[:200]
except Exception as e:  # noqa: BLE001
    rec["outcome"] = "raised"
    rec["error"] = short_exc(e)
rec["tool_executed"] = bool(calls)
rec["disk"] = disk_state(d)
emit(rec)
