import argparse
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import voice
from profile_store import DEFAULT_PROFILE_ID, PROFILE_PATTERN, ProfileRepository, ProfileUnavailable


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
MODEL_ENV_KEYS = ("MODEL_API_BASE_URL", "MODEL_API_KEY", "MODEL_API_NAME")
MAX_MESSAGE_LENGTH = 500
MAX_HISTORY_ITEMS = 8
# 只接受这几种声音容器类型，其余一律 400，避免把任意字节转发给供应商。
AUDIO_CONTENT_TYPES = ("audio/wav", "audio/x-wav", "audio/wave")
_DOTENV_VALUES = {}
_DOTENV_LOCK = threading.Lock()


def load_dotenv():
    """刷新文件提供的配置，同时保留启动环境或后续显式设置的值。"""
    env_path = ROOT / "server" / ".env"
    with _DOTENV_LOCK:
        try:
            contents = env_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return
        for raw_line in contents.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key not in os.environ or (key in _DOTENV_VALUES and os.environ[key] == _DOTENV_VALUES[key]):
                os.environ[key] = value
                _DOTENV_VALUES[key] = value


def refresh_dotenv():
    # 只有启动时读取过文件的服务才刷新；嵌入使用和测试注入的环境不读本机私有文件。
    if _DOTENV_VALUES:
        load_dotenv()


def live_configured():
    return all(os.environ.get(key, "").strip() for key in MODEL_ENV_KEYS)


def list_profiles():
    return ProfileRepository(ROOT).list_profiles()


def requested_profile(profile_id):
    return ProfileRepository(ROOT).load(profile_id)


def demo_answer(profile, question):
    best_text = profile.get("fallback", "这个问题我得想一想。")
    best_score = 0
    for answer in profile.get("answers", []):
        keys = answer.get("keys", [])
        if not isinstance(keys, list):
            continue
        score = sum(1 for key in keys if isinstance(key, str) and key and key in question)
        if score > best_score:
            best_text = answer.get("text", best_text)
            best_score = score
    return best_text


def clean_history(raw_history):
    if not isinstance(raw_history, list):
        return []
    history = []
    for item in raw_history[-MAX_HISTORY_ITEMS:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        history.append({"role": role, "content": content.strip()[:MAX_MESSAGE_LENGTH]})
    return history


def call_openai_compatible(profile, question, history):
    base_url = os.environ.get("MODEL_API_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("MODEL_API_KEY", "")
    model = os.environ.get("MODEL_API_NAME", "")
    if not live_configured():
        return None

    grounding = {
        "name": profile.get("name"),
        "intro": profile.get("intro"),
        "persona": profile.get("persona", {}),
        "answers": profile.get("answers", []),
    }
    system_prompt = (
        "你是一个基于已审核生平资料生成的数字纪念形象，用第一人称和家属对话。"
        "只能依据下面的人物资料回答；资料没有提到的内容，要温和地说明不知道或没有留下资料，"
        "不得编造经历、亲属、观点或近况，不得声称自己拥有真实意识或还活着。"
        "语气自然、克制、口语化，每次回答 2 到 4 句话，尽量用短句、一句只说一件事，不要使用列表和标题。"
        "本次对话中对方告诉你的信息（名字、近况、心情）要记住，并在后续回答里自然用上；"
        "这些属于这次聊天，不是你的人生往事，不要把它们说成自己的经历。"
        "人物资料：" + json.dumps(grounding, ensure_ascii=False)
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 300,
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt in range(2):
        try:
            request = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=encoded,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                return None
            choices = data.get("choices", [])
            if not isinstance(choices, list):
                return None
            content = choices[0].get("message", {}).get("content") if choices else data.get("reply")
            if isinstance(content, str) and content.strip():
                return content.strip()
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError, IndexError, KeyError):
            if attempt == 0:
                time.sleep(0.3)
                continue
            return None
    return None


class Handler(SimpleHTTPRequestHandler):
    _static_request = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format_string, *args):
        print(f"[http] {self.address_string()} - {format_string % args}")

    def end_headers(self):
        if self._static_request:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload, status=200):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def access_denied(self, parsed):
        """配置了 ACCESS_CODE 时，/api/* 必须带口令；静态页面不受限制。

        口令可放在 X-Access-Code 请求头，或网址参数 k（方便直接扫码进入）。
        """
        expected = os.environ.get("ACCESS_CODE", "").strip()
        if not expected:
            return False
        if not parsed.path.startswith("/api/"):
            return False
        provided = (self.headers.get("X-Access-Code") or "").strip()
        if not provided:
            provided = parse_qs(parsed.query).get("k", [""])[0].strip()
        return provided != expected

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def send_audio(self, audio, truncated=False):
        """语音合成成功时直接回音频字节，前端用 Audio 元素播放。"""
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-TTS-Truncated", "1" if truncated else "0")
        self.end_headers()
        self.wfile.write(audio)

    def handle_asr(self):
        """接收 16kHz/16bit/单声道 WAV 原始字节，转发一句话识别。"""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json({"ok": False, "error": "empty_audio"}, 400)
            return
        if length > voice.MAX_AUDIO_BYTES:
            self.close_connection = True
            self.send_json({"ok": False, "error": "audio_too_large"}, 400)
            return
        audio = self.rfile.read(length)
        if content_type not in AUDIO_CONTENT_TYPES:
            self.send_json({"ok": False, "error": "unsupported_audio_type"}, 400)
            return
        text = voice.recognize(audio)
        if text is None:
            self.send_json({"ok": False, "error": "asr_unavailable"}, 503)
            return
        self.send_json({"ok": True, "text": text})

    def do_GET(self):
        refresh_dotenv()
        parsed = urlparse(self.path)
        if self.access_denied(parsed):
            self.send_json({"ok": False, "error": "access_denied"}, 403)
            return
        if parsed.path == "/api/health":
            mode = "live" if live_configured() else "demo"
            self.send_json({"ok": True, "mode": mode, "service": "digital-memorial"})
            return
        if parsed.path == "/api/profile":
            requested = parse_qs(parsed.query, keep_blank_values=True).get("id", [DEFAULT_PROFILE_ID])[0]
            try:
                self.send_json(requested_profile(requested))
            except ProfileUnavailable:
                self.send_json({"ok": False, "error": "profile_unavailable"}, 503)
            except ValueError:
                self.send_json({"ok": False, "error": "invalid_profile_id"}, 400)
            except FileNotFoundError:
                self.send_json({"ok": False, "error": "profile_not_found"}, 404)
            return
        if parsed.path == "/api/profiles":
            self.send_json({"ok": True, "profiles": list_profiles()})
            return
        if parsed.path == "/":
            self.redirect("/web/people.html")
            return
        if parsed.path == "/web":
            self.redirect("/web/")
            return
        if parsed.path.startswith("/web/"):
            relative = parsed.path[len("/web"):]
            self.path = relative + (f"?{parsed.query}" if parsed.query else "")
            self._static_request = True
            super().do_GET()
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        refresh_dotenv()
        parsed = urlparse(self.path)
        if self.access_denied(parsed):
            self.send_json({"ok": False, "error": "access_denied"}, 403)
            return
        if parsed.path == "/api/asr":
            # 音频是原始字节而不是 JSON，必须在通用 JSON 解析之前分流。
            self.handle_asr()
            return
        body = self.read_body()
        if not isinstance(body, dict):
            self.send_json({"ok": False, "error": "invalid_json"}, 400)
            return

        if parsed.path == "/api/chat":
            profile_id = body.get("profileId", DEFAULT_PROFILE_ID)
            question = body.get("message", "")
            if not isinstance(profile_id, str) or not PROFILE_PATTERN.fullmatch(profile_id):
                self.send_json({"ok": False, "error": "invalid_profile_id"}, 400)
                return
            if not isinstance(question, str) or not question.strip():
                self.send_json({"ok": False, "error": "empty_message"}, 400)
                return
            question = question.strip()
            if len(question) > MAX_MESSAGE_LENGTH:
                self.send_json({"ok": False, "error": "message_too_long"}, 400)
                return
            try:
                profile = requested_profile(profile_id)
            except ProfileUnavailable:
                self.send_json({"ok": False, "error": "profile_unavailable"}, 503)
                return
            except FileNotFoundError:
                self.send_json({"ok": False, "error": "profile_not_found"}, 404)
                return
            history = clean_history(body.get("history", []))
            live_reply = call_openai_compatible(profile, question, history)
            if live_reply:
                self.send_json({"ok": True, "reply": live_reply, "mode": "live", "source": "model", "fallback": False})
            else:
                self.send_json({
                    "ok": True,
                    "reply": demo_answer(profile, question),
                    "mode": "demo",
                    "source": "approved_profile",
                    "fallback": live_configured(),
                })
            return

        if parsed.path == "/api/tts":
            profile_id = body.get("profileId", DEFAULT_PROFILE_ID)
            text = body.get("text", "")
            if not isinstance(profile_id, str) or not PROFILE_PATTERN.fullmatch(profile_id):
                self.send_json({"ok": False, "error": "invalid_profile_id"}, 400)
                return
            if not isinstance(text, str) or not text.strip():
                self.send_json({"ok": False, "error": "empty_text"}, 400)
                return
            if len(text) > MAX_MESSAGE_LENGTH:
                self.send_json({"ok": False, "error": "text_too_long"}, 400)
                return
            try:
                requested_profile(profile_id)
            except ProfileUnavailable:
                self.send_json({"ok": False, "error": "profile_unavailable"}, 503)
                return
            except FileNotFoundError:
                self.send_json({"ok": False, "error": "profile_not_found"}, 404)
                return
            audio, truncated = voice.synthesize(text)
            if audio:
                self.send_audio(audio, truncated)
                return
            # 未配置或供应商失败都回退到浏览器朗读，用户体验不中断。
            self.send_json({"ok": True, "mode": "browser", "source": "browser_speech", "fallback": True})
            return

        self.send_json({"ok": False, "error": "not_found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="数字纪念形象本地服务")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；局域网手机访问时使用 0.0.0.0")
    # 手机浏览器的麦克风只在安全上下文可用：局域网 http 地址会被直接禁用，需要 https。
    parser.add_argument("--cert", default="", help="HTTPS 证书文件（PEM）；与 --key 同时提供才启用 https")
    parser.add_argument("--key", default="", help="HTTPS 私钥文件（PEM）")
    args = parser.parse_args()
    load_dotenv()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    scheme = "http"
    if args.cert and args.key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    display_host = "localhost" if args.host in {"127.0.0.1", "localhost"} else args.host
    print(f"数字纪念形象服务已启动：{scheme}://{display_host}:{args.port}/web/people.html")
    print(f"当前模式：{'live' if live_configured() else 'demo'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
