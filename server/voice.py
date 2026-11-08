"""阿里云智能语音交互（NLS）：一句话识别与语音合成。

只使用标准库 urllib，不引入任何第三方依赖。
约定：任何失败都返回 None（不是抛异常），由 server.py 回退到现有行为，
并且绝不在返回值、日志或响应里出现 NLS_TOKEN。
"""
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import wave

DEFAULT_ASR_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr"
DEFAULT_TTS_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"
DEFAULT_TTS_VOICE = "xiaoyun"
ASR_TIMEOUT = 8
TTS_TIMEOUT = 12
MAX_TTS_CHARS = 300
MAX_AUDIO_BYTES = 5 * 1024 * 1024
ASR_SUCCESS_STATUS = 20000000
WAV_HEADER = b"RIFF"
# 断句与停顿：整段一次性合成会一顺到底、没有呼吸感，改为逐句合成再拼接。
SENTENCE_GAP_MS = 260
MAX_SENTENCES = 8
SENTENCE_END = "。！？；…!?;\n"
SAMPLE_RATE = 16000
# 与 server.py 里的模型调用保持一致：网络错误和脏数据都按“不可用”处理。
FAULTS = (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError, KeyError)


def _env(name):
    return os.environ.get(name, "").strip()


def nls_configured():
    """appkey 与 token 同时存在才算语音链路可用。"""
    return bool(_env("NLS_APPKEY") and _env("NLS_TOKEN"))


def asr_endpoint():
    return _env("NLS_ASR_URL") or DEFAULT_ASR_URL


def tts_endpoint():
    return _env("NLS_TTS_URL") or DEFAULT_TTS_URL


def split_for_tts(text):
    """返回 (本次送入的文本, 是否被截断)。接口单次上限 300 字，本轮只取第一段。"""
    clean = text if isinstance(text, str) else ""
    return clean[:MAX_TTS_CHARS], len(clean) > MAX_TTS_CHARS


def split_sentences(text):
    """按中文句末标点切成句子，保留标点，供逐句合成用。

    句数超过上限时把剩余内容并进最后一句，避免请求数失控。
    """
    clean = (text or "").strip()
    if not clean:
        return []
    sentences, buffer = [], ""
    for char in clean:
        buffer += char
        if char in SENTENCE_END:
            if buffer.strip():
                sentences.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        sentences.append(buffer.strip())
    if not sentences:
        return []
    if len(sentences) > MAX_SENTENCES:
        head = sentences[: MAX_SENTENCES - 1]
        head.append("".join(sentences[MAX_SENTENCES - 1:]))
        sentences = head
    return sentences


def pause_ms():
    """句间停顿毫秒数，可用 NLS_TTS_PAUSE_MS 覆盖，限制在 0~2000。"""
    raw = _env("NLS_TTS_PAUSE_MS")
    if not raw:
        return SENTENCE_GAP_MS
    try:
        return max(0, min(2000, int(raw)))
    except ValueError:
        return SENTENCE_GAP_MS


def _silence(milliseconds):
    frames = int(SAMPLE_RATE * milliseconds / 1000)
    return b"\x00\x00" * max(0, frames)


def _join_wav(chunks, gap):
    """把多段 16k/16bit/单声道 WAV 拼成一段，句间插入静音。"""
    if len(chunks) == 1:
        return chunks[0]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        for index, audio in enumerate(chunks):
            if index:
                writer.writeframes(_silence(gap))
            with wave.open(io.BytesIO(audio)) as reader:
                writer.writeframes(reader.readframes(reader.getnframes()))
    return buffer.getvalue()


def recognize(audio):
    """把 16kHz/16bit/单声道 WAV 交给一句话识别。

    成功返回识别文字（可能是空字符串，表示听清了但没听懂内容），
    不可用或供应商失败返回 None，调用方据此返回 503。
    """
    if not audio or not nls_configured():
        return None
    query = urllib.parse.urlencode({
        "appkey": _env("NLS_APPKEY"),
        "format": "wav",
        "sample_rate": 16000,
    })
    request = urllib.request.Request(
        f"{asr_endpoint()}?{query}",
        data=audio,
        headers={"Content-Type": "application/octet-stream", "X-NLS-Token": _env("NLS_TOKEN")},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=ASR_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except FAULTS:
        return None
    if not isinstance(payload, dict) or payload.get("status") != ASR_SUCCESS_STATUS:
        return None
    result = payload.get("result")
    return result.strip() if isinstance(result, str) else None


def synthesize(text):
    """转发语音合成。

    返回 (音频字节, 是否截断)；任何失败都返回 (None, False)，由调用方回退浏览器朗读。
    成功时响应体是 WAV 音频，失败时供应商会返回 JSON 文本，用 RIFF 头区分最稳。
    """
    if not nls_configured():
        return None, False
    spoken, truncated = split_for_tts(text)
    if not spoken.strip():
        return None, False
    sentences = split_sentences(spoken)
    if not sentences:
        return None, False
    chunks = []
    for sentence in sentences:
        audio = _tts_once(sentence)
        if not audio:
            return None, False
        chunks.append(audio)
    try:
        return _join_wav(chunks, pause_ms()), truncated
    except (wave.Error, EOFError, ValueError):
        return None, False


def _tts_once(spoken):
    """合成单句，成功返回 WAV 字节，任何失败返回 None。"""
    # 必须用 JSON 请求体：表单编码会被网关直接拒成 400（2026-11-04 实测）。
    # 不用 GET 查询串，是为了不把 token 写进 URL 与访问日志。
    payload = {
        "appkey": _env("NLS_APPKEY"),
        "token": _env("NLS_TOKEN"),
        "text": spoken,
        "format": "wav",
        "sample_rate": 16000,
        "voice": _env("NLS_TTS_VOICE") or DEFAULT_TTS_VOICE,
    }
    # 语速 / 语调 / 音量：不填时保持服务端默认，填了才下发，便于不改代码调听感。
    for key, env_name in (("speech_rate", "NLS_TTS_RATE"), ("pitch_rate", "NLS_TTS_PITCH"), ("volume", "NLS_TTS_VOLUME")):
        raw = _env(env_name)
        if raw:
            try:
                payload[key] = int(raw)
            except ValueError:
                pass
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        tts_endpoint(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TTS_TIMEOUT) as response:
            audio = response.read()
    except FAULTS:
        return None
    return audio if audio.startswith(WAV_HEADER) else None
