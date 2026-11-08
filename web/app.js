import { fetchJson, postSpeech, postWav, rememberAccessCode, withAccessCode } from "./api.js";

// 网址里带 k 参数时先把口令记下来，后续所有接口请求都会带上。
rememberAccessCode();

const DEFAULT_PROFILE_ID = "lin-shu-demo";
const PROFILE_ID_PATTERN = /^[a-z0-9-]{1,48}$/;
const MAX_HISTORY = 8;

// 服务完全不可用时的最后兜底，不包含任何客户资料。
const offlineProfile = {
  id: DEFAULT_PROFILE_ID,
  name: "纪念空间",
  pronoun: "TA",
  intro: "人物资料暂时没有加载出来，请点击下方“重新加载资料”。",
  welcome: "人物资料暂时不可用，请点击“重新加载资料”后再继续。",
  voice: { rate: 0.95 },
  suggestedQuestions: [],
  answers: [],
  fallback: "人物资料暂时不可用。请刷新页面，或确认本地服务已经启动。",
};

const messages = document.querySelector("#messages");
const composer = document.querySelector("#composer");
const questionInput = document.querySelector("#question");
// 必须按 id 取发送按钮：录音按钮也在这个 form 里且排在前面，取第一个 button 会取错。
const submitButton = document.querySelector("#send-button") || composer.querySelector('button[type="submit"]');
const status = document.querySelector("#status");
const voiceNotice = document.querySelector("#voice-notice");
const suggestions = document.querySelector("#suggestions");
const resetButton = document.querySelector("#reset-button");
const retryProfileButton = document.querySelector("#retry-profile");
const availability = document.querySelector("#availability");
const micButton = document.querySelector("#mic-button");
const micCancel = document.querySelector("#mic-cancel");
const recordingBar = document.querySelector("#recording-bar");
const canSpeak = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
const canPlayAudio = typeof window.Audio === "function" && typeof window.URL?.createObjectURL === "function";
const AudioContextClass = window.AudioContext || window.webkitAudioContext;
const canRecord = Boolean(
  AudioContextClass && typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia,
);
const isWeChat = /MicroMessenger/i.test(navigator.userAgent || "");
// 一句话识别要求 16kHz 单声道，录音最长 60 秒，权限弹窗最多等 15 秒。
const TARGET_SAMPLE_RATE = 16000;
const MAX_RECORD_MS = 60000;
const PERMISSION_WAIT_MS = 15000;
const MIN_RECORD_MS = 300;

let profile = offlineProfile;
let profileNotice = "";
let healthMode = "demo";
let baseStatus = "正在加载人物资料…";
let activeSpeakButton = null;
let activeAudio = null;
let activeAudioUrl = "";
let playbackNeedsTap = false;
let browserSpeechNeedsTap = "";
let speakToken = 0;
let micState = "idle";
let micToken = 0;
let micTimer = null;
let recorder = null;
let pendingMicCleanup = null;
let pending = false;
let requestToken = 0;
let activeRequestController = null;
let profileReady = false;

// 会话保存在浏览器本地：手机切走再回来时页面会被系统重载，不存就等于失忆。
const SESSION_KEY = "memorial-session-v1";

function storageRead() {
  try {
    const raw = window.sessionStorage?.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    return null;
  }
}

function storageWrite(historyItems) {
  try {
    if (!historyItems.length) {
      window.sessionStorage?.removeItem(SESSION_KEY);
      return;
    }
    window.sessionStorage?.setItem(SESSION_KEY, JSON.stringify({ profileId: profile.id, history: historyItems }));
  } catch (error) {
    // 隐私模式下可能不允许写入，忽略即可，不影响对话。
  }
}

function restoreSession() {
  const saved = storageRead();
  if (!saved || saved.profileId !== profile.id || !Array.isArray(saved.history) || !saved.history.length) return;
  saved.history.forEach((item) => {
    if (!item || typeof item.content !== "string") return;
    history.push({ role: item.role, content: item.content });
    if (item.role === "assistant") addAssistantMessage(item.content);
    else appendMessage(item.content, "user");
  });
  setBaseStatus("继续上次的对话 · 换一个人会重新开始");
}

// 手机上没有控制台，把关键状态和脚本错误直接显示在页面上（网址加 &debug=1 才显示）。
const debugBar = document.querySelector("#debug-bar");
const debugEnabled = (() => {
  try {
    return new URLSearchParams(window.location.search).get("debug") === "1";
  } catch (error) {
    return false;
  }
})();
let lastFault = "";

function renderDebug() {
  if (!debugBar || !debugEnabled) return;
  debugBar.hidden = false;
  debugBar.textContent = [
    "安全上下文=" + (window.isSecureContext === false ? "否（http 地址，浏览器会禁用麦克风）" : "是"),
    "录音能力=" + (canRecord ? "支持" : "不支持"),
    "资料已加载=" + (profileReady ? "是" : "否"),
    "请求进行中=" + (pending ? "是" : "否"),
    "服务模式=" + healthMode,
    "最近错误=" + (lastFault || "无"),
  ].join("\n");
}

function noteFault(message) {
  lastFault = String(message || "").slice(0, 120);
  renderDebug();
}

window.addEventListener("error", (event) => noteFault(event.message));
window.addEventListener("unhandledrejection", (event) => noteFault(event.reason?.message || event.reason));
if (debugEnabled) {
  renderDebug();
  window.setInterval(renderDebug, 700);
}
let initializing = false;
const history = [];

function setStatus(text) {
  status.textContent = text;
  if (voiceNotice) voiceNotice.hidden = true;
}

function setVoiceStatus(text) {
  setStatus(text);
  if (voiceNotice) {
    voiceNotice.textContent = text;
    voiceNotice.hidden = false;
  }
}

function setBaseStatus(text) {
  baseStatus = text;
  setStatus(text);
}

function normalizeProfile(raw, fallbackId) {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" && PROFILE_ID_PATTERN.test(raw.id) ? raw.id : fallbackId;
  if (!id || !PROFILE_ID_PATTERN.test(id)) return null;
  if (typeof raw.name !== "string" || !raw.name.trim()) return null;
  if (typeof raw.intro !== "string") return null;
  const answers = Array.isArray(raw.answers) ? raw.answers : [];
  const questions = Array.isArray(raw.suggestedQuestions) ? raw.suggestedQuestions : [];
  return {
    ...offlineProfile,
    ...raw,
    id,
    name: raw.name.trim(),
    intro: raw.intro,
    pronoun: typeof raw.pronoun === "string" && raw.pronoun.trim() ? raw.pronoun.trim() : "TA",
    voice: raw.voice && typeof raw.voice === "object" ? raw.voice : {},
    suggestedQuestions: questions.filter((item) => typeof item === "string" && item.trim()).slice(0, 6),
    answers: answers.filter(
      (answer) => answer && Array.isArray(answer.keys) && typeof answer.text === "string",
    ),
  };
}

async function fetchProfile(id) {
  const data = await fetchJson(`/api/profile?id=${encodeURIComponent(id)}`, { cache: "no-store" });
  const loaded = normalizeProfile(data, id);
  if (!loaded) throw new Error("profile_invalid");
  return loaded;
}

async function loadProfile() {
  const requested = new URLSearchParams(window.location.search).get("profile") || DEFAULT_PROFILE_ID;
  if (!PROFILE_ID_PATTERN.test(requested)) {
    profileNotice = "人物链接无效，已切换到默认人物。";
    try {
      return await fetchProfile(DEFAULT_PROFILE_ID);
    } catch (error) {
      console.warn("默认人物资料也不可用。", error);
      profileNotice = "人物链接无效，人物资料也暂时不可用，请重新加载资料。";
      return null;
    }
  }
  try {
    return await fetchProfile(requested);
  } catch (error) {
    console.warn("人物资料加载失败。", error);
    if (requested !== DEFAULT_PROFILE_ID && error.status === 404) {
      try {
        const fallback = await fetchProfile(DEFAULT_PROFILE_ID);
        profileNotice = "没有找到这位人物，已切换到默认人物。";
        return fallback;
      } catch (fallbackError) {
        console.warn("默认人物资料也不可用。", fallbackError);
      }
    }
    profileNotice = "人物资料加载失败，请重新加载资料。";
    return null;
  }
}

function appendMessage(text, role, extraClass = "") {
  const item = document.createElement("div");
  item.className = `message ${role}${extraClass ? ` ${extraClass}` : ""}`;
  item.textContent = text;
  messages.append(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function resetActiveSpeakButton() {
  if (activeSpeakButton) {
    activeSpeakButton.textContent = "播放这句话";
    activeSpeakButton.disabled = false;
    activeSpeakButton = null;
  }
}

// 停掉正在播放的音频和浏览器朗读，两条播放链路互斥。
function stopPlayback() {
  playbackNeedsTap = false;
  browserSpeechNeedsTap = "";
  window.speechSynthesis?.cancel();
  const audio = activeAudio;
  const url = activeAudioUrl;
  activeAudio = null;
  activeAudioUrl = "";
  // 用户点“停止”时不会触发 onended，必须在这里释放对象地址。
  if (url) window.URL.revokeObjectURL?.(url);
  if (!audio) return;
  audio.onended = null;
  audio.onerror = null;
  try {
    audio.pause();
  } catch (error) {
    console.warn("停止音频播放时出错，已忽略。", error);
  }
}

function finishSpeech(button, token, message) {
  if (token !== speakToken || activeSpeakButton !== button) return;
  stopPlayback();
  resetActiveSpeakButton();
  if (message) setVoiceStatus(message);
  else setStatus(baseStatus);
}

// 服务端回退 browser 模式时的原路径：用浏览器自带的语音朗读。
function speakWithBrowser(text, button, token) {
  if (!canSpeak) {
    finishSpeech(button, token, isWeChat
      ? "语音服务暂不可用，微信也无法使用备用朗读。请稍后重试，或点右上角菜单在系统浏览器打开。"
      : "语音服务暂不可用，当前浏览器不支持备用朗读，请稍后重试。");
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  const rate = Number(profile.voice?.rate);
  utterance.rate = Number.isFinite(rate) && rate >= 0.5 && rate <= 1.5 ? rate : 0.95;
  const voices = window.speechSynthesis.getVoices?.() || [];
  const chineseVoice =
    voices.find((voice) => /^zh[-_]CN/i.test(voice.lang)) ||
    voices.find((voice) => /^zh/i.test(voice.lang));
  if (chineseVoice) utterance.voice = chineseVoice;
  button.textContent = "停止播放";
  button.disabled = false;
  utterance.onend = () => finishSpeech(button, token, "");
  utterance.onerror = (event) => {
    if (token !== speakToken || activeSpeakButton !== button) return;
    if (event?.error === "not-allowed") {
      browserSpeechNeedsTap = text;
      button.textContent = "点此朗读";
      button.disabled = false;
      setVoiceStatus("浏览器需要再次点击才能朗读，请点“点此朗读”。");
      return;
    }
    finishSpeech(button, token, "语音播放失败，请重试或在系统浏览器打开。");
  };
  try {
    window.speechSynthesis.speak(utterance);
    setVoiceStatus("正在朗读…");
  } catch (error) {
    finishSpeech(button, token, "浏览器未能开始朗读，请重试或在系统浏览器打开。");
  }
}

function startAudioPlayback(audio, button, token) {
  playbackNeedsTap = false;
  button.textContent = "停止播放";
  button.disabled = false;
  setVoiceStatus("正在播放语音…");
  const failed = (error) => {
    if (token !== speakToken || activeAudio !== audio || activeSpeakButton !== button) return;
    if (error?.name === "NotAllowedError") {
      // 保留已生成的音频，下一次点击直接播放，不再等待合成请求而丢失用户手势。
      playbackNeedsTap = true;
      button.textContent = "点此播放";
      setVoiceStatus("语音已准备好，请再点一次“点此播放”。");
      return;
    }
    finishSpeech(button, token, "语音播放失败，请重试或在系统浏览器打开。");
  };
  try {
    const started = audio.play();
    if (started && typeof started.catch === "function") started.catch(failed);
  } catch (error) {
    failed(error);
  }
}

// 服务端返回 audio/wav 时用 Audio 元素播放，按钮同时充当停止控件。
function playAudio(blob, text, button, token) {
  let url = "";
  try {
    url = window.URL.createObjectURL(blob);
  } catch (error) {
    console.warn("音频地址创建失败，改用浏览器朗读。", error);
  }
  if (!url) {
    speakWithBrowser(text, button, token);
    return;
  }
  let audio;
  try {
    audio = new window.Audio(url);
  } catch (error) {
    console.warn("音频元素创建失败，改用浏览器朗读。", error);
    window.URL.revokeObjectURL?.(url);
    speakWithBrowser(text, button, token);
    return;
  }
  activeAudio = audio;
  activeAudioUrl = url;
  button.textContent = "停止播放";
  button.disabled = false;
  // 释放地址统一由 stopPlayback 负责，这里只收尾按钮状态。
  const release = (message) => finishSpeech(button, token, message);
  audio.onended = () => release("");
  audio.onerror = () => release("语音播放失败，文字回答仍可继续使用。");
  startAudioPlayback(audio, button, token);
}

async function speakText(text, button) {
  if (activeSpeakButton === button) {
    if (playbackNeedsTap && activeAudio) {
      startAudioPlayback(activeAudio, button, speakToken);
      return;
    }
    if (browserSpeechNeedsTap) {
      const preparedText = browserSpeechNeedsTap;
      browserSpeechNeedsTap = "";
      speakWithBrowser(preparedText, button, speakToken);
      return;
    }
    speakToken += 1;
    stopPlayback();
    resetActiveSpeakButton();
    setStatus(baseStatus);
    return;
  }
  if (!canPlayAudio && !canSpeak) {
    setVoiceStatus("当前浏览器不支持语音播放，请在系统浏览器打开。");
    return;
  }
  speakToken += 1;
  const token = speakToken;
  stopPlayback();
  resetActiveSpeakButton();
  activeSpeakButton = button;
  button.textContent = "正在生成语音…";
  button.disabled = true;
  setVoiceStatus("正在准备语音，请稍候…");
  let result = null;
  try {
    result = await postSpeech("/api/tts", { profileId: profile.id, text }, { timeoutMs: 15000 });
  } catch (error) {
    console.warn("语音合成接口不可用，改用浏览器朗读。", error);
  }
  if (token !== speakToken) return;
  if (result?.audio && canPlayAudio) {
    playAudio(result.audio, text, button, token);
    return;
  }
  speakWithBrowser(text, button, token);
}

function addAssistantMessage(text) {
  const item = appendMessage(text, "assistant");
  const tools = document.createElement("div");
  tools.className = "message-tools";
  const speak = document.createElement("button");
  speak.className = "speak-button";
  speak.type = "button";
  speak.textContent = "播放这句话";
  speak.addEventListener("click", () => speakText(text, speak));
  tools.append(speak);
  item.append(tools);
}

function setMicState(next) {
  micState = next;
  if (!micButton) return;
  const labels = { idle: "录音", starting: "准备中…", recording: "停止", processing: "识别中…" };
  micButton.textContent = labels[next] || labels.idle;
  micButton.className = next === "recording" ? "mic-button is-recording" : "mic-button";
  // 录音中必须保持可点，否则"再点一次结束录音"这条提示根本无法执行。
  micButton.disabled = !profileReady || (next !== "idle" && next !== "recording");
  micButton.setAttribute("aria-pressed", next === "recording" ? "true" : "false");
  const labelsForScreen = { idle: "开始语音输入", starting: "正在准备麦克风", recording: "停止录音并识别", processing: "正在识别" };
  micButton.setAttribute("aria-label", labelsForScreen[next] || labelsForScreen.idle);
  if (recordingBar) recordingBar.hidden = next !== "recording";
}

// 把浏览器抛出的录音错误翻译成用户能看懂的一句话。
function micErrorText(error) {
  const name = error?.name || "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
    return "麦克风权限被拒绝，请用键盘输入，或在浏览器设置里允许麦克风。";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return "没有找到可用的麦克风设备，请用键盘输入。";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "麦克风被其他程序占用，请关闭后重试。";
  }
  return "录音没有启动成功，请用键盘输入。";
}

function stopStream(stream) {
  stream?.getTracks?.().forEach((track) => track.stop?.());
}

function clearMicTimer() {
  if (micTimer === null) return;
  window.clearTimeout(micTimer);
  micTimer = null;
}

function discardRecorder() {
  const active = recorder;
  recorder = null;
  if (!active) return;
  active.processor.onaudioprocess = null;
  try {
    active.processor.disconnect();
  } catch (error) {
    console.warn("断开录音节点时出错，已忽略。", error);
  }
  try {
    active.source.disconnect();
  } catch (error) {
    console.warn("断开音频源时出错，已忽略。", error);
  }
  try {
    active.context.close?.();
  } catch (error) {
    console.warn("关闭音频上下文时出错，已忽略。", error);
  }
  stopStream(active.stream);
}

function mergeChunks(chunks) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  chunks.forEach((chunk) => {
    merged.set(chunk, offset);
    offset += chunk.length;
  });
  return merged;
}

// 线性插值重采样到 16kHz，保证送进一句话识别的格式固定。
export function downsample(samples, inputRate, targetRate = TARGET_SAMPLE_RATE) {
  if (!samples?.length) return new Float32Array(0);
  if (inputRate === targetRate) return samples;
  const ratio = inputRate / targetRate;
  const length = Math.floor(samples.length / ratio);
  const output = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = index * ratio;
    const left = Math.min(Math.floor(start), samples.length - 1);
    const right = Math.min(left + 1, samples.length - 1);
    const weight = start - left;
    output[index] = samples[left] * (1 - weight) + samples[right] * weight;
  }
  return output;
}

// 生成标准 44 字节头的 WAV：PCM、单声道、16bit。
export function encodeWav(samples, sampleRate = TARGET_SAMPLE_RATE) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeAscii = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + index * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  });
  return buffer;
}

function beginCapture(stream, token, context) {
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  processor.onaudioprocess = (event) => {
    const channel = event?.inputBuffer?.getChannelData?.(0);
    if (channel) chunks.push(new Float32Array(channel));
  };
  // 静音节点：ScriptProcessor 必须连到 destination 才会回调，但不能让麦克风回放出声。
  const mute = context.createGain();
  mute.gain.value = 0;
  source.connect(processor);
  processor.connect(mute);
  mute.connect(context.destination);
  recorder = { context, source, processor, mute, chunks, stream, rate: context.sampleRate || TARGET_SAMPLE_RATE };
  micTimer = window.setTimeout(() => finishRecording(token), MAX_RECORD_MS);
}

async function startRecording() {
  if (!profileReady) {
    setVoiceStatus("人物资料还没加载好，请稍后再试。");
    return;
  }
  if (!canRecord) {
    setVoiceStatus(
      window.isSecureContext === false
        ? "当前是 http 局域网地址，浏览器禁止使用麦克风。改用 https 或本机 localhost 打开才能录音。"
        : isWeChat
          ? "当前微信环境不支持网页录音，请点右上角菜单，在系统浏览器打开后重试。"
          : "当前浏览器不支持录音，请用键盘输入。",
    );
    return;
  }
  const token = ++micToken;
  let stream = null;
  let captureContext = null;
  const closePendingContext = () => {
    stopStream(stream);
    try { captureContext?.close?.()?.catch?.(() => {}); } catch (error) { /* 已关闭 */ }
    if (pendingMicCleanup === closePendingContext) pendingMicCleanup = null;
  };
  pendingMicCleanup = closePendingContext;
  setMicState("starting");
  setVoiceStatus("正在打开麦克风，请允许录音…");
  clearMicTimer();
  micTimer = window.setTimeout(() => {
    if (token !== micToken) return;
    micToken += 1;
    micTimer = null;
    closePendingContext();
    setMicState("idle");
    setVoiceStatus(isWeChat
      ? "微信未能打开麦克风，请点右上角菜单在系统浏览器打开，或用键盘输入。"
      : "没等到麦克风授权，可以重试或直接用键盘输入。");
  }, PERMISSION_WAIT_MS);
  try {
    // 在点击事件内创建并唤醒音频上下文，避免手机等待授权后丢失用户手势。
    captureContext = new AudioContextClass();
    const resumed = Promise.resolve(captureContext.resume?.());
    resumed.catch(() => {});
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, sampleRate: TARGET_SAMPLE_RATE, echoCancellation: true, noiseSuppression: true },
    });
    if (token !== micToken) {
      closePendingContext();
      return;
    }
    await resumed;
  } catch (error) {
    closePendingContext();
    if (token !== micToken) return;
    clearMicTimer();
    setMicState("idle");
    setVoiceStatus(micErrorText(error));
    return;
  }
  if (token !== micToken) {
    closePendingContext();
    return;
  }
  clearMicTimer();
  if (pendingMicCleanup === closePendingContext) pendingMicCleanup = null;
  try {
    beginCapture(stream, token, captureContext);
  } catch (error) {
    console.warn("录音启动失败。", error);
    closePendingContext();
    setMicState("idle");
    setVoiceStatus("录音启动失败，请重试或在系统浏览器打开。");
    return;
  }
  setMicState("recording");
  setVoiceStatus("正在录音，再点一次麦克风结束并识别，最长 60 秒。");
}

async function transcribe(buffer, token) {
  setMicState("processing");
  setVoiceStatus("正在识别你说的话…");
  try {
    const data = await postWav("/api/asr", buffer, { timeoutMs: 15000 });
    if (token !== micToken) return;
    const text = typeof data?.text === "string" ? data.text.trim() : "";
    setMicState("idle");
    if (text) {
      // 只填进输入框，发送与否交给用户确认。
      questionInput.value = text;
      questionInput.focus?.();
      setVoiceStatus("已填好识别结果，确认无误再点发送。");
    } else {
      setVoiceStatus("没有识别到内容，请再说一次或用键盘输入。");
    }
  } catch (error) {
    if (token !== micToken) return;
    console.warn("语音识别请求失败。", error);
    setMicState("idle");
    setVoiceStatus(error?.status === 503 ? "语音识别服务暂不可用，请稍后重试或用键盘输入。" : "语音识别失败，请重试或在系统浏览器打开。");
  }
}

function finishRecording(token) {
  if (token !== micToken || micState !== "recording" || !recorder) return;
  clearMicTimer();
  const { chunks, rate } = recorder;
  discardRecorder();
  const samples = downsample(mergeChunks(chunks), rate, TARGET_SAMPLE_RATE);
  if (samples.length < (TARGET_SAMPLE_RATE * MIN_RECORD_MS) / 1000) {
    setMicState("idle");
    setVoiceStatus("录音太短了，请说完整一句再点停止。");
    return;
  }
  transcribe(encodeWav(samples, TARGET_SAMPLE_RATE), token);
}

function cancelRecording() {
  micToken += 1;
  clearMicTimer();
  pendingMicCleanup?.();
  pendingMicCleanup = null;
  discardRecorder();
  if (micState === "idle") return;
  setMicState("idle");
  setStatus("已取消录音。");
}

function answerFor(question) {
  let best = null;
  let bestScore = 0;
  profile.answers.forEach((answer) => {
    const score = answer.keys.filter((key) => question.includes(key)).length;
    if (score > bestScore) {
      best = answer;
      bestScore = score;
    }
  });
  return best?.text ?? profile.fallback;
}

async function requestAnswer(question, signal) {
  try {
    const data = await fetchJson("/api/chat", {
      method: "POST",
      timeoutMs: 25000,
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profileId: profile.id,
        message: question,
        history: history.slice(-MAX_HISTORY),
      }),
    });
    if (!data?.ok || typeof data.reply !== "string" || !data.reply.trim()) {
      throw new Error("chat_invalid");
    }
    return data;
  } catch (error) {
    if (signal.aborted) throw error;
    if (error.status === 404 || error.status === 503 || profile.kind === "real") {
      const unavailable = new Error("profile_unavailable");
      unavailable.profileUnavailable = true;
      throw unavailable;
    }
    console.warn("对话服务不可用，回退到页面内置的审核回答。", error);
    return {
      ok: true,
      reply: answerFor(question),
      mode: "demo",
      source: "local_fallback",
      fallback: true,
    };
  }
}

function setSuggestionsDisabled(disabled) {
  suggestions.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function setComposerDisabled(disabled) {
  submitButton.disabled = disabled;
  questionInput.disabled = disabled;
  setSuggestionsDisabled(disabled);
  if (micButton) micButton.disabled = disabled || (micState !== "idle" && micState !== "recording");
}

function renderSuggestions() {
  suggestions.replaceChildren();
  const questions = profile.suggestedQuestions;
  if (!questions.length) {
    suggestions.hidden = true;
    return;
  }
  questions.forEach((question) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = question;
    button.dataset.question = question;
    button.addEventListener("click", () => sendQuestion(question));
    suggestions.append(button);
  });
  suggestions.hidden = false;
}

async function sendQuestion(question) {
  const cleanQuestion = question.trim();
  if (!cleanQuestion || pending || !profileReady) return;
  pending = true;
  const token = ++requestToken;
  const controller = new AbortController();
  activeRequestController = controller;
  appendMessage(cleanQuestion, "user");
  questionInput.value = "";
  setComposerDisabled(true);
  setStatus("正在整理资料…");
  const pendingMessage = appendMessage("正在想一想…", "assistant", "pending");
  try {
    const result = await requestAnswer(cleanQuestion, controller.signal);
    if (token !== requestToken) return;
    pendingMessage.remove();
    addAssistantMessage(result.reply);
    history.push({ role: "user", content: cleanQuestion });
    history.push({ role: "assistant", content: result.reply });
    if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY);
    storageWrite(history);
    if (result.mode === "live") {
      setBaseStatus("真实模型回答 · 请人工复核重要内容");
    } else if (result.fallback) {
      setBaseStatus("已回退到审核过的演示回答 · 内容仅来自人物资料");
    } else {
      setBaseStatus(`演示模式 · ${profile.name} · 回答来自已审核的人物资料`);
    }
  } catch (error) {
    if (token !== requestToken) return;
    pendingMessage.remove();
    if (error.profileUnavailable) {
      profileNotice = "人物资料暂时不可用，请重新加载资料后再继续。";
      useLoadedProfile(null);
      return;
    }
    addAssistantMessage(profile.fallback);
    setBaseStatus("服务暂时不可用 · 已显示人物资料中的兜底回答");
  } finally {
    if (token === requestToken) {
      pending = false;
      activeRequestController = null;
      setComposerDisabled(false);
    }
  }
}

function resetChat(focusInput = true) {
  requestToken += 1;
  activeRequestController?.abort();
  activeRequestController = null;
  pending = false;
  history.length = 0;
  questionInput.value = "";
  speakToken += 1;
  stopPlayback();
  resetActiveSpeakButton();
  cancelRecording();
  setComposerDisabled(!profileReady);
  messages.replaceChildren();
  if (profileReady) {
    addAssistantMessage(profile.welcome);
    const modeText = modeStatus();
    setBaseStatus(profileNotice ? `${profileNotice} ${modeText}` : modeText);
  } else {
    appendMessage(profile.welcome, "assistant");
    setBaseStatus(profileNotice || "人物资料暂时不可用，请重新加载资料。");
  }
  if (profileReady && focusInput) questionInput.focus();
}

function modeStatus() {
  if (healthMode === "live") return "AI 问答已配置 · 回答以人物资料为准";
  return `演示模式 · ${profile.name} · 回答来自已审核的人物资料`;
}

async function detectMode() {
  try {
    const data = await fetchJson("/api/health", { cache: "no-store", timeoutMs: 4000 });
    if (data?.mode === "live") healthMode = "live";
  } catch (error) {
    console.warn("未能读取服务模式，按演示模式显示。", error);
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendQuestion(questionInput.value);
});
resetButton.addEventListener("click", () => {
  resetChat();
  storageWrite([]);
});
retryProfileButton.addEventListener("click", init);
micButton?.addEventListener("click", () => {
  if (micState === "recording") finishRecording(micToken);
  else if (micState === "idle") startRecording();
});
micCancel?.addEventListener("click", cancelRecording);
window.addEventListener("pagehide", () => {
  activeRequestController?.abort();
  speakToken += 1;
  stopPlayback();
  cancelRecording();
});

function useLoadedProfile(loadedProfile) {
  profile = loadedProfile || { ...offlineProfile };
  profileReady = Boolean(loadedProfile);
  document.title = profileReady ? `${profile.name} · 数字纪念空间` : "数字纪念空间";
  document.querySelector("#profile-name").textContent = profile.name;
  document.querySelector("#profile-intro").textContent = profile.intro;
  document.querySelector("#portrait").textContent = profile.name.slice(0, 2);
  document.querySelector("#chat-title").textContent = profileReady ? `和${profile.pronoun}聊几句` : "加载人物资料";
  const backLink = document.querySelector(".back-link");
  if (backLink) backLink.href = withAccessCode("./people.html");
  renderSuggestions();
  resetButton.disabled = !profileReady;
  availability.hidden = !profileReady;
  retryProfileButton.hidden = profileReady;
  setMicState("idle");
  resetChat(false);
  restoreSession();
}

async function init() {
  if (initializing) return;
  initializing = true;
  profileReady = false;
  profileNotice = "";
  healthMode = "demo";
  setComposerDisabled(true);
  availability.hidden = true;
  resetButton.disabled = true;
  retryProfileButton.disabled = true;
  setBaseStatus("正在加载人物资料…");
  const [loadedProfile] = await Promise.all([loadProfile(), detectMode()]);
  useLoadedProfile(loadedProfile);
  retryProfileButton.disabled = false;
  initializing = false;
}

window.memorialAppStarted = true;
window.clearTimeout(window.memorialStartupTimer);
init();
