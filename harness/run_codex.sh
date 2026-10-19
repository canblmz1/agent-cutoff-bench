#!/usr/bin/env bash
# Codex CLI (codex exec) against the fake server, Responses API wire format.
# usage: run_codex.sh BASE_URL MODE CODEX_BIN
set -u
base="$1"; mode="$2"; bin="$3"
home="$(mktemp -d)"; work="$(mktemp -d)"
cat > "$home/config.toml" <<EOF
model = "bench-model"
model_provider = "bench"
[model_providers.bench]
name = "bench"
base_url = "$base/$mode/v1"
wire_api = "responses"
env_key = "BENCH_KEY"
request_max_retries = 0
stream_max_retries = 1
EOF
out="$(cd "$work" && BENCH_KEY=sk-bench CODEX_HOME="$home" timeout 90 "$bin" exec --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox "Write the 40-line report to report.txt." 2>&1)"
rc=$?
ver="$("$bin" --version)"
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
rec = {"run_id": os.environ.get("BENCH_RUN_ID"), "framework": "codex-cli", "versions": {"codex": ver}, "mode": mode, "api": "responses",
       "streaming": True, "exit_code": rc, "tool_executed": disk["file_written"], "disk": disk,
       "output_tail": os.environ.get("OUT", "")[-600:]}
line = json.dumps(rec)
print(line)
if os.environ.get("BENCH_RESULT"):
    open(os.environ["BENCH_RESULT"], "a").write(line + "\n")
PY
