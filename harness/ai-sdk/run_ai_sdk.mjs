// Optional paired client. The existing fake server and fixtures are unchanged.
// node run_ai_sdk.mjs BASE_URL MODE {native|psj}
import { appendFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { stepCountIs, streamText, tool } from "ai";
import { createGuardedChat } from "psj-ai-sdk";
import { z } from "zod";

const packages = ["ai", "@ai-sdk/openai-compatible", "prefix-safe-json", "psj-ai-sdk", "zod"];
const shortError = (error) => `${error?.name ?? "Error"}: ${String(error?.message ?? error).slice(0, 240)}`;
function installedVersion(name) {
  let directory = dirname(fileURLToPath(import.meta.resolve(name)));
  while (dirname(directory) !== directory) {
    const manifest = join(directory, "package.json");
    if (existsSync(manifest)) {
      const metadata = JSON.parse(readFileSync(manifest, "utf8"));
      if (metadata.name === name) return metadata.version;
    }
    directory = dirname(directory);
  }
  throw new Error(`Cannot find installed manifest for ${name}`);
}

const versions = Object.fromEntries(packages.map((name) => [name, installedVersion(name)]));

export async function runClient(base, mode, policy) {
  const origin = new URL(base);
  if (origin.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(origin.hostname) ||
      !["complete", "length", "drop", "reset"].includes(mode) || !["native", "psj"].includes(policy)) {
    throw new Error("Use a loopback HTTP server, a bench mode, and native or psj.");
  }
  const directory = mkdtempSync(join(tmpdir(), "cutoff-ai-sdk-"));
  const calls = [];
  const decisions = [];
  const requests = [];
  const errors = [];
  const observedFetch = async (url, init) => {
    if (new URL(url).origin !== origin.origin) throw new Error("Unexpected non-bench request");
    const body = JSON.parse(init.body);
    requests.push({ tool_result_ids: (body.messages ?? []).filter((message) => message.role === "tool")
      .map((message) => message.tool_call_id) });
    return fetch(url, init);
  };
  const settings = { name: "cutoff-bench", baseURL: `${base}/${mode}/v1`, apiKey: "sk-bench", fetch: observedFetch };
  const options = {
    tools: { write_file: tool({
      description: "Write content to a file.",
      inputSchema: z.object({ path: z.string(), content: z.string() }),
      execute: async ({ path, content }, { toolCallId }) => {
        calls.push({ tool_call_id: toolCallId, path, content_chars: content.length });
        writeFileSync(join(directory, basename(path)), content, "utf8");
        return `Wrote ${content.length} characters to ${basename(path)}`;
      },
    }) },
    prompt: "Write the 40-line report to report.txt.",
    stopWhen: stepCountIs(3),
    abortSignal: AbortSignal.timeout(15_000),
    onError: ({ error }) => errors.push(shortError(error)),
  };
  const record = {
    run_id: process.env.BENCH_RUN_ID ?? `ai-sdk-${policy}-chat-stream-${mode}`,
    framework: policy === "psj" ? "ai-sdk-psj" : "ai-sdk",
    versions,
    node: process.version, platform: process.platform, arch: process.arch,
    mode, api: "chat", streaming: true, policy,
  };
  try {
    const result = policy === "psj"
      ? createGuardedChat({ ...settings, onDecision: (event) => decisions.push(event) })({ ...options, model: "bench-model" })
      : streamText({ ...options, model: createOpenAICompatible(settings).chatModel("bench-model"), maxRetries: 0 });
    for await (const part of result.fullStream) {
      if (part.type === "error" && !errors.includes(shortError(part.error))) errors.push(shortError(part.error));
    }
    record.final_answer = (await result.text).slice(0, 200);
    record.outcome = errors.length ? "stream-error" : "returned";
  } catch (error) {
    record.outcome = "raised";
    record.error = shortError(error);
  } finally {
    record.tool_executed = calls.length > 0;
    record.callback_count = calls.length;
    record.calls = calls;
    record.decisions = decisions;
    record.stream_errors = errors;
    record.requests = requests;
    const file = join(directory, "report.txt");
    if (existsSync(file)) {
      const data = readFileSync(file, "utf8");
      const last = data.replace(/\n$/, "").split("\n").at(-1);
      const lines = (data.match(/\n/g) ?? []).length;
      record.disk = { file_written: true, bytes: Buffer.byteLength(data), newline_terminated_lines: lines,
        last_line: last, complete: lines === 40 && last.startsWith("line 40") };
    } else record.disk = { file_written: false };
    assertTemporaryDirectory(directory, "cutoff-ai-sdk-");
    rmSync(directory, { recursive: true, force: true });
  }
  return record;
}

export function assertTemporaryDirectory(directory, prefix) {
  if (dirname(resolve(directory)) !== resolve(tmpdir()) || !basename(directory).startsWith(prefix)) {
    throw new Error("Unexpected temporary cleanup path");
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const record = await runClient(...process.argv.slice(2));
  const line = JSON.stringify(record);
  console.log(line);
  if (process.env.BENCH_RESULT) appendFileSync(process.env.BENCH_RESULT, line + "\n");
}
