"""Two edge cases any fix for cut-off streamed tool calls should get right.

Run it against any langchain-core build:  python edge_cases.py
"""
from langchain_core.messages import AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.messages.utils import message_chunk_to_message
from langchain_core.output_parsers.openai_tools import parse_tool_call


def streamed(args: str) -> AIMessageChunk:
    """A finished stream: the chunks summed, ending with chunk_position="last"."""
    first = AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(name="w", args=args, id="1", index=0)])
    return first + AIMessageChunk(content="", chunk_position="last")


cut_off = '{"path": "a.txt", "content": "line 1\\nli'
raw_newline = '{"text": "one\ntwo"}'  # a raw newline character inside a JSON string

# 1. A complete call whose string holds a raw newline. The non-streaming
#    parser accepts it (json.loads(..., strict=False)); streaming should too.
non_streamed = parse_tool_call({"id": "1", "function": {"name": "w", "arguments": raw_newline}})
msg = message_chunk_to_message(streamed(raw_newline))
print("1. non-streamed args:", non_streamed["args"])
print("   streamed tool_calls:", msg.tool_calls, "| invalid:", len(msg.invalid_tool_calls))
print("   ->", "OK" if msg.tool_calls and not msg.invalid_tool_calls else "MISMATCH with non-streaming")

# 2. A cut-off call read from the summed chunks (full = full + chunk), the
#    pattern used when streaming by hand.
full = streamed(cut_off)
print("2. summed-chunks tool_calls:", full.tool_calls)
print("   ->", "OK" if not full.tool_calls else "cut-off call still looks valid")

# Reference: the converted message (what invoke() returns).
msg = message_chunk_to_message(streamed(cut_off))
print("3. invoke()-style message tool_calls:", msg.tool_calls, "| invalid:", len(msg.invalid_tool_calls))
print("   ->", "OK" if not msg.tool_calls else "cut-off call still looks valid")
