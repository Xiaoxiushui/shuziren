import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from profile_store import PROFILE_PATTERN, ProfileRepository, ProfileUnavailable, profile_digest


def load_profile(profile_id):
    return ProfileRepository(ROOT).load(profile_id)


def write_qr(url, output):
    try:
        import qrcode
    except ImportError as error:
        raise RuntimeError(
            "生成 PNG 二维码需要 qrcode 包。先执行：py -3 -m pip install qrcode[pil]"
        ) from error
    image = qrcode.make(url)
    image.save(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成数字纪念形象专属链接和二维码")
    parser.add_argument("--profile", required=True, help="人物 ID，例如 lin-shu-demo")
    parser.add_argument("--base-url", required=True, help="部署后的 H5 地址，例如 https://example.com/web/")
    parser.add_argument("--out", default="out", help="输出目录")
    parser.add_argument("--access-code", default="", help="可选：服务端开启了 ACCESS_CODE 时，把口令拼进链接，扫码即可直接使用")
    args = parser.parse_args(argv)

    if not PROFILE_PATTERN.fullmatch(args.profile):
        parser.error("profile 必须只包含小写字母、数字和短横线")
    try:
        base = urlsplit(args.base_url)
        if base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password or base.query or base.fragment:
            parser.error("base-url 必须是没有账号、查询参数或片段的 HTTP/HTTPS 页面目录地址。")
        base.port
        profile = load_profile(args.profile)
    except ProfileUnavailable as error:
        print("人物包未通过校验，未生成链接：", file=sys.stderr)
        for item in error.issues:
            print(f"  {item['field']}: {item['message']}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"无法生成链接：{error}", file=sys.stderr)
        return 1
    url = f"{args.base_url.rstrip('/')}/?profile={quote(args.profile)}"
    if args.access_code:
        url += f"&k={quote(args.access_code)}"
    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    url_file = out_dir / f"{args.profile}.url.txt"
    manifest_file = out_dir / f"{args.profile}.json"
    qr_file = out_dir / f"{args.profile}.png"
    url_file.write_text(url + "\n", encoding="utf-8")
    manifest_file.write_text(
        json.dumps({"profile": args.profile, "name": profile["name"], "kind": profile["kind"], "profile_digest": profile_digest(profile), "url": url}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        write_qr(url, qr_file)
        print(f"已生成二维码：{qr_file}")
    except RuntimeError as error:
        print(error, file=sys.stderr)
        print(f"已生成链接清单：{manifest_file}")
        return 2
    print(f"人物：{profile.get('name')}")
    print(f"链接：{url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
