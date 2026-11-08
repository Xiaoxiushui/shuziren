"""先填草稿，再逐条审核，最后发布到人物列表。"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from profile_store import (  # noqa: E402
    PROFILE_PATTERN, ProfileRepository, ProfileUnavailable, issue, load_json,
    profile_digest, review_template, validate_profile, validate_review,
)


def atomic_write(path, contents):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def draft_directory(value):
    return (ROOT / value).resolve()


def inspect_draft(directory):
    try:
        profile = load_json(directory / "profile.json")
    except (OSError, ValueError, UnicodeError):
        return None, None, [issue("/", "unreadable_profile", "无法读取草稿 profile.json。")]
    errors = validate_profile(profile)
    if errors:
        return profile, None, errors
    try:
        review = load_json(directory / "review.json")
    except (OSError, ValueError, UnicodeError):
        review = None
    return profile, review, validate_review(profile, review)


def create_draft(profile_id, kind, destination, name=""):
    if not PROFILE_PATTERN.fullmatch(profile_id):
        raise ValueError("ID 只允许 1 至 48 位小写字母、数字和短横线。")
    profile = {
        "schemaVersion": 1, "id": profile_id, "kind": kind, "name": name, "pronoun": "TA",
        "intro": "", "source": "虚构演示人物" if kind == "fictional" else "",
        "voice": {"mode": "browser-demo", "voice_id": None, "rate": 0.95},
        "persona": {
            "tone": "温和、克制、口语化",
            "habits": [""],
            "boundaries": ["不编造未提供的生平事实", "不声称自己是真人或具有真实意识", "不描述虚构的实时近况"],
        },
        "welcome": "", "suggestedQuestions": [""],
        "answers": [{"keys": [], "text": ""}],
        "fallback": "这个问题没有留下明确的资料，我不能替你编写。可以聊聊资料中记录的生活习惯。",
    }
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "profile.json", profile)
    return destination / "profile.json"


def publish_draft(directory, replace=False):
    profile, review, errors = inspect_draft(directory)
    if errors:
        raise ProfileUnavailable(errors)
    repository = ProfileRepository(ROOT)
    profile_id = profile["id"]
    try:
        target = repository.profile_path(profile_id)
    except FileNotFoundError:
        target = repository.profiles_dir / f"{profile_id}.json"
    review_target = repository.reviews_dir / f"{profile_id}.json"
    if (target.exists() or review_target.exists()) and not replace:
        raise ValueError("此人物已有发布文件。更新请使用 --replace，旧版本会自动归档。")
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + profile_digest(profile)[:12]
    archive = ROOT / "profile-versions" / profile_id / version
    archive.mkdir(parents=True, exist_ok=False)
    write_json(archive / "profile.json", profile)
    write_json(archive / "review.json", review)
    for source, name in ((target, "previous-profile.json"), (review_target, "previous-review.json")):
        if source.exists():
            shutil.copy2(source, archive / name)
    previous_review = review_target.read_bytes() if review_target.exists() else None
    write_json(review_target, review)
    try:
        write_json(target, profile)
    except OSError:
        if previous_review is None:
            review_target.unlink(missing_ok=True)
        else:
            atomic_write(review_target, previous_review)
        raise
    return {"id": profile_id, "profile": str(target), "archive": str(archive), "digest": profile_digest(profile)}


def print_issues(errors):
    for error in errors:
        print(f"  {error['field']}: {error['message']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="数字纪念人物：草稿、审核、校验与发布")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="创建未发布的空白素材草稿")
    init.add_argument("--id", required=True)
    init.add_argument("--kind", required=True, choices=("fictional", "real"))
    init.add_argument("--name", default="")
    init.add_argument("--out", help="草稿目录；默认 drafts/<id>")
    review = commands.add_parser("review", help="为填好的草稿生成待审核清单，不自动批准")
    review.add_argument("--draft", required=True)
    review.add_argument("--reset-review", action="store_true", help="重新生成清单，旧审核结果将被归档")
    check = commands.add_parser("check", help="检查已发布人物，或检查草稿是否可以发布")
    selection = check.add_mutually_exclusive_group()
    selection.add_argument("--draft")
    selection.add_argument("--all", action="store_true")
    check.add_argument("--json", action="store_true", help="输出不含素材正文的 JSON 报告")
    publish = commands.add_parser("publish", help="发布已审核的当前版本，自动归档版本")
    publish.add_argument("--draft", required=True)
    publish.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if not PROFILE_PATTERN.fullmatch(args.id):
                parser.error("ID 只允许 1 至 48 位小写字母、数字和短横线。")
            path = create_draft(args.id, args.kind, draft_directory(args.out or f"drafts/{args.id}"), args.name)
            print(f"已创建草稿：{path}\n尚未加入人物列表。填好资料后运行 review 生成待审核清单。")
        elif args.command == "review":
            directory = draft_directory(args.draft)
            profile = load_json(directory / "profile.json")
            template = review_template(profile)
            target = directory / "review.json"
            if target.exists():
                if not args.reset_review:
                    raise ValueError("审核清单已存在。资料改动后可用 --reset-review 归档旧清单并重新审核。")
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = directory / "review-history" / f"{stamp}.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            write_json(target, template)
            print(f"已生成待审核清单：{target}\n逐条核对来源后填写审核状态、审核人和时间；此命令不会自动批准。")
        elif args.command == "check":
            if args.draft:
                profile, _, errors = inspect_draft(draft_directory(args.draft))
                report = {"ok": not errors, "scope": "draft", "id": profile.get("id") if isinstance(profile, dict) else None, "issues": errors}
            else:
                records = ProfileRepository(ROOT).inspect()
                report = {"ok": bool(records) and all(record["ok"] for record in records), "scope": "published", "profiles": records}
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif args.draft:
                print("草稿可以发布。" if report["ok"] else "草稿尚不能发布：")
                print_issues(report["issues"])
            else:
                print(f"已检查 {len(report['profiles'])} 个人物包：{'通过' if report['ok'] else '未通过'}")
                for record in report["profiles"]:
                    print(f"{record['file']}: {'通过' if record['ok'] else '未通过'}")
                    print_issues(record["issues"])
            return 0 if report["ok"] else 1
        else:
            result = publish_draft(draft_directory(args.draft), args.replace)
            print(f"已发布：{result['id']}\n人物包：{result['profile']}\n版本归档：{result['archive']}")
        return 0
    except ProfileUnavailable as error:
        print("操作未完成，人物资料或审核记录未通过：", file=sys.stderr)
        for item in error.issues:
            print(f"  {item['field']}: {item['message']}", file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError) as error:
        print(f"操作未完成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
