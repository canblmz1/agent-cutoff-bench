"""OpenAI Agents SDK against the fake server.

usage: run_openai_agents.py BASE_URL MODE {chat|responses} {stream|nostream}
"""
from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version

from agents import Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel, Runner, function_tool, set_tracing_disabled
from openai import AsyncOpenAI

from common import disk_state, emit, safe_write, short_exc, workdir

base, mode, api, streaming = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
set_tracing_disabled(True)
d = workdir()
calls: list[dict] = []


@function_tool
def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    calls.append({"path": path, "content_chars": len(content)})
    return safe_write(d, path, content)


client = AsyncOpenAI(base_url=f"{base}/{mode}/v1", api_key="sk-bench", max_retries=0)
model = (OpenAIResponsesModel if api == "responses" else OpenAIChatCompletionsModel)(model="bench-model", openai_client=client)
agent = Agent(name="writer", instructions="Use tools.", tools=[write_file], model=model)

rec = {"framework": "openai-agents", "versions": {"openai-agents": version("openai-agents")},
       "mode": mode, "api": api, "streaming": streaming == "stream"}


async def main() -> None:
    try:
        if streaming == "stream":
            r = Runner.run_streamed(agent, "Write the 40-line report to report.txt.", max_turns=4)
            async for _ in r.stream_events():
                pass
        else:
            r = await Runner.run(agent, "Write the 40-line report to report.txt.", max_turns=4)
        rec["outcome"] = "returned"
        rec["final_answer"] = str(r.final_output)[:200]
    except Exception as e:  # noqa: BLE001
        rec["outcome"] = "raised"
        rec["error"] = short_exc(e)


asyncio.run(main())
rec["tool_executed"] = bool(calls)
rec["disk"] = disk_state(d)
emit(rec)
