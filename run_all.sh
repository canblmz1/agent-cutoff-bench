#!/usr/bin/env bash
# Re-run every framework x API x streaming x mode combination.
# Each run gets its own fresh server and its own server log:
#   $OUT_DIR/runs.jsonl            one line per run (what the client did)
#   $OUT_DIR/logs/<run-id>.jsonl   every HTTP request that run made (what the server saw)
#
# Env:
#   PY       python that has requirements.txt installed   (default: python3)
#   OUT_DIR  where to write results                       (default: ./results-local)
#   CODEX    path to a codex binary   (optional; Codex rows are skipped if unset)
#   HERMES   path to a hermes binary  (optional; Hermes rows are skipped if unset)
# The LangGraph.js rows need `npm ci` (or `npm install`) in harness/js first.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-python3}"
out="${OUT_DIR:-$here/results-local}"
mkdir -p "$out/logs"; out="$(cd "$out" && pwd)"; : > "$out/runs.jsonl"
port=${BASE_PORT:-9100}
modes="complete length drop reset"

run() {  # run <id> <command ...>   (the base URL is appended as the last argument)
  local id="$1"; shift
  port=$((port+1))
  BENCH_LOG="$out/logs/$id.jsonl" "$PY" "$here/server.py" "$port" >/dev/null 2>&1 &
  local spid=$!
  sleep 0.7
  BENCH_RESULT="$out/runs.jsonl" BENCH_RUN_ID="$id" "$@" "http://127.0.0.1:$port" >/dev/null 2>&1 || true
  kill "$spid" 2>/dev/null; wait "$spid" 2>/dev/null
  echo "done $id"
}

h="$here/harness"
for api in chat responses; do for s in stream nostream; do for m in $modes; do
  run "langgraph-$api-$s-$m"     bash -c 'cd "$0" && "$1" run_langgraph.py "$5" "$2" "$3" "$4"' "$h" "$PY" "$m" "$api" "$s"
  run "openai-agents-$api-$s-$m" bash -c 'cd "$0" && "$1" run_openai_agents.py "$5" "$2" "$3" "$4"' "$h" "$PY" "$m" "$api" "$s"
  if [ -d "$h/js/node_modules" ]; then
    run "langgraph-js-$api-$s-$m" bash -c 'cd "$0" && node run_langgraphjs.mjs "$4" "$1" "$2" "$3"' "$h/js" "$m" "$api" "$s"
  fi
done; done; done
for s in stream nostream; do for m in $modes; do
  run "smolagents-chat-$s-$m" bash -c 'cd "$0" && "$1" run_smolagents.py "$4" "$2" "$3"' "$h" "$PY" "$m" "$s"
done; done
if [ -n "${CODEX:-}" ]; then for m in $modes; do
  run "codex-responses-stream-$m" bash -c '"$0" "$3" "$1" "$2"' "$h/run_codex.sh" "$m" "$CODEX"
done; fi
if [ -n "${HERMES:-}" ]; then for m in $modes; do
  run "hermes-chat-stream-$m" bash -c '"$0" "$3" "$1" "$2"' "$h/run_hermes.sh" "$m" "$HERMES"
done; fi
echo "runs: $(wc -l < "$out/runs.jsonl")"
"$PY" "$here/summarize.py" "$out/runs.jsonl"
