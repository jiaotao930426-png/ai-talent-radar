import re
import urllib.parse
from typing import Any, Dict, Optional, Tuple


CONTACT_LEVEL_LABELS = {
    "A": "已核验邮箱",
    "B": "公开邮箱待复核",
    "C": "其他公开入口待核验",
    "D": "仅公开主页",
}
CONTACT_LEVEL_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
MATCH_HIGH_SCORE = 82
MATCH_MEDIUM_SCORE = 70
PUBLIC_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)


def _public_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if (
        len(email) > 320
        or any(character in email for character in "\r\n\x00")
        or not PUBLIC_EMAIL_PATTERN.fullmatch(email)
        or "noreply" in email
    ):
        return ""
    return email


def _public_url_identity(value: Any) -> Optional[Tuple[str, str, str]]:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
    )


def derive_contact_level(candidate: Dict[str, Any]) -> str:
    email = _public_email(candidate.get("contact_email"))
    email_source = _public_url_identity(candidate.get("contact_email_source_url"))
    email_verified_at = str(candidate.get("contact_email_verified_at") or "").strip()
    profile_url = _public_url_identity(candidate.get("profile_url"))
    contact_url = _public_url_identity(candidate.get("contact_url"))
    if email and email_source and email_verified_at:
        return "A"
    if email:
        return "B"
    if contact_url and contact_url != profile_url:
        return "C"
    return "D"


def contact_label(level: Any) -> str:
    normalized = str(level or "D").upper()
    return CONTACT_LEVEL_LABELS.get(normalized, CONTACT_LEVEL_LABELS["D"])


def contact_rank(candidate: Dict[str, Any]) -> int:
    level = str(candidate.get("contact_level") or derive_contact_level(candidate)).upper()
    return CONTACT_LEVEL_RANK.get(level, CONTACT_LEVEL_RANK["D"])


def match_tier(score: Any) -> int:
    value = int(score or 0)
    if value >= MATCH_HIGH_SCORE:
        return 2
    if value >= MATCH_MEDIUM_SCORE:
        return 1
    return 0


def candidate_priority_key(
    candidate: Dict[str, Any], prefer_contactable: bool = True
) -> Tuple[int, int, int, str, str]:
    score = int(candidate.get("match_score") or 0)
    contact_priority = contact_rank(candidate) if prefer_contactable else 0
    return (
        -match_tier(score),
        -contact_priority,
        -score,
        str(candidate.get("source") or ""),
        str(candidate.get("external_id") or candidate.get("id") or ""),
    )


def has_direct_contact(candidate: Dict[str, Any]) -> bool:
    return contact_rank(candidate) > CONTACT_LEVEL_RANK["D"]
