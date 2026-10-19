"""Fake OpenAI-compatible model server that truncates a tool call.

Turn 1 (no tool result in the request): the "model" calls a file-writing tool
whose content is a 40-line file. Depending on the mode, the call arrives:

  complete  full arguments, finish_reason=tool_calls (control: proves we can
            observe execution)
  length    arguments cut in the middle of line 17, finish_reason=length
            (Responses API: status=incomplete, reason=max_output_tokens)
  drop      arguments cut in the middle of line 17, then the HTTP body ends
            cleanly: no finish_reason, no [DONE], no response.completed
  reset     arguments cut in the middle of line 17, then the TCP connection is
            closed without finishing the chunked body (transport error)

Turn 2+ (a tool result is present): the "model" answers "Done." and stops.

URL layout:  http://127.0.0.1:PORT/<mode>/v1/chat/completions
             http://127.0.0.1:PORT/<mode>/v1/responses
Every request is appended as one JSON line to the file named by $BENCH_LOG.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODES = ("complete", "length", "drop", "reset")
TARGET_NAME = "report.txt"
LINES = [f"line {i:02d}: the quick brown fox jumps over the lazy dog" for i in range(1, 41)]
CONTENT = "\n".join(LINES) + "\n"
CUT_LINE = 17  # truncation happens inside this line


def pick_tool(tool_names: list[str]) -> str:
    for name in ("write_file", "shell", "exec_command", "shell_command"):
        if name in tool_names:
            return name
    return tool_names[0] if tool_names else "write_file"


def full_args(tool: str, cwd_hint: str | None) -> str:
    if tool in ("shell", "exec_command", "shell_command"):
        script = f"cat > {TARGET_NAME} <<'EOF'\n{CONTENT}EOF"
        if tool == "shell":
            return json.dumps({"command": ["bash", "-lc", script]})
        return json.dumps({"cmd": script} if tool == "exec_command" else {"command": script})
    return json.dumps({"path": TARGET_NAME, "content": CONTENT})


def cut_args(args: str) -> str:
    """Cut the serialized arguments in the middle of line CUT_LINE."""
    marker = f"line {CUT_LINE:02d}: the quick"
    i = args.index(marker)
    return args[: i + len(marker)]


def pieces(s: str, n: int = 24) -> list[str]:
    return [s[i : i + n] for i in range(0, len(s), n)]


def has_tool_result(body: dict) -> bool:
    for m in body.get("messages", []) or []:
        if m.get("role") == "tool":
            return True
    inp = body.get("input")
    if isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict) and item.get("type") in (
                "function_call_output", "custom_tool_call_output", "local_shell_call_output"):
                return True
    return False


def tool_names_of(body: dict) -> list[str]:
    names = []
    for t in body.get("tools", []) or []:
        if "function" in t and isinstance(t["function"], dict):
            names.append(t["function"].get("name"))
        elif "name" in t:
            names.append(t["name"])
        elif t.get("type"):
            names.append(t["type"])
    return [n for n in names if n]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    # ---------- plumbing ----------
    def _log(self, rec: dict) -> None:
        path = os.environ.get("BENCH_LOG")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def _start_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, data: str) -> None:
        b = data.encode()
        self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
        self.wfile.flush()

    def _end_body(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _hard_close(self) -> None:
        try:
            self.wfile.flush()
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        except OSError:
            pass
        self.connection.close()
        self.close_connection = True

    def _json(self, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _truncated_json(self, obj: dict) -> None:
        """Non-streaming 'drop'/'reset': promise a body, send half, close."""
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b[: len(b) // 2])
        self._hard_close()

    # ---------- routing ----------
    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json({"object": "list", "data": [{"id": "bench-model", "object": "model"}]})
        self.send_error(404)

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        mode = parts[0] if parts and parts[0] in MODES else "complete"
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        endpoint = "responses" if self.path.endswith("/responses") else (
            "chat" if self.path.endswith("/chat/completions") else "other")
        stream = bool(body.get("stream"))
        tools = tool_names_of(body)
        follow_up = has_tool_result(body)
        tool = pick_tool(tools)
        rec = {
            "t": round(time.time(), 3), "mode": mode, "endpoint": endpoint, "stream": stream,
            "turn": "after_tool_result" if follow_up else "first",
            "tools_offered": tools, "tool_called": None if follow_up else tool,
            "max_tokens_sent": {k: body[k] for k in ("max_tokens", "max_completion_tokens", "max_output_tokens") if k in body},
        }
        if follow_up:
            rec["tool_result_seen"] = self._extract_tool_result(body)
        self._log(rec)
        if endpoint == "chat":
            return self._chat(mode, stream, follow_up, tool)
        if endpoint == "responses":
            return self._responses(mode, stream, follow_up, tool)
        self.send_error(404)

    @staticmethod
    def _extract_tool_result(body: dict) -> str | None:
        for m in body.get("messages", []) or []:
            if m.get("role") == "tool":
                c = m.get("content")
                return (c if isinstance(c, str) else json.dumps(c))[:300]
        inp = body.get("input")
        if isinstance(inp, list):
            for item in inp:
                if isinstance(item, dict) and item.get("type", "").endswith("_output"):
                    o = item.get("output")
                    return (o if isinstance(o, str) else json.dumps(o))[:300]
        return None

    # ---------- Chat Completions ----------
    def _chat(self, mode, stream, follow_up, tool):
        base = {"id": "chatcmpl-bench", "object": "chat.completion", "created": int(time.time()), "model": "bench-model"}
        if follow_up:
            if not stream:
                return self._json({**base, "choices": [{"index": 0, "finish_reason": "stop",
                                   "message": {"role": "assistant", "content": "Done."}}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})
            self._start_stream()
            ck = {**base, "object": "chat.completion.chunk"}
            self._chunk("data: " + json.dumps({**ck, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Done."}, "finish_reason": None}]}) + "\n\n")
            self._chunk("data: " + json.dumps({**ck, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n")
            self._chunk("data: [DONE]\n\n")
            return self._end_body()

        args = full_args(tool, None)
        sent = args if mode == "complete" else cut_args(args)
        call = {"id": "call_bench_1", "type": "function", "function": {"name": tool, "arguments": sent}}
        finish = "tool_calls" if mode == "complete" else "length"
        if not stream:
            msg = {**base, "choices": [{"index": 0, "finish_reason": finish,
                   "message": {"role": "assistant", "content": None, "tool_calls": [call]}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 500, "total_tokens": 510}}
            if mode in ("drop", "reset"):
                return self._truncated_json(msg)
            return self._json(msg)

        self._start_stream()
        ck = {**base, "object": "chat.completion.chunk"}
        first = {"index": 0, "id": "call_bench_1", "type": "function", "function": {"name": tool, "arguments": ""}}
        self._chunk("data: " + json.dumps({**ck, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [first]}, "finish_reason": None}]}) + "\n\n")
        for p in pieces(sent):
            self._chunk("data: " + json.dumps({**ck, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": p}}]}, "finish_reason": None}]}) + "\n\n")
        if mode in ("complete", "length"):
            self._chunk("data: " + json.dumps({**ck, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}) + "\n\n")
            self._chunk("data: [DONE]\n\n")
            return self._end_body()
        if mode == "drop":
            return self._end_body()  # body ends cleanly, no finish_reason, no [DONE]
        return self._hard_close()  # reset

    # ---------- Responses ----------
    def _responses(self, mode, stream, follow_up, tool):
        rid = "resp_bench"
        base = {"id": rid, "object": "response", "created_at": int(time.time()), "model": "bench-model",
                "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "output": []}
        usage = {"input_tokens": 10, "output_tokens": 500, "total_tokens": 510,
                 "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
        if follow_up:
            item = {"type": "message", "id": "msg_bench", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "Done.", "annotations": []}]}
            done = {**base, "status": "completed", "output": [item], "usage": usage}
            if not stream:
                return self._json(done)
            self._start_stream()
            ev = lambda t, d: self._chunk(f"event: {t}\ndata: " + json.dumps({"type": t, **d}) + "\n\n")
            ev("response.created", {"response": {**base, "status": "in_progress"}})
            ev("response.output_item.added", {"output_index": 0, "item": {**item, "status": "in_progress", "content": []}})
            ev("response.content_part.added", {"output_index": 0, "item_id": "msg_bench", "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
            ev("response.output_text.delta", {"output_index": 0, "item_id": "msg_bench", "content_index": 0, "delta": "Done."})
            ev("response.output_text.done", {"output_index": 0, "item_id": "msg_bench", "content_index": 0, "text": "Done."})
            ev("response.content_part.done", {"output_index": 0, "item_id": "msg_bench", "content_index": 0, "part": item["content"][0]})
            ev("response.output_item.done", {"output_index": 0, "item": item})
            ev("response.completed", {"response": done})
            return self._end_body()

        args = full_args(tool, None)
        sent = args if mode == "complete" else cut_args(args)
        fc = {"type": "function_call", "id": "fc_bench_1", "call_id": "call_bench_1", "name": tool,
              "arguments": sent, "status": "completed" if mode == "complete" else "incomplete"}
        if mode == "complete":
            final = {**base, "status": "completed", "output": [fc], "usage": usage}
        else:
            final = {**base, "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                     "output": [fc], "usage": usage}
        if not stream:
            if mode in ("drop", "reset"):
                return self._truncated_json(final)
            return self._json(final)

        self._start_stream()
        ev = lambda t, d: self._chunk(f"event: {t}\ndata: " + json.dumps({"type": t, **d}) + "\n\n")
        ev("response.created", {"response": {**base, "status": "in_progress"}})
        ev("response.output_item.added", {"output_index": 0, "item": {**fc, "arguments": "", "status": "in_progress"}})
        for p in pieces(sent):
            ev("response.function_call_arguments.delta", {"output_index": 0, "item_id": "fc_bench_1", "delta": p})
        if mode == "complete":
            ev("response.function_call_arguments.done", {"output_index": 0, "item_id": "fc_bench_1", "arguments": sent})
            ev("response.output_item.done", {"output_index": 0, "item": fc})
            ev("response.completed", {"response": final})
            return self._end_body()
        if mode == "length":
            ev("response.incomplete", {"response": final})
            return self._end_body()
        if mode == "drop":
            return self._end_body()
        return self._hard_close()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"bench server on 127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
