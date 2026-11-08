r"""把同一句话用不同发音人/语速合成出来，方便用耳朵挑音色。

用法：
    py -3 scripts\sample_voices.py
    py -3 scripts\sample_voices.py --text "自定义句子"

输出目录：out\voice-samples
只使用标准库，直接读取 server\.env 里的 NLS_* 配置。
"""
import argparse
import json
import re
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "server" / ".env"
OUT_DIR = ROOT / "out" / "voice-samples"
TTS_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"
DEFAULT_TEXT = "回来啦。慢慢说，我在这儿听着。今天不着急，我们一句一句说。"

# 常见普通话发音人，账号没有的会自动跳过。
VOICES = [
    "xiaoyun", "aixia", "aimei", "siqi", "sitong", "sijing",
    "sicheng", "aibo", "aiye", "aijia", "ruoxi", "zhiyan",
]
RATES = [0, -100, -200]


def read_env():
    config = {}
    if not ENV_PATH.exists():
        return config
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def synth(appkey, token, text, voice, rate):
    payload = {
        "appkey": appkey,
        "token": token,
        "text": text,
        "format": "wav",
        "sample_rate": 16000,
        "voice": voice,
        "speech_rate": rate,
    }
    request = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        audio = response.read()
    return audio if audio.startswith(b"RIFF") else None


def seconds(audio):
    if len(audio) < 44:
        return 0.0
    data_size = struct.unpack("<I", audio[40:44])[0]
    return round(data_size / (16000 * 2), 1)


def main():
    parser = argparse.ArgumentParser(description="批量试听发音人")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--rates", default="0,-100,-200", help="要试的语速，逗号分隔")
    args = parser.parse_args()

    config = read_env()
    appkey, token = config.get("NLS_APPKEY", ""), config.get("NLS_TOKEN", "")
    if not appkey or not token:
        print("server/.env 里缺少 NLS_APPKEY 或 NLS_TOKEN。")
        return 2

    rates = [int(value) for value in args.rates.split(",") if value.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.wav"):
        old.unlink()

    rows, ok = [], 0
    for voice in VOICES:
        for rate in rates:
            name = f"{re.sub(r'[^a-z0-9]', '', voice)}_rate{rate}.wav"
            try:
                audio = synth(appkey, token, args.text, voice, rate)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                print(f"跳过 {voice} rate={rate}：{error}")
                continue
            if not audio:
                print(f"跳过 {voice} rate={rate}：返回内容不是音频（该账号可能没有这个发音人）")
                continue
            (OUT_DIR / name).write_bytes(audio)
            rows.append((name, voice, rate, seconds(audio)))
            ok += 1

    index = OUT_DIR / "试听清单.txt"
    index.write_text(
        "\n".join(f"{name}\t发音人={voice}\t语速={rate}\t时长={length}秒" for name, voice, rate, length in rows) or "没有任何音色合成成功。",
        encoding="utf-8",
    )
    print(f"成功 {ok} 个，文件在 {OUT_DIR}")
    for name, voice, rate, length in rows[:6]:
        print(f"  {name}  发音人={voice} 语速={rate} 时长={length}秒")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
