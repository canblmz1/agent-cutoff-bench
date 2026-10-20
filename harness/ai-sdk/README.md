# Optional AI SDK / PSJ comparison

This separate npm consumer runs the repository's unchanged `server.py` in all
four modes, once with native AI SDK and once with `psj-ai-sdk@0.3.2`. It does
not change the LangChain harness, its dependencies, or the default matrix.

Requires Node 22 or later and Python 3. No Python packages or model credentials
are needed. From the repository root:

```sh
cd harness/ai-sdk
npm ci --ignore-scripts --strict-peer-deps --no-audit --no-fund
npm test
```

Set `PY` if Python has another executable name. Set `BENCH_RESULT` to append
the eight JSON records to a file. Each record includes exact npm versions,
callback count, disk content checks, original call/result IDs, client requests,
server observations and PSJ decision reasons. Every run starts a fresh server
and uses a fresh temporary directory; the harness removes its temporary files.
The healthy cases must write all 40 lines (2,120 bytes), run once with
`call_bench_1`, send its result back, and obtain `Done.`. The other modes must
write nothing and run no callback. PSJ refusal must send no continuation.

For one manual row, start the existing server from the repository root:

```sh
python server.py 8765
```

Then, in another terminal in this directory:

```sh
node run_ai_sdk.mjs http://127.0.0.1:8765 length native
node run_ai_sdk.mjs http://127.0.0.1:8765 length psj
```

## Interpretation and scope

Operator result on Windows 11 (build 26200), Node 22.23.2, npm 10.9.8 and
Python 3.12.10, with the exact versions in this directory's lockfile:

| Mode | Native callbacks / requests | PSJ callbacks / requests | Observed outcome |
|---|---|---|---|
| `complete` | 1 / 2 | 1 / 2 | Both write 2,120 bytes, retain the original ID, and receive `Done.` |
| `length` | 0 / 2 | 0 / 1 | Native sends an invalid-input result, then receives `Done.`; PSJ refuses as `truncated` |
| `drop` | 0 / 2 | 0 / 1 | Native reports missing finish reason and sends an invalid-input result, then receives `Done.`; PSJ refuses as `truncated` |
| `reset` | 0 / 1 | 0 / 1 | Native reports a transport error; PSJ refuses as `transport_error` |

All eight checks pass. The native row is essential: current AI SDK already
checks malformed JSON, tool schemas and completion. **There is no difference
in callback counts or disk writes on these fixtures.** The difference on
`length` and `drop` is refusal versus SDK-owned error-result continuation.
The fake server returns `Done.` after *any* tool result, including an error;
that is not a claim that the SDK mistakes a failed write for a successful one.
An application can inspect native tool errors instead of adding PSJ. These
authored fixtures do not measure how often truncation occurs in real traffic
or demonstrate package adoption. Linux results are not claimed here.

The [eight raw records](results/windows-20261020.jsonl) and
[clean-install receipt](results/validation-windows-20261020.json) preserve this
operator run. Re-run the command above to evaluate it independently.

The companion checks original response evidence before releasing tool bytes
to the SDK's ordinary execute callbacks and continuation. Its whole-response
policy differs from preserving a valid sibling beside an invalid call. This
bench has one call and cannot establish that the stricter group policy is
desirable. The repository's native Python fix remains an alternative and is
not described as an upstream-merged fix.

Only streaming OpenAI-compatible Chat Completions is evaluated here. The
companion is not a LangChain adapter and this directory does not fix LangChain,
test Responses API or non-streaming behavior, or provide authorization,
sandboxing, retries with exactly-once effects, or rollback guarantees.

For the maintainer: would you retain this optional paired row when upgrading
SDKs, and why? Running it here and deciding whether it is useful would be an
independent evaluation; the submitted implementation and its author's runs
are only preparation.
