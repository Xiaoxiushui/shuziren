"""人物包校验与私有审核记录。运行和交付均只依赖 Python 标准库。"""

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path


PROFILE_PATTERN = re.compile(r"^[a-z0-9-]{1,48}$")
DEFAULT_PROFILE_ID = "lin-shu-demo"
BUNDLED_DEMO_IDS = {DEFAULT_PROFILE_ID, "another-demo"}
PUBLIC_FIELDS = {
    "schemaVersion", "id", "kind", "name", "pronoun", "intro", "source", "voice",
    "persona", "welcome", "suggestedQuestions", "answers", "fallback",
}


class ProfileUnavailable(ValueError):
    def __init__(self, issues):
        super().__init__("profile_unavailable")
        self.issues = issues


def issue(field, code, message):
    return {"field": field, "code": code, "message": message}


def read_json(path):
    return Path(path).read_text(encoding="utf-8-sig")


def load_json(path):
    return json.loads(read_json(path))


def profile_digest(profile):
    encoded = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_profile(profile):
    errors = []
    if not isinstance(profile, dict):
        return [issue("/", "expected_object", "人物包必须是 JSON 对象。")]

    def text(value, field, maximum=500):
        if not isinstance(value, str) or not value.strip():
            errors.append(issue(field, "required_text", "请填写非空文本。"))
            return False
        if len(value) > maximum:
            errors.append(issue(field, "text_too_long", f"文本最长 {maximum} 字符。"))
        return True

    def texts(value, field, maximum_items=20, maximum_length=200):
        if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
            errors.append(issue(field, "invalid_list", f"请填写 1 至 {maximum_items} 条内容。"))
            return False
        for index, item in enumerate(value):
            text(item, f"{field}/{index}", maximum_length)
        return True

    for field in profile.keys() - PUBLIC_FIELDS:
        errors.append(issue(f"/{field}", "unknown_field", "此字段不属于公开人物包；审核或原始素材应单独保存。"))
    if type(profile.get("schemaVersion")) is not int or profile["schemaVersion"] != 1:
        errors.append(issue("/schemaVersion", "unsupported_version", "schemaVersion 必须为 1。"))
    if not isinstance(profile.get("id"), str) or not PROFILE_PATTERN.fullmatch(profile["id"]):
        errors.append(issue("/id", "invalid_profile_id", "ID 只允许 1 至 48 位小写字母、数字和短横线。"))
    if profile.get("kind") not in ("fictional", "real"):
        errors.append(issue("/kind", "invalid_kind", "kind 必须为 fictional 或 real。"))
    for field, maximum in (("name", 40), ("pronoun", 12), ("intro", 500), ("source", 300), ("welcome", 500), ("fallback", 500)):
        text(profile.get(field), f"/{field}", maximum)

    persona = profile.get("persona")
    if not isinstance(persona, dict):
        errors.append(issue("/persona", "expected_object", "请填写语气、习惯和回答边界。"))
    else:
        for field in persona.keys() - {"tone", "habits", "boundaries"}:
            errors.append(issue(f"/persona/{field}", "unknown_field", "人物设定只包含 tone、habits 和 boundaries。"))
        text(persona.get("tone"), "/persona/tone", 200)
        texts(persona.get("habits"), "/persona/habits")
        texts(persona.get("boundaries"), "/persona/boundaries")

    voice = profile.get("voice")
    if not isinstance(voice, dict):
        errors.append(issue("/voice", "expected_object", "请填写浏览器语音配置。"))
    else:
        for field in voice.keys() - {"mode", "voice_id", "rate"}:
            errors.append(issue(f"/voice/{field}", "unknown_field", "语音配置包含未支持字段。"))
        if voice.get("mode") != "browser-demo":
            errors.append(issue("/voice/mode", "unsupported_voice", "当前仅支持 browser-demo；真实 TTS 尚未接入。"))
        rate = voice.get("rate")
        if type(rate) not in (int, float) or not 0.5 <= rate <= 1.5 or not math.isfinite(rate):
            errors.append(issue("/voice/rate", "invalid_rate", "语速必须是 0.5 至 1.5 的数字。"))
        voice_id = voice.get("voice_id")
        if voice_id is not None and (not isinstance(voice_id, str) or len(voice_id) > 200):
            errors.append(issue("/voice/voice_id", "invalid_voice_id", "voice_id 应为空或不超过 200 字符的文本。"))

    texts(profile.get("suggestedQuestions"), "/suggestedQuestions", maximum_items=6, maximum_length=120)
    answers = profile.get("answers")
    if not isinstance(answers, list) or not 1 <= len(answers) <= 100:
        errors.append(issue("/answers", "invalid_list", "请填写 1 至 100 条审核回答。"))
    else:
        for index, answer in enumerate(answers):
            field = f"/answers/{index}"
            if not isinstance(answer, dict):
                errors.append(issue(field, "expected_object", "回答必须包含 keys 和 text。"))
                continue
            for key in answer.keys() - {"keys", "text"}:
                errors.append(issue(f"{field}/{key}", "unknown_field", "回答仅包含 keys 和 text；来源记录请放入审核文件。"))
            texts(answer.get("keys"), f"{field}/keys", maximum_items=20, maximum_length=40)
            text(answer.get("text"), f"{field}/text")
    if not errors:
        for index, question in enumerate(profile["suggestedQuestions"]):
            if not any(key in question for answer in answers for key in answer["keys"]):
                errors.append(issue(f"/suggestedQuestions/{index}", "unmatched_question", "建议问题没有命中任何审核回答，请补充对应关键词或回答。"))
    return errors


def review_fields(profile):
    fields = [f"/{field}" for field in ("name", "pronoun", "intro", "source", "welcome", "fallback")]
    fields.append("/persona/tone")
    for name in ("habits", "boundaries"):
        fields.extend(f"/persona/{name}/{index}" for index in range(len(profile["persona"][name])))
    fields.extend(f"/suggestedQuestions/{index}" for index in range(len(profile["suggestedQuestions"])))
    fields.extend(f"/answers/{index}/text" for index in range(len(profile["answers"])))
    return fields


def review_template(profile):
    errors = validate_profile(profile)
    if errors:
        raise ProfileUnavailable(errors)
    return {
        "schemaVersion": 1,
        "profileId": profile["id"],
        "kind": profile["kind"],
        "profileDigest": profile_digest(profile),
        "status": "pending",
        "reviewer": "",
        "reviewedAt": "",
        "authorization": {
            "status": "not_required" if profile["kind"] == "fictional" else "pending",
            "recordRef": "",
        },
        "items": [{"field": field, "status": "pending", "sourceRef": ""} for field in review_fields(profile)],
    }


def validate_review(profile, review):
    errors = validate_profile(profile)
    if errors:
        return errors
    if not isinstance(review, dict):
        return [issue("/review", "review_required", "缺少与当前人物包对应的审核记录。")]
    for field, expected in (("schemaVersion", 1), ("profileId", profile["id"]), ("kind", profile["kind"]), ("profileDigest", profile_digest(profile))):
        if review.get(field) != expected or (field == "schemaVersion" and type(review.get(field)) is not int):
            errors.append(issue(f"/review/{field}", "review_mismatch", "审核记录与当前人物版本不匹配，请重新生成审核清单并复核。"))
    if review.get("status") != "approved":
        errors.append(issue("/review/status", "not_approved", "审核总状态尚未通过。"))
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        errors.append(issue("/review/reviewer", "reviewer_required", "请登记实际审核人。"))
    try:
        reviewed_at = datetime.fromisoformat(review.get("reviewedAt", ""))
        if reviewed_at.tzinfo is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError):
        errors.append(issue("/review/reviewedAt", "review_date_required", "请填写带时区的审核时间，例如 2026-11-01T22:00:00+08:00。"))
    authorization = review.get("authorization")
    if not isinstance(authorization, dict):
        authorization = {}
    if profile["kind"] == "real":
        if authorization.get("status") != "confirmed":
            errors.append(issue("/review/authorization/status", "authorization_pending", "真实人物的授权记录尚未确认。"))
        if not isinstance(authorization.get("recordRef"), str) or not authorization["recordRef"].strip():
            errors.append(issue("/review/authorization/recordRef", "authorization_reference_required", "请填写已归档的授权记录编号。"))
    elif authorization.get("status") != "not_required":
        errors.append(issue("/review/authorization/status", "fictional_authorization", "虚构人物应标记 not_required。"))
    items = review.get("items")
    if not isinstance(items, list):
        items = []
    reviewed_fields = {}
    expected_fields = set(review_fields(profile))
    for index, item in enumerate(items):
        field = f"/review/items/{index}"
        if not isinstance(item, dict) or not isinstance(item.get("field"), str):
            errors.append(issue(field, "invalid_review_item", "审核条目必须指定 field。"))
            continue
        target = item["field"]
        if target not in expected_fields or target in reviewed_fields:
            errors.append(issue(field, "invalid_review_field", "审核字段不在当前清单内，或重复出现。"))
        reviewed_fields[target] = item
    for target in review_fields(profile):
        item = reviewed_fields.get(target, {})
        if item.get("status") != "approved":
            errors.append(issue(target, "field_not_approved", "此项尚未逐条审核通过。"))
        if not isinstance(item.get("sourceRef"), str) or not item["sourceRef"].strip():
            errors.append(issue(target, "source_reference_required", "请登记此项的来源或复核记录编号。"))
    return errors


def public_profile(profile):
    return {field: value for field, value in profile.items() if field in PUBLIC_FIELDS}


class ProfileRepository:
    def __init__(self, root):
        self.root = Path(root)
        self.profiles_dir = self.root / "profiles"
        self.reviews_dir = self.root / "reviews"

    def profile_path(self, profile_id):
        if not isinstance(profile_id, str) or not PROFILE_PATTERN.fullmatch(profile_id):
            raise ValueError("invalid_profile_id")
        direct = self.profiles_dir / f"{profile_id}.json"
        legacy = self.profiles_dir / "demo.json"
        if profile_id == DEFAULT_PROFILE_ID and direct.is_file() and legacy.is_file():
            raise ProfileUnavailable([issue("/id", "duplicate_id", "默认人物存在两个文件，请只保留一个版本。")])
        if direct.is_file():
            return direct
        if profile_id == DEFAULT_PROFILE_ID and legacy.is_file():
            return legacy
        raise FileNotFoundError("profile_not_found")

    def load(self, profile_id):
        path = self.profile_path(profile_id)
        try:
            profile = load_json(path)
        except (OSError, ValueError, UnicodeError):
            raise ProfileUnavailable([issue("/", "unreadable_profile", "人物包无法读取或不是有效 JSON。")]) from None
        errors = validate_profile(profile)
        if not errors and profile["id"] != profile_id:
            errors.append(issue("/id", "profile_id_mismatch", "文件中的人物 ID 与请求不一致。"))
        if errors:
            raise ProfileUnavailable(errors)
        review_path = self.reviews_dir / f"{profile_id}.json"
        if profile["kind"] == "real" or profile_id not in BUNDLED_DEMO_IDS or review_path.exists():
            try:
                review = load_json(review_path)
            except (OSError, ValueError, UnicodeError):
                review = None
            errors = validate_review(profile, review)
            if errors:
                raise ProfileUnavailable(errors)
        return public_profile(profile)

    def inspect(self):
        records = []
        seen = set()
        for path in sorted(self.profiles_dir.glob("*.json")):
            profile_id = None
            try:
                raw = load_json(path)
                errors = validate_profile(raw)
                if not errors:
                    profile_id = raw["id"]
                    expected_name = f"{profile_id}.json"
                    if path.name != expected_name and not (path.name == "demo.json" and profile_id == DEFAULT_PROFILE_ID):
                        errors.append(issue("/id", "filename_mismatch", "文件名应与人物 ID 一致。"))
                    if not errors:
                        self.load(profile_id)
                    if profile_id in seen:
                        errors.append(issue("/id", "duplicate_id", "人物 ID 重复。"))
                    seen.add(profile_id)
            except ProfileUnavailable as error:
                errors = error.issues
            except (OSError, ValueError, UnicodeError):
                errors = [issue("/", "unreadable_profile", "人物包无法读取或不是有效 JSON。")]
            records.append({"file": path.name, "id": profile_id, "ok": not errors, "issues": errors})
        return records

    def list_profiles(self):
        items = []
        for record in self.inspect():
            if record["ok"]:
                try:
                    profile = self.load(record["id"])
                except (OSError, ValueError):
                    continue
                items.append({field: profile[field] for field in ("id", "name", "intro")})
        return items
