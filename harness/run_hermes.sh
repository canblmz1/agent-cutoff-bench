#!/usr/bin/env bash
# Hermes Agent (control) against the fake server: custom OpenAI-compatible
# endpoint (Chat Completions, streaming), one-shot mode.
# usage: run_hermes.sh BASE_URL MODE HERMES_BIN
set -u
base="$1"; mode="$2"; bin="$3"
home="$(mktemp -d)"; work="$(mktemp -d)"
cat > "$home/config.yaml" <<EOF
model:
  default: bench-model
  provider: custom
  base_url: $base/$mode/v1
  api_key: sk-bench
  context_length: 64000
EOF
out="$(cd "$work" && HERMES_HOME="$home" timeout 180 "$bin" --yolo --ignore-rules -t file \
  -z "Write the 40-line report to report.txt in the current directory." 2>&1)"
rc=$?
ver="$(git -C "$(dirname "$bin")/../.." rev-parse --short=9 HEAD 2>/dev/null || echo unknown)"
OUT="$out" python3 - "$work" "$mode" "$rc" "$ver" <<'PY'
import json, os, sys
from pathlib import Path
work, mode, rc, ver = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
p = Path(work) / "report.txt"
if p.exists():
    data = p.read_text()
    last = data.rstrip("\n").splitlines()[-1]
    disk = {"file_written": True, "bytes": len(data.encode()), "newline_terminated_lines": data.count("\n"),
            "last_line": last, "complete": data.count("\n") == 40 and last.startswith("line 40")}
else:
    disk = {"file_written": False}
rec = {"run_id": os.environ.get("BENCH_RUN_ID"), "framework": "hermes-agent", "versions": {"hermes-agent": ver}, "mode": mode, "api": "chat",
       "streaming": True, "exit_code": rc, "tool_executed": disk["file_written"], "disk": disk,
       "output_tail": os.environ.get("OUT", "")[-600:]}
line = json.dumps(rec)
print(line)
if os.environ.get("BENCH_RESULT"):
    open(os.environ["BENCH_RESULT"], "a").write(line + "\n")
PY
