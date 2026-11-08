import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from profile_store import DEFAULT_PROFILE_ID, ProfileRepository, ProfileUnavailable, load_json, validate_review

INCLUDE = [
    "README.md", "requirements.txt", ".gitignore", "一键启动.cmd",
    "docs", "scripts", "server", "web", "tests",
]
EXCLUDED_NAMES = {".env", "__pycache__", "out", ".git"}
EXCLUDED_SUFFIXES = {".pyc"}


def should_include(path):
    if any(part in EXCLUDED_NAMES for part in path.parts):
        return False
    if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
        return False
    if any(part.startswith(".env.") and part != ".env.example" for part in path.parts):
        return False
    return path.is_file()


def main(argv=None):
    parser = argparse.ArgumentParser(description="打包数字纪念形象 Demo")
    parser.add_argument("--out", default="out/digital-memorial-demo.zip")
    parser.add_argument("--profile", help="只交付指定人物；默认仅包含林叔和周姨两位演示人物")
    args = parser.parse_args(argv)
    repository = ProfileRepository(ROOT)
    selected_ids = [args.profile] if args.profile else [DEFAULT_PROFILE_ID, "another-demo"]
    selected_files = []
    try:
        for profile_id in selected_ids:
            profile = repository.load(profile_id)
            if not args.profile and profile["kind"] != "fictional":
                raise ValueError("默认演示人物已改为真实人物，请使用 --profile 明确指定交付对象。")
            selected_files.append(repository.profile_path(profile_id))
            review_path = repository.reviews_dir / f"{profile_id}.json"
            if review_path.is_file():
                errors = validate_review(profile, load_json(review_path))
                if errors:
                    raise ProfileUnavailable(errors)
                selected_files.append(review_path)
    except ProfileUnavailable as error:
        print("人物资料或审核记录未通过，未生成交付包：", file=sys.stderr)
        for item in error.issues:
            print(f"  {item['field']}: {item['message']}", file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError) as error:
        print(f"无法打包：{error}", file=sys.stderr)
        return 1
    destination = (ROOT / args.out).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = list(selected_files)
    for relative_root in INCLUDE:
        root = ROOT / relative_root
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = root.rglob("*")
        else:
            continue
        files.extend(path for path in candidates if should_include(path))
    files = sorted(set(files))
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profiles": selected_ids,
        "file_count": len(files),
        "files": [path.relative_to(ROOT).as_posix() for path in files],
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr("PACKAGE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"已生成：{destination}")
    print(f"文件数：{len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
