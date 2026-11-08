// 访问口令：二维码里的网址带 k 参数，首次进入后存在本地，后续请求自动带上。
const ACCESS_KEY = "memorial-access-code";
let pageAccessCode = "";

export function rememberAccessCode() {
  try {
    const code = new URLSearchParams(window.location.search).get("k");
    if (code) {
      pageAccessCode = code;
      window.localStorage?.setItem(ACCESS_KEY, code);
    }
  } catch (error) {
    // 隐私模式或没有 localStorage 时忽略，不影响本地开发。
  }
}

function accessHeaders() {
  // 微信或隐私模式可能禁止本地存储，当前页面仍需保留扫码带来的口令。
  if (pageAccessCode) return { "X-Access-Code": pageAccessCode };
  try {
    const code = window.localStorage?.getItem(ACCESS_KEY);
    return code ? { "X-Access-Code": code } : {};
  } catch (error) {
    return {};
  }
}

export function withAccessCode(url) {
  const code = accessHeaders()["X-Access-Code"];
  return code ? `${url}${url.includes("?") ? "&" : "?"}k=${encodeURIComponent(code)}` : url;
}

// 超时覆盖网络请求和响应读取；页面离开或重置时也可主动取消。
async function request(url, { timeoutMs = 8000, signal, ...options } = {}, readBody) {
  const controller = new AbortController();
  let rejectAbort;
  const aborted = new Promise((resolve, reject) => {
    rejectAbort = () => reject(new DOMException("Aborted", "AbortError"));
    controller.signal.addEventListener("abort", rejectAbort, { once: true });
  });
  const cancel = () => controller.abort();
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });
  const timer = window.setTimeout(cancel, timeoutMs);
  try {
    return await Promise.race([
      (async () => {
        if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
        const response = await fetch(url, {
          ...options,
          headers: { ...accessHeaders(), ...(options.headers || {}) },
          signal: controller.signal,
        });
        if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
        if (!response.ok) {
          const error = new Error(`http_${response.status}`);
          error.status = response.status;
          throw error;
        }
        return await readBody(response);
      })(),
      aborted,
    ]);
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", rejectAbort);
  }
}

export async function fetchJson(url, options = {}) {
  return request(url, options, (response) => response.json());
}

// 上传录音：body 是标准 WAV 的 ArrayBuffer，服务端转发一句话识别。
export async function postWav(url, buffer, { timeoutMs = 15000, signal } = {}) {
  return request(url, {
    method: "POST",
    timeoutMs,
    signal,
    headers: { "Content-Type": "audio/wav" },
    body: buffer,
  }, (response) => response.json());
}

// 语音合成：成功时服务端直接回 audio/wav 字节，回退时回浏览器朗读的 JSON。
export async function postSpeech(url, payload, { timeoutMs = 15000, signal } = {}) {
  return request(url, {
    method: "POST",
    timeoutMs,
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }, async (response) => {
    const contentType = response.headers?.get?.("content-type") || "";
    if (contentType.toLowerCase().includes("audio/")) {
      return { audio: await response.blob() };
    }
    return { data: await response.json() };
  });
}
