"""Shared helpers for the Python harnesses."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

TARGET = "report.txt"
EXPECTED_LINES = 40


def workdir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="cutoff-"))
    return d


def disk_state(d: Path) -> dict:
    p = d / TARGET
    if not p.exists():
        return {"file_written": False}
    data = p.read_text(encoding="utf-8")
    last = data.rstrip("\n").splitlines()[-1] if data.strip() else ""
    return {
        "file_written": True,
        "bytes": len(data.encode()),
        "newline_terminated_lines": data.count("\n"),
        "last_line": last,
        "complete": data.count("\n") == EXPECTED_LINES and last.startswith("line 40"),
    }


def emit(rec: dict) -> None:
    rec = {"run_id": os.environ.get("BENCH_RUN_ID"), **rec}
    out = os.environ.get("BENCH_RESULT")
    line = json.dumps(rec, ensure_ascii=False)
    print(line)
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def safe_write(d: Path, path: str, content: str) -> str:
    """The only side effect the tool has: write one file inside d."""
    p = (d / Path(path).name)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {p.name}"


def short_exc(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e)[:240]}"
