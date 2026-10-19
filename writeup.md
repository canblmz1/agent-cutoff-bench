# The tool said "Wrote 866 characters". The file should have been 2,120.

Here is a failure that produces no error anywhere.

A model is asked to write a 40-line file. It starts a `write_file` tool call and
streams the arguments. Halfway through line 17 the stream stops: the reply hit
its output-token limit, or a proxy closed the connection. The arguments now end
in the middle of a JSON string.

What should an agent framework do with that call? The arguments are
incomplete, so the only safe answer is to not run it, and to say why.

I built a small fake model server that sends exactly this, the same way, to
every client, and pointed six agent stacks at it. The tool in each harness
really writes the file; afterwards the bench reads the file back from disk.

## What happened

OpenAI Agents SDK, smolagents, Codex CLI and Hermes Agent did not run the call.
Their behaviour differs after that (Hermes retries and then tells the user the
reply was cut off and nothing was changed; the Agents SDK sends the model a
generic tool error; Codex stops with an "incomplete response" error), but none
of them touched the disk.

LangGraph, in Python and in JavaScript, ran it when streaming was on.
`report.txt` ended up with 16 lines and a fragment of line 17, 866 bytes instead
of 2,120. The tool returned `Wrote 866 characters to report.txt`, that result
went back to the model, and the agent answered `Done.` Both the Chat
Completions and the Responses API behaved the same. With streaming off, the
same code did not run the call.

So for the same agent and the same model output, whether a half-written file
lands on disk depends on one flag, and the flag most agents turn on.

## Why

LangChain builds the final tool calls of a streamed message with a partial-JSON
parser. That parser exists for a good reason: it lets a UI show arguments while
they are still arriving. The problem is that it is still used after the stream
has ended, when nothing more can arrive. At that point it closes the open
string and the open object, and `{"content": "line 01 … line 17: the quick`
becomes a valid argument object.

The non-streaming path parses the same arguments with `json.loads`, fails, and
reports the call as invalid. The two paths disagree about the same bytes.

## The fix

In Python the change is small: once the stream has ended, tool-call arguments
must parse as JSON (with the same leniency the non-streaming parser uses), or
the call goes to `invalid_tool_calls` with the parse error. Mid-stream parsing
stays as it is. With that change, the LangGraph runs above stop running the
cut-off call, and the complete call still runs.

Both sides had already been reported upstream: Python in langchain#41285 and
JavaScript in langchainjs#11469. What the bench adds is the end-to-end result,
the comparison with other frameworks, and three edge cases for the fix: a
complete call that contains a raw newline must still run, a cut-off call
must also be rejected when you sum the chunks yourself, and in a stream that
carries two calls only the cut one may be dropped. The patch, its tests, a
twenty-line reproduction and an edge-case script are in the repository. On the
JavaScript side, the proposed fix (not merged) only covers the output-limit
case, not a stream that simply ends.

## What this does and does not show

The server is fake. It shows how each client handles these streams, not how
often real providers send them. It covers one tool, one cut point and
OpenAI-compatible APIs only. Everything is in the repository: the server, one
harness per client, and the server log of every run, so you can rerun it with
no API key.

One more limit worth naming. The fix is in the parser, and the agent layer has
no check of its own: at `langchain-ai/langgraph` `main` on October 19, 2026,
`prebuilt/tool_node.py` contains no reference to `finish_reason` or
`invalid_tool_calls` — it runs whatever is in `message.tool_calls`. So a parser
fix closes this route, not every route. An agent that wants to refuse a cut-off
call also has to look at *why the model stopped*, not only at whether the
arguments parse.

Repository: https://github.com/simin-yuan/agent-cutoff-bench
