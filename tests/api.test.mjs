import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../web/api.js", import.meta.url), "utf8");

async function loadApi(readBody, contentType = "application/json") {
  const timers = new Map();
  const calls = [];
  let nextTimer = 0;
  const context = vm.createContext({
    AbortController, DOMException,
    window: {
      setTimeout: (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
      clearTimeout: (id) => timers.delete(id),
    },
    fetch: async (url, options) => {
      calls.push({ url, ...options });
      return {
        ok: true,
        headers: { get: () => contentType },
        json: readBody,
        blob: readBody,
      };
    },
  });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => { throw new Error("Unexpected dependency"); });
  await module.evaluate();
  return { api: module.namespace, timers, calls };
}

async function settle() {
  for (let turn = 0; turn < 3; turn += 1) await new Promise(setImmediate);
}

const pendingBody = () => new Promise(() => {});
const requests = [
  { name: "JSON", call: (api, options) => api.fetchJson("/api/profile", options) },
  { name: "录音识别 JSON", call: (api, options) => api.postWav("/api/asr", new ArrayBuffer(44), options) },
  { name: "语音合成 JSON", call: (api, options) => api.postSpeech("/api/tts", { text: "测试" }, options) },
  { name: "语音合成音频", contentType: "audio/wav", call: (api, options) => api.postSpeech("/api/tts", { text: "测试" }, options) },
];

for (const request of requests) {
  test(`${request.name} 响应头到达但响应体一直等待时，超时仍会结束请求`, { timeout: 2000 }, async () => {
    const page = await loadApi(pendingBody, request.contentType);
    const result = request.call(page.api, { timeoutMs: 1234 });
    const rejection = assert.rejects(result, { name: "AbortError" });
    await settle();
    assert.equal(page.calls.length, 1);
    assert.equal(page.timers.size, 1, "读取响应体期间必须保留超时");
    const [id, timer] = [...page.timers][0];
    assert.equal(timer.delay, 1234);
    page.timers.delete(id);
    timer.callback();
    await rejection;
    assert.equal(page.calls[0].signal.aborted, true);
    assert.equal(page.timers.size, 0);
  });
}

test("响应体读取期间外部取消立即结束等待，并清理定时器", { timeout: 2000 }, async () => {
  const page = await loadApi(pendingBody, "audio/wav");
  const controller = new AbortController();
  const result = page.api.postSpeech("/api/tts", { text: "测试" }, { signal: controller.signal });
  const rejection = assert.rejects(result, { name: "AbortError" });
  await settle();
  controller.abort();
  await rejection;
  assert.equal(page.calls[0].signal.aborted, true);
  assert.equal(page.timers.size, 0);
});

test("调用前已经取消不会开始网络请求", { timeout: 2000 }, async () => {
  const page = await loadApi(async () => ({}));
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(page.api.fetchJson("/api/profile", { signal: controller.signal }), { name: "AbortError" });
  assert.equal(page.calls.length, 0);
  assert.equal(page.timers.size, 0);
});

test("读取成功后清理计时器与外部取消监听", { timeout: 2000 }, async () => {
  const page = await loadApi(async () => ({ ok: true }));
  const controller = new AbortController();
  const result = await page.api.fetchJson("/api/health", { signal: controller.signal });
  assert.equal(result.ok, true);
  assert.equal(page.timers.size, 0);
  controller.abort();
  assert.equal(page.calls[0].signal.aborted, false, "完成后的外部取消不得再影响该请求");
});
