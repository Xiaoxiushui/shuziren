import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";

const profile = JSON.parse(await readFile(new URL("../profiles/demo.json", import.meta.url), "utf8"));

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.text = "";
  }
  set textContent(value) { this.text = value; this.replaceChildren(); }
  get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); }
  append(...children) {
    for (const child of children) { child.parent = this; this.children.push(child); }
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    this.append(...children);
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  emit(type) { return this.listeners.get(type)?.({ preventDefault() {} }); }
  querySelectorAll(tag) {
    return this.children.flatMap((child) => [
      ...(child.tagName === tag ? [child] : []), ...child.querySelectorAll(tag),
    ]);
  }
  querySelector(tag) { return this.querySelectorAll(tag)[0] || null; }
  setAttribute(key, value) { this.attributes.set(key, value); }
  focus() { this.focused = true; }
}

function response(body, status = 200, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    json: async () => body,
    blob: async () => body,
  };
}

// 模拟浏览器录音：AudioContext 只保留可以手动触发的 ScriptProcessor。
class FakeAudioContext {
  constructor(owner) {
    this.sampleRate = 48000;
    this.destination = { name: "destination" };
    this.processors = [];
    this.closed = false;
    owner.audioContexts.push(this);
  }
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  createScriptProcessor() {
    const processor = { onaudioprocess: null, connect() {}, disconnect() {} };
    this.processors.push(processor);
    return processor;
  }
  createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
  close() { this.closed = true; return Promise.resolve(); }
  resume() { this.resumed = true; }
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function waitForAbort(signal) {
  return new Promise((resolve, reject) => {
    if (!signal) return;
    if (signal.aborted) reject(new DOMException("Aborted", "AbortError"));
    else signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  });
}

async function settle() {
  for (let turn = 0; turn < 5; turn += 1) await new Promise(setImmediate);
}

async function loadPage({ entry = "app.js", chat, health, getProfile, people, search = "", asr, tts, media, storage } = {}) {
  const store = storage ?? new Map();
  const sessionStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
  const nodes = Object.fromEntries([
    "messages", "composer", "question", "status", "suggestions", "reset-button", "retry-profile",
    "profile-name", "profile-intro", "portrait", "chat-title", "people-list", "retry-people", "availability",
    "mic-button", "mic-cancel", "recording-bar", "voice-notice",
  ].map((id) => [id, new Element()]));
  const submit = new Element("button");
  nodes["send-button"] = submit;
  // 与 index.html 一致：录音与发送都是 <button>，且初始都是 disabled，加载成功后由脚本打开
  nodes["mic-button"] = new Element("button");
  nodes.question.disabled = true;
  nodes["mic-button"].disabled = true;
  submit.disabled = true;
  nodes.composer.append(nodes.question, nodes["mic-button"], submit);
  const calls = [];
  const timers = new Map();
  const media2 = { audioContexts: [], audios: [], objects: [], revoked: [], spoken: [], cancelled: 0, constraints: [], streams: [] };
  let nextTimer = 0;
  const getUserMedia = async (constraints) => {
    media2.constraints.push(constraints);
    if (media?.reject) throw media.reject;
    if (media?.permission) return media.permission;
    const stream = { getTracks: () => [{ stop() { stream.stopped = true; } }] };
    media2.streams.push(stream);
    return stream;
  };
  const urlApi = {
    createObjectURL: (blob) => { media2.objects.push(blob); return `blob:test/${media2.objects.length}`; },
    revokeObjectURL: (url) => media2.revoked.push(url),
  };
  const UtteranceClass = class {
    constructor(text) { this.text = text; }
  };
  const context = vm.createContext({
    AbortController, DOMException, URLSearchParams,
    navigator: { userAgent: media?.userAgent || "Test", mediaDevices: { getUserMedia } },
    SpeechSynthesisUtterance: UtteranceClass,
    console: { warn() {} },
    document: {
      querySelector: (selector) => nodes[selector.slice(1)],
      createElement: (tag) => new Element(tag),
    },
    window: {
      location: { search },
      sessionStorage,
      localStorage: {
        getItem: () => { if (media?.storageBlocked) throw new Error("storage denied"); return null; },
        setItem: () => { if (media?.storageBlocked) throw new Error("storage denied"); },
      },
      addEventListener() {},
      setTimeout: (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
      clearTimeout: (id) => timers.delete(id),
      AudioContext: class extends FakeAudioContext { constructor() { super(media2); } },
      Audio: class {
        constructor(src) { this.src = src; this.paused = false; this.played = 0; media2.audios.push(this); }
        play() { this.played += 1; return media?.play ? media.play(this) : Promise.resolve(); }
        pause() { this.paused = true; }
      },
      URL: urlApi,
      speechSynthesis: {
        getVoices: () => [],
        speak: (utterance) => { media2.spoken.push(utterance); },
        cancel: () => { media2.cancelled += 1; },
      },
      SpeechSynthesisUtterance: UtteranceClass,
    },
    fetch: async (url, options = {}) => {
      const raw = options.body;
      const call = { url, ...options, body: typeof raw === "string" ? JSON.parse(raw) : raw };
      calls.push(call);
      if (url === "/api/health") return health ? health(call) : response({ ok: true, mode: "demo" });
      if (url.startsWith("/api/profile?")) return getProfile ? getProfile(call) : response(profile);
      if (url === "/api/chat") return chat ? chat(call) : response({ ok: true, reply: "已审核的回答", mode: "demo" });
      if (url === "/api/profiles") return people ? people(call) : response({ ok: true, profiles: [profile] });
      if (url === "/api/asr") return asr ? asr(call) : response({ ok: true, text: "北京的天气。" });
      if (url === "/api/tts") {
        return tts ? tts(call) : response({ ok: true, mode: "browser", source: "browser_speech", fallback: true });
      }
      throw new Error(`Unexpected URL: ${url}`);
    },
  });
  if (media?.recording === false) delete context.navigator.mediaDevices;
  if (media?.speech === false) {
    delete context.window.speechSynthesis;
    delete context.window.SpeechSynthesisUtterance;
  }
  const moduleCache = new Map();
  async function loadModule(name) {
    if (moduleCache.has(name)) return moduleCache.get(name);
    const source = await readFile(new URL(`../web/${name}`, import.meta.url), "utf8");
    const module = new vm.SourceTextModule(source, { context, identifier: name });
    moduleCache.set(name, module);
    await module.link((specifier) => loadModule(specifier.replace(/^\.\//, "")));
    return module;
  }
  await (await loadModule(entry)).evaluate();
  await settle();
  return {
    nodes, submit, calls, timers, media: media2, store,
    async send(text) { nodes.question.value = text; nodes.composer.emit("submit"); await settle(); },
    async reset() { nodes["reset-button"].emit("click"); await settle(); },
    async expire(delay) {
      const due = [...timers.entries()].filter(([, timer]) => timer.delay === delay);
      assert.ok(due.length, `Expected a ${delay}ms request timeout`);
      for (const [id, timer] of due) { timers.delete(id); timer.callback(); }
      await settle();
    },
  };
}

test("重开页面后恢复上次对话，并继续带着上下文提问", async () => {
  const store = new Map();
  const first = await loadPage({ storage: store });
  await first.send("我最近天天加班");
  const second = await loadPage({ storage: store });
  const rendered = second.nodes.messages.children.map((item) => item.textContent);
  assert.ok(rendered.some((text) => text.includes("我最近天天加班")), "历史消息应重新显示");
  await second.send("你还记得我刚才说了什么吗");
  const payload = second.calls.find((call) => call.url === "/api/chat").body;
  assert.equal(payload.history.length, 2);
  assert.equal(payload.history[0].content, "我最近天天加班");
});

test("点重新开始会清掉本地保存的对话", async () => {
  const store = new Map();
  const first = await loadPage({ storage: store });
  await first.send("记一下这个");
  assert.ok(store.size > 0);
  await first.reset();
  assert.equal(store.size, 0);
  const second = await loadPage({ storage: store });
  assert.equal(second.nodes.messages.children.some((item) => item.textContent.includes("记一下这个")), false);
});

test("加载完成后发送按钮可用，录音按钮状态互不影响", async () => {
  const page = await loadPage();
  assert.equal(page.nodes["send-button"].disabled, false);
  assert.equal(page.nodes["mic-button"].disabled, false);
});

test("重新开始后下一次模型请求不携带旧对话，并清空草稿", async () => {
  const page = await loadPage();
  await page.send("第一个问题");
  await page.send("第二个问题");
  assert.equal(page.calls.filter((call) => call.url === "/api/chat")[1].body.history.length, 2);
  page.nodes.question.value = "未发送的草稿";
  await page.reset();
  assert.equal(page.nodes.question.value, "");
  await page.send("新话题");
  assert.deepEqual(page.calls.filter((call) => call.url === "/api/chat")[2].body.history, []);
});

test("等待中重置会取消请求，迟到的回复不会污染新对话", async () => {
  const oldReply = deferred();
  let count = 0;
  const page = await loadPage({ chat: () => ++count === 1 ? oldReply.promise : response({ ok: true, reply: "新的回答", mode: "demo" }) });
  await page.send("旧问题");
  assert.equal(page.submit.disabled, true);
  const oldCall = page.calls.find((call) => call.url === "/api/chat");
  await page.reset();
  assert.equal(oldCall.signal?.aborted, true);
  await page.send("新问题");
  oldReply.resolve(response({ ok: true, reply: "迟到的旧回答", mode: "demo" }));
  await settle();
  assert.ok(page.nodes.messages.textContent.includes("新的回答"));
  assert.ok(!page.nodes.messages.textContent.includes("迟到的旧回答"));
  assert.equal(page.submit.disabled, false);
  assert.equal(page.timers.size, 0);
});

test("对话超时后使用当前人物的审核回答，并恢复发送", async () => {
  const page = await loadPage({ chat: (call) => waitForAbort(call.signal) });
  await page.send("你喜欢做什么？");
  assert.equal(page.submit.disabled, true);
  await page.expire(25000);
  assert.ok(page.nodes.messages.textContent.includes(profile.answers[1].text));
  assert.ok(page.nodes.status.textContent.includes("回退"));
  assert.equal(page.submit.disabled, false);
  assert.equal(page.timers.size, 0);
});

test("人物接口返回不可用后停止本地代答，重新加载后可恢复", async () => {
  const page = await loadPage({ chat: () => response({ ok: false, error: "profile_unavailable" }, 503) });
  await page.send("你喜欢做什么？");
  assert.equal(page.submit.disabled, true);
  assert.equal(page.nodes["retry-profile"].hidden, false);
  assert.equal(page.nodes.availability.hidden, true);
  assert.ok(!page.nodes.messages.textContent.includes(profile.answers[1].text));
  page.nodes["retry-profile"].emit("click");
  await settle();
  assert.equal(page.submit.disabled, false);
  assert.equal(page.nodes["profile-name"].textContent, profile.name);
});

test("真实人物无法确认资料状态时不会使用浏览器缓存代答", async () => {
  const page = await loadPage({
    getProfile: () => response({ ...profile, kind: "real" }),
    chat: (call) => waitForAbort(call.signal),
  });
  await page.send("你喜欢做什么？");
  await page.expire(25000);
  assert.equal(page.submit.disabled, true);
  assert.equal(page.nodes["retry-profile"].hidden, false);
  assert.ok(!page.nodes.messages.textContent.includes(profile.answers[1].text));
});

test("健康检查超时不会卡住初始化，加载完成前禁止发问", async () => {
  const page = await loadPage({ health: (call) => waitForAbort(call.signal) });
  assert.equal(page.submit.disabled, true);
  await page.send("加载中不能发送");
  assert.equal(page.calls.filter((call) => call.url === "/api/chat").length, 0);
  await page.expire(4000);
  assert.equal(page.nodes["profile-name"].textContent, profile.name);
  assert.equal(page.submit.disabled, false);
  assert.equal(page.timers.size, 0);
});

test("人物资料不可用时禁止空身份对话，重试成功后恢复", async () => {
  let available = false;
  const page = await loadPage({ getProfile: () => response(available ? profile : {}, available ? 200 : 503) });
  assert.equal(page.submit.disabled, true);
  assert.equal(page.nodes["retry-profile"].hidden, false);
  assert.equal(page.nodes.availability.hidden, true);
  assert.equal(page.nodes.messages.querySelectorAll("button").length, 0);
  await page.send("不能冒用默认人物");
  assert.equal(page.calls.filter((call) => call.url === "/api/chat").length, 0);
  available = true;
  page.nodes["retry-profile"].emit("click");
  await settle();
  assert.equal(page.nodes["retry-profile"].hidden, true);
  assert.equal(page.submit.disabled, false);
  assert.ok(page.nodes.messages.textContent.includes(profile.welcome));
  assert.equal(page.nodes.availability.hidden, false);
});

test("指定人物服务故障时不会改用另一位人物，仅不存在时才明确回退", async () => {
  const failed = await loadPage({
    search: "?profile=another-demo",
    getProfile: (call) => call.url.includes("another-demo") ? response({}, 503) : response(profile),
  });
  assert.equal(failed.submit.disabled, true);
  assert.equal(failed.calls.filter((call) => call.url.includes("id=lin-shu-demo")).length, 0);
  const missing = await loadPage({
    search: "?profile=missing-demo",
    getProfile: (call) => call.url.includes("missing-demo") ? response({}, 404) : response(profile),
  });
  assert.equal(missing.submit.disabled, false);
  assert.equal(missing.nodes["profile-name"].textContent, profile.name);
  assert.ok(missing.nodes.status.textContent.includes("已切换到默认人物"));
});

test("连续对话只保留最近四轮上下文", async () => {
  const page = await loadPage();
  for (let index = 0; index < 6; index += 1) await page.send(`问题${index}`);
  const chats = page.calls.filter((call) => call.url === "/api/chat");
  assert.equal(chats[5].body.history.length, 8);
  assert.equal(chats[5].body.history[0].content, "问题1");
});

test("人物列表请求超时后可直接重试，不用刷新整个页面", async () => {
  let stalled = true;
  const page = await loadPage({ entry: "people.js", people: (call) => stalled ? waitForAbort(call.signal) : response({ ok: true, profiles: [profile] }) });
  await page.expire(8000);
  assert.ok(page.nodes["people-list"].textContent.includes("加载失败"));
  assert.equal(page.nodes["retry-people"].hidden, false);
  stalled = false;
  page.nodes["retry-people"].emit("click");
  await settle();
  assert.equal(page.nodes["retry-people"].hidden, true);
  assert.ok(page.nodes["people-list"].textContent.includes(profile.name));
  assert.equal(page.nodes["people-list"].children[0].href, "./index.html?profile=lin-shu-demo");
  assert.equal(page.timers.size, 0);
});
async function startRecording(page) {
  page.nodes["mic-button"].emit("click");
  await settle();
}

function feedAudio(page, seconds = 1, rate = 48000) {
  const chunk = new Float32Array(Math.round(rate * seconds));
  page.media.audioContexts[0].processors[0].onaudioprocess({ inputBuffer: { getChannelData: () => chunk } });
}

test("录音后把识别文字填进输入框，不自动发送", async () => {
  const page = await loadPage({ asr: () => response({ ok: true, text: " 北京的天气。 " }) });
  await startRecording(page);
  assert.equal(page.nodes["mic-button"].textContent, "停止");
  // 录音中按钮必须可点，否则用户无法"再点一次"结束录音（曾因为一律禁用而卡死）
  assert.equal(page.nodes["mic-button"].disabled, false);
  assert.equal(page.nodes["recording-bar"].hidden, false);
  assert.equal(page.media.constraints[0].audio.sampleRate, 16000);
  feedAudio(page);
  page.nodes["mic-button"].emit("click");
  await settle();
  const call = page.calls.find((item) => item.url === "/api/asr");
  assert.equal(call.headers["Content-Type"], "audio/wav");
  assert.equal(String.fromCharCode(...new Uint8Array(call.body, 0, 4)), "RIFF");
  const view = new DataView(call.body);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), call.body.byteLength - 44);
  assert.equal(page.nodes.question.value, "北京的天气。");
  assert.equal(page.calls.filter((item) => item.url === "/api/chat").length, 0);
  assert.equal(page.nodes["mic-button"].textContent, "录音");
  assert.equal(page.nodes["recording-bar"].hidden, true);
  assert.equal(page.timers.size, 0);
});

test("麦克风权限被拒绝时给出可读提示且不抛异常", async () => {
  const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
  const page = await loadPage({ media: { reject: denied } });
  await startRecording(page);
  assert.ok(page.nodes.status.textContent.includes("麦克风权限被拒绝"));
  assert.equal(page.nodes["mic-button"].textContent, "录音");
  assert.equal(page.nodes["mic-button"].disabled, false);
  assert.equal(page.nodes["recording-bar"].hidden, true);
  assert.equal(page.calls.filter((item) => item.url === "/api/asr").length, 0);
  assert.equal(page.timers.size, 0);
});

test("录音满 60 秒自动结束并识别", async () => {
  const page = await loadPage();
  await startRecording(page);
  feedAudio(page);
  await page.expire(60000);
  assert.equal(page.calls.filter((item) => item.url === "/api/asr").length, 1);
  assert.equal(page.nodes.question.value, "北京的天气。");
  assert.equal(page.nodes["mic-button"].textContent, "录音");
  assert.equal(page.timers.size, 0);
});

test("点取消录音直接丢弃，不上传也不留定时器", async () => {
  const page = await loadPage();
  await startRecording(page);
  feedAudio(page);
  page.nodes["mic-cancel"].emit("click");
  await settle();
  assert.equal(page.calls.filter((item) => item.url === "/api/asr").length, 0);
  assert.equal(page.nodes["mic-button"].textContent, "录音");
  assert.equal(page.media.streams[0].stopped, true);
  assert.equal(page.timers.size, 0);
});

test("语音合成返回音频时用 Audio 播放，按钮可停止并恢复", async () => {
  const page = await loadPage({ tts: () => response({ byteLength: 4 }, 200, { "content-type": "audio/wav" }) });
  await page.send("你好");
  const speaks = page.nodes.messages.querySelectorAll("button");
  const speak = speaks[speaks.length - 1];
  speak.emit("click");
  await settle();
  assert.equal(page.calls.find((item) => item.url === "/api/tts").body.text, "已审核的回答");
  assert.equal(page.media.audios.length, 1);
  assert.equal(page.media.audios[0].played, 1);
  assert.equal(page.media.spoken.length, 0);
  assert.equal(speak.textContent, "停止播放");
  speak.emit("click");
  await settle();
  assert.equal(page.media.audios[0].paused, true);
  assert.equal(speak.textContent, "播放这句话");
  assert.equal(page.media.revoked.length, 1);
  speak.emit("click");
  await settle();
  assert.equal(page.media.audios.length, 2);
  page.media.audios[1].onended();
  assert.equal(speak.textContent, "播放这句话");
  assert.equal(page.media.revoked.length, 2);
});

test("语音合成回退到浏览器朗读时按钮状态同样恢复", async () => {
  const page = await loadPage();
  await page.send("你好");
  const speaks = page.nodes.messages.querySelectorAll("button");
  const speak = speaks[speaks.length - 1];
  speak.emit("click");
  await settle();
  assert.equal(page.media.audios.length, 0);
  assert.equal(page.media.spoken.length, 1);
  assert.equal(speak.textContent, "停止播放");
  assert.equal(speak.disabled, false);
  page.media.spoken[0].onend();
  assert.equal(speak.textContent, "播放这句话");
  assert.equal(speak.disabled, false);
});

test("微信禁止异步播放时保留音频，再点一次直接播放且不重复合成", async () => {
  const denied = Object.assign(new Error("user gesture required"), { name: "NotAllowedError" });
  const page = await loadPage({
    tts: () => response({ byteLength: 4 }, 200, { "content-type": "audio/wav" }),
    media: { play: (audio) => audio.played === 1 ? Promise.reject(denied) : Promise.resolve() },
  });
  const button = page.nodes.messages.querySelectorAll("button")[0];
  button.emit("click");
  await settle();
  assert.equal(button.textContent, "点此播放");
  assert.equal(button.disabled, false);
  assert.equal(page.nodes["voice-notice"].hidden, false);
  assert.ok(page.nodes["voice-notice"].textContent.includes("再点一次"));
  button.emit("click");
  await settle();
  assert.equal(page.media.audios.length, 1);
  assert.equal(page.media.audios[0].played, 2);
  assert.equal(page.calls.filter((call) => call.url === "/api/tts").length, 1);
  assert.equal(button.textContent, "停止播放");
  page.media.audios[0].onended();
  assert.equal(button.textContent, "播放这句话");
  assert.equal(page.media.revoked.length, 1);
});

test("微信无录音接口时立即显示可见提示", async () => {
  const page = await loadPage({ media: { userAgent: "MicroMessenger", recording: false } });
  await startRecording(page);
  assert.equal(page.nodes["voice-notice"].hidden, false);
  assert.ok(page.nodes["voice-notice"].textContent.includes("系统浏览器"));
  assert.equal(page.calls.filter((call) => call.url === "/api/asr").length, 0);
});

test("语音服务失效且微信没有备用朗读时显示失败提示并恢复按钮", async () => {
  const page = await loadPage({ media: { userAgent: "MicroMessenger", speech: false } });
  const button = page.nodes.messages.querySelectorAll("button")[0];
  button.emit("click");
  await settle();
  assert.ok(page.nodes["voice-notice"].textContent.includes("语音服务暂不可用"));
  assert.equal(page.nodes["voice-notice"].hidden, false);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "播放这句话");
});

test("录音识别服务失败时提示保持可见且可重新录音", async () => {
  const page = await loadPage({ asr: () => response({ ok: false, error: "asr_unavailable" }, 503) });
  await startRecording(page);
  feedAudio(page);
  page.nodes["mic-button"].emit("click");
  await settle();
  assert.equal(page.nodes["voice-notice"].hidden, false);
  assert.ok(page.nodes["voice-notice"].textContent.includes("服务暂不可用"));
  assert.equal(page.nodes["mic-button"].disabled, false);
  assert.equal(page.nodes["mic-button"].textContent, "录音");
});

test("等待麦克风授权前已唤醒音频上下文，超时后关闭并释放迟到的录音流", async () => {
  const permission = deferred();
  const page = await loadPage({ media: { permission: permission.promise } });
  await startRecording(page);
  assert.equal(page.media.audioContexts.length, 1);
  assert.equal(page.media.audioContexts[0].resumed, true);
  await page.expire(15000);
  assert.equal(page.media.audioContexts[0].closed, true);
  let stopped = false;
  permission.resolve({ getTracks: () => [{ stop() { stopped = true; } }] });
  await settle();
  assert.equal(stopped, true);
  assert.equal(page.nodes["mic-button"].textContent, "录音");
  assert.equal(page.calls.filter((call) => call.url === "/api/asr").length, 0);
});

test("浏览器禁止存储时仍把扫码口令传给人物、录音识别和朗读接口", async () => {
  const page = await loadPage({ search: "?profile=lin-shu-demo&k=test-only-code", media: { storageBlocked: true } });
  await startRecording(page);
  feedAudio(page);
  page.nodes["mic-button"].emit("click");
  await settle();
  page.nodes.messages.querySelectorAll("button")[0].emit("click");
  await settle();
  for (const url of ["/api/profile?", "/api/asr", "/api/tts"]) {
    const call = page.calls.find((item) => item.url.startsWith(url));
    assert.ok(call, url);
    assert.equal(call.headers["X-Access-Code"], "test-only-code");
  }
});

test("人物列表首次进入也读取扫码口令", async () => {
  const page = await loadPage({ entry: "people.js", search: "?k=test-only-code", media: { storageBlocked: true } });
  const call = page.calls.find((item) => item.url === "/api/profiles");
  assert.equal(call.headers["X-Access-Code"], "test-only-code");
  assert.equal(page.nodes["people-list"].children[0].href, "./index.html?profile=lin-shu-demo&k=test-only-code");
});

test("备用朗读被浏览器拒绝时可再次点击朗读，不重新请求合成", async () => {
  const page = await loadPage();
  const button = page.nodes.messages.querySelectorAll("button")[0];
  button.emit("click");
  await settle();
  page.media.spoken[0].onerror({ error: "not-allowed" });
  assert.equal(button.textContent, "点此朗读");
  button.emit("click");
  await settle();
  assert.equal(page.media.spoken.length, 2);
  assert.equal(page.calls.filter((call) => call.url === "/api/tts").length, 1);
  assert.equal(button.textContent, "停止播放");
});

test("等待麦克风授权时重置立即关闭上下文，迟到的流不能覆盖新录音", async () => {
  const permission = deferred();
  const page = await loadPage({ media: { permission: permission.promise } });
  await startRecording(page);
  await page.reset();
  assert.equal(page.media.audioContexts[0].closed, true);
  assert.equal(page.timers.size, 0);
  let stopped = false;
  permission.resolve({ getTracks: () => [{ stop() { stopped = true; } }] });
  await settle();
  assert.equal(stopped, true);
  assert.equal(page.nodes["mic-button"].textContent, "录音");
});
