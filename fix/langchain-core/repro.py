from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.output_parsers.openai_tools import parse_tool_call
from langchain_core.outputs import ChatGenerationChunk

# A write_file call whose arguments were cut off mid-string
# (finish_reason="length", or the stream closed before finishing).
ARGS = '{"path": "notes.txt", "content": "line 1\\nline 2\\nline 3\\nli'


class CutOffModel(BaseChatModel):
    streaming: bool = False

    @property
    def _llm_type(self) -> str:
        return "cut-off"

    def _generate(self, *args, **kwargs):
        raise NotImplementedError

    def _stream(self, *args, **kwargs):
        for i in range(0, len(ARGS), 10):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        tool_call_chunk(
                            name="write_file" if i == 0 else None,
                            args=ARGS[i : i + 10],
                            id="call_1" if i == 0 else None,
                            index=0,
                        )
                    ],
                )
            )


msg = CutOffModel(streaming=True).invoke("save my notes")  # e.g. ChatOpenAI(streaming=True)
print("streamed tool_calls:", msg.tool_calls)
print("streamed invalid_tool_calls:", msg.invalid_tool_calls)

# The same arguments on the non-streaming path are rejected:
try:
    parse_tool_call({"id": "call_1", "function": {"name": "write_file", "arguments": ARGS}})
except Exception as e:
    print("non-streamed:", type(e).__name__)
