// Eight sequential runs against the repository's unchanged Python server.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { appendFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { assertTemporaryDirectory, runClient } from "./run_ai_sdk.mjs";

const python = process.env.PY ?? (process.platform === "win32" ? "python" : "python3");
const serverFile = fileURLToPath(new URL("../../server.py", import.meta.url));
const directory = mkdtempSync(join(tmpdir(), "cutoff-verify-"));
let verified = 0;
try {
  for (const mode of ["complete", "length", "drop", "reset"]) {
    for (const policy of ["native", "psj"]) {
      const reservation = createServer();
      reservation.listen(0, "127.0.0.1");
      await once(reservation, "listening");
      const port = reservation.address().port;
      await new Promise((resolve, reject) => reservation.close((error) => error ? reject(error) : resolve()));
      const log = join(directory, `${mode}-${policy}.jsonl`);
      const server = spawn(python, [serverFile, String(port)], {
        env: { ...process.env, BENCH_LOG: log }, windowsHide: true, stdio: ["ignore", "ignore", "pipe"],
      });
      let serverError;
      server.on("error", (error) => { serverError = error; });
      const exited = new Promise((resolve) => server.once("close", resolve));
      let stderr = "";
      server.stderr.on("data", (data) => { stderr = (stderr + data).slice(-4_000); });
      const base = `http://127.0.0.1:${port}`;
      try {
        let ready = false;
        for (let attempt = 0; attempt < 50; attempt++) {
          if (serverError) throw serverError;
          if (server.exitCode !== null) throw new Error(`Bench server exited: ${stderr}`);
          try {
            ready = (await fetch(`${base}/v1/models`, { signal: AbortSignal.timeout(500) })).ok;
          } catch { /* The child may still be starting. */ }
          if (ready) break;
          await delay(100);
        }
        assert.ok(ready, `Python bench server did not start: ${stderr}`);
        const record = await runClient(base, mode, policy);
        const serverLog = readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line));
        record.server_requests = serverLog;
        const line = JSON.stringify(record);
        console.log(line);
        if (process.env.BENCH_RESULT) appendFileSync(process.env.BENCH_RESULT, line + "\n");
        const id = `${mode}/${policy}`;
        assert.equal(record.callback_count, mode === "complete" ? 1 : 0, `${id}: callbacks`);
        assert.equal(record.disk.file_written, mode === "complete", `${id}: disk write`);
        assert.equal(serverLog.length, record.requests.length, `${id}: observed request count`);
        assert.ok(serverLog.every((request) => request.mode === mode && request.endpoint === "chat" && request.stream),
          `${id}: unchanged server mode and streaming protocol`);
        if (mode === "complete") {
          assert.equal(record.outcome, "returned", `${id}: healthy outcome`);
          assert.equal(record.disk.complete, true, `${id}: full report`);
          assert.equal(record.disk.bytes, 2_120, `${id}: report bytes`);
          assert.equal(record.final_answer, "Done.", `${id}: final answer`);
          assert.deepEqual(record.calls.map((call) => call.tool_call_id), ["call_bench_1"], `${id}: original ID`);
          assert.equal(record.requests.length, 2, `${id}: continuation request`);
          assert.deepEqual(record.requests[1].tool_result_ids, ["call_bench_1"], `${id}: result ID`);
          assert.equal(serverLog[1].turn, "after_tool_result", `${id}: server saw result`);
          assert.equal(serverLog[1].tool_result_seen, "Wrote 2120 characters to report.txt", `${id}: result content`);
        } else if (policy === "psj") {
          assert.equal(record.requests.length, 1, `${id}: refusal sends no continuation`);
          assert.equal(record.decisions[0]?.status, "refused", `${id}: explicit refusal`);
          assert.equal(record.decisions[0]?.reason, mode === "reset" ? "transport_error" : "truncated", `${id}: refusal reason`);
          assert.equal(record.final_answer, undefined, `${id}: no false success answer`);
        } else if (mode === "reset") {
          assert.equal(record.requests.length, 1, `${id}: transport failure stops continuation`);
          assert.ok(record.error || record.stream_errors.length, `${id}: observable transport error`);
        } else {
          assert.equal(record.requests.length, 2, `${id}: native error-result continuation`);
          assert.deepEqual(record.requests[1].tool_result_ids, ["call_bench_1"], `${id}: error result retains original ID`);
          assert.match(serverLog[1].tool_result_seen, /AI_InvalidToolInputError/, `${id}: server saw validation error`);
          assert.equal(record.final_answer, "Done.", `${id}: fake server answers after the error result`);
          if (mode === "drop") assert.ok(record.stream_errors.some((error) => error.includes("without a finish reason")),
            `${id}: native missing-finish diagnostic is retained`);
        }
        verified++;
      } finally {
        if (server.exitCode === null) server.kill();
        await exited;
      }
    }
  }
  console.log(`Verified ${verified}/8 paired Chat Completions streaming cases.`);
} finally {
  assertTemporaryDirectory(directory, "cutoff-verify-");
  rmSync(directory, { recursive: true, force: true });
}
