import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlencode
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 4174


def access_code():
    if "ACCESS_CODE" in os.environ:
        return os.environ["ACCESS_CODE"].strip()
    try:
        contents = (ROOT / "server" / ".env").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""
    for raw in contents.splitlines():
        key, separator, value = raw.strip().partition("=")
        if separator and key.strip() == "ACCESS_CODE":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value
    return ""


def health_ok(port):
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        code = access_code()
        if code:
            request.add_header("X-Access-Code", code)
        with urllib.request.urlopen(request, timeout=1) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and isinstance(body, dict) and body.get("service") == "digital-memorial"
    except (OSError, ValueError):
        return False


def lan_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def pick_port(preferred):
    for port in range(preferred, preferred + 20):
        if health_ok(port):
            return port, True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port, False
    raise SystemExit("找不到可用端口，请关闭占用 4174-4193 的程序后重试。")


def make_qr(url, output):
    try:
        import qrcode
    except ImportError:
        print("提示：未安装 qrcode，跳过二维码生成。")
        print('可运行：py -3 -m pip install "qrcode[pil]"')
        return None
    image = qrcode.make(url)
    image.save(output)
    return output


def main():
    parser = argparse.ArgumentParser(description="一键启动数字纪念形象")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--profile", default=None, help="直接打开指定人物；不填则打开人物列表")
    parser.add_argument("--no-open", action="store_true", help="只启动，不自动打开浏览器和二维码")
    args = parser.parse_args()

    port, reused = pick_port(args.port)
    child = None
    if not reused:
        child = subprocess.Popen(
            [sys.executable, str(ROOT / "server" / "server.py"), "--host", "0.0.0.0", "--port", str(port)],
            cwd=str(ROOT),
        )
        for _ in range(40):
            if health_ok(port):
                break
            time.sleep(0.25)
        else:
            child.terminate()
            raise SystemExit("服务启动失败，请检查 Python 是否可用。")

    host = lan_ip()
    entry = f"/web/?profile={args.profile}" if args.profile else "/web/people.html"
    phone_url = f"http://{host}:{port}{entry}"
    local_url = f"http://127.0.0.1:{port}{entry}"
    code = access_code()
    if code:
        query = urlencode({"k": code})
        phone_url += ("&" if "?" in phone_url else "?") + query
        local_url += ("&" if "?" in local_url else "?") + query
    qr_path = ROOT / "手机扫码.png"
    make_qr(phone_url, qr_path)

    print("")
    print("=" * 52)
    print("  数字纪念形象已启动")
    print("=" * 52)
    print(f"  电脑打开：http://127.0.0.1:{port}{entry}")
    print(f"  手机打开：http://{host}:{port}{entry}")
    if code:
        print("  已启用访问口令，请使用自动打开的页面或二维码进入。")
    print("  手机和电脑必须连同一个 Wi-Fi")
    print("  二维码：手机扫码.png（已自动打开）")
    print("=" * 52)
    print("  手机打不开时依次检查：")
    print("  1. 手机没有用移动数据，确实连着同一个 Wi-Fi")
    print("  2. 路由器关闭了“无线设备隔离 / AP 隔离”")
    print("  3. 换用手机热点：电脑连手机热点后重新双击启动")
    print("  4. Windows 防火墙弹窗时选择“允许访问”")
    print("  按 Ctrl+C 停止服务")
    print("=" * 52)

    if not args.no_open:
        webbrowser.open(local_url)
        if qr_path.is_file():
            os.startfile(qr_path)

    try:
        if child is not None:
            child.wait()
        else:
            print("检测到服务已在运行，本窗口只负责显示访问地址。")
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\n正在停止服务…")
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
        print("服务已停止。")


if __name__ == "__main__":
    main()
