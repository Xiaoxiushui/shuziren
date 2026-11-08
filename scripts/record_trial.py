import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def request(url, method="GET", payload=None, expect_json=True):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    started = time.perf_counter()
    try:
        request_obj = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request_obj, timeout=15) as response:
            body = response.read()
            raw_body = body.decode("utf-8")
            result = {
                "ok": True,
                "status": response.status,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            if expect_json:
                result["body"] = json.loads(raw_body) if body else None
            else:
                result["body_size"] = len(body)
                result["contains_html"] = "<html" in raw_body.lower()
            return result
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return {"ok": False, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description="记录虚构人物 Demo 的服务级试点指标")
    parser.add_argument("--base-url", default="http://localhost:4174")
    parser.add_argument("--profile", default="another-demo")
    parser.add_argument("--out", default="out/trial-another-demo.json")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    entry_results = [request(f"{base}/web/?profile={args.profile}", expect_json=False) for _ in range(3)]
    profile_result = request(f"{base}/api/profile?id={args.profile}")
    questions = ["你喜欢做什么？", "如果给家人留一句话，你会说什么？"]
    chat_results = [
        request(f"{base}/api/chat", "POST", {"profileId": args.profile, "message": question, "history": []})
        for question in questions
    ]
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "base_url": base,
        "scope": "服务级虚构人物试点，不代表手机实际扫码结果",
        "entry_attempts": len(entry_results),
        "entry_successes": sum(1 for item in entry_results if item["ok"] and item.get("status") == 200),
        "entry_results": entry_results,
        "profile_result": profile_result,
        "chat_results": chat_results,
        "chat_successes": sum(1 for item in chat_results if item["ok"] and item.get("body", {}).get("ok")),
        "notes": [
            "素材处理耗时需要在真实交付中人工记录。",
            "失真问题需要由体验者逐条记录。",
            "手机实际访问已由项目负责人确认；二维码打印和现场扫码结果需要另行补录。",
        ],
    }
    output = (ROOT / args.out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入：{output}")
    print(json.dumps({"entry_successes": record["entry_successes"], "entry_attempts": record["entry_attempts"], "chat_successes": record["chat_successes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
