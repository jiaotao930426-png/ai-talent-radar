import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROLE_TERMS = {
    "AI Agent 工程师": {
        "agent": 9,
        "multi-agent": 10,
        "multi agent": 10,
        "langgraph": 9,
        "tool call": 8,
        "planning": 7,
        "memory": 5,
        "mcp": 7,
        "rag": 4,
        "workflow": 4,
        "智能体": 9,
    },
    "AI Coding 工程师": {
        "agentic coding": 14,
        "coding agent": 14,
        "code agent": 12,
        "codex": 12,
        "claude code": 12,
        "developer tool": 8,
        "ide": 6,
        "代码": 5,
        "mcp": 5,
        "agent": 4,
    },
    "AI 产品经理": {
        "ai product": 12,
        "agent builder": 12,
        "product": 7,
        "workflow": 6,
        "dify": 5,
        "coze": 5,
        "用户": 4,
        "产品": 8,
        "agent": 5,
    },
}

CITY_TOKENS = {
    "北京": ("北京", "beijing", "peking"),
    "重庆": ("重庆", "chongqing", "chong qing"),
}

EDUCATION_PATTERNS = (
    "ph.d",
    "phd",
    "博士",
    "master",
    "硕士",
    "bachelor",
    "本科",
    "b.e.",
    "b.s.",
    "university",
    "大学",
)


def normalized_text(*values: Any) -> str:
    return " ".join(str(value or "").lower() for value in values)


def city_matches(location: str, city: str) -> bool:
    if not city or city == "全部":
        return True
    text = normalized_text(location)
    return any(token in text for token in CITY_TOKENS.get(city, (city.lower(),)))


def detect_city(location: str) -> str:
    for city in ("北京", "重庆"):
        if city_matches(location, city):
            return city
    return "待核验"


def education_status(text: str) -> str:
    lower = normalized_text(text)
    if any(pattern in lower for pattern in EDUCATION_PATTERNS):
        return "公开资料含学历/高校信息，待本人确认"
    return "待核验"


def evidence_relevance(evidence: Dict[str, Any]) -> int:
    text = normalized_text(evidence.get("title"), evidence.get("description"))
    score = 0
    for terms in ROLE_TERMS.values():
        score = max(score, sum(weight for term, weight in terms.items() if term in text))
    if not evidence.get("is_fork"):
        score += 6
    stars = int(evidence.get("stars") or 0)
    if stars >= 100:
        score += 7
    elif stars >= 10:
        score += 4
    elif stars >= 1:
        score += 2
    return score


def template_evidence_relevance(
    evidence: Dict[str, Any], template: Dict[str, Any]
) -> int:
    """Rank public evidence by the selected template instead of legacy roles."""
    text = normalized_text(evidence.get("title"), evidence.get("description"))
    score = 0
    term_weights = {
        "required_terms": 14,
        "preferred_terms": 10,
        "evidence_terms": 9,
        "synonyms": 7,
    }
    for key, weight in term_weights.items():
        score += sum(
            weight for term in template.get(key, []) if str(term).lower() in text
        )
    score -= sum(
        12
        for term in template.get("exclude_terms", [])
        if str(term).lower() in text
    )
    if not evidence.get("is_fork"):
        score += 6
    stars = int(evidence.get("stars") or 0)
    if stars >= 100:
        score += 7
    elif stars >= 10:
        score += 4
    elif stars >= 1:
        score += 2
    return score


def score_candidate(
    profile: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    requested_role: str = "全部",
    requested_city: str = "全部",
    role_template: Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, List[Dict[str, Any]]]:
    profile_text = normalized_text(
        profile.get("bio"),
        profile.get("company"),
        profile.get("display_name"),
        profile.get("username"),
    )
    if role_template:
        return _score_with_template(
            profile,
            evidence,
            profile_text,
            requested_role,
            requested_city,
            role_template,
        )
    ranked = sorted(evidence, key=evidence_relevance, reverse=True)
    evidence_text = normalized_text(
        *[
            "{} {}".format(item.get("title") or "", item.get("description") or "")
            for item in ranked[:12]
        ]
    )
    combined = "{} {}".format(profile_text, evidence_text)

    role_scores: Dict[str, int] = {}
    for role, terms in ROLE_TERMS.items():
        role_scores[role] = sum(weight for term, weight in terms.items() if term in combined)

    if requested_role in ROLE_TERMS:
        suggested_role = requested_role
    else:
        suggested_role = max(role_scores, key=role_scores.get)

    # Keep the established score scale without using contact details as a signal.
    score = 29
    score += min(role_scores[suggested_role], 38)
    relevant_original = [item for item in ranked if evidence_relevance(item) >= 10 and not item.get("is_fork")]
    score += min(len(relevant_original) * 5, 20)

    city = profile.get("city") or "待核验"
    if requested_city in ("北京", "重庆"):
        score += 10 if city == requested_city else 0
    elif city in ("北京", "重庆"):
        score += 8

    if profile.get("source_updated_at"):
        score += 2

    return min(score, 100), suggested_role, ranked[:6]


def _score_with_template(
    profile: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    profile_text: str,
    requested_role: str,
    requested_city: str,
    template: Dict[str, Any],
) -> Tuple[int, str, List[Dict[str, Any]]]:
    """Score a candidate against a user-owned template without changing legacy rules."""
    ranked = sorted(
        evidence,
        key=lambda item: template_evidence_relevance(item, template),
        reverse=True,
    )
    evidence_text = normalized_text(
        *[
            "{} {}".format(item.get("title") or "", item.get("description") or "")
            for item in ranked[:12]
        ]
    )
    combined = "{} {}".format(profile_text, evidence_text)

    def hits(key: str) -> int:
        return sum(1 for term in template.get(key, []) if str(term).lower() in combined)

    required = hits("required_terms")
    preferred = hits("preferred_terms")
    synonym_hits = hits("synonyms")
    evidence_hits = hits("evidence_terms")
    excluded = hits("exclude_terms")
    weights = template.get("weights") or {}
    required_terms = template.get("required_terms") or []
    # A missing required term is a meaningful mismatch, but an empty required list
    # keeps templates useful for exploratory searches.
    required_score = min(required * int(weights.get("required", 10)), 35)
    preferred_score = min(
        (preferred + synonym_hits) * int(weights.get("preferred", 8)), 24
    )
    evidence_score = min(evidence_hits * int(weights.get("evidence", 7)), 20)
    exclude_penalty = min(excluded * int(weights.get("exclude", 15)), 40)
    score = 20 + required_score + preferred_score + evidence_score
    if required_terms and required == 0:
        score -= 18
    score -= exclude_penalty
    template_terms = [
        str(term).lower()
        for key in ("required_terms", "preferred_terms", "evidence_terms")
        for term in template.get(key, [])
    ]
    relevant_original = [
        item
        for item in evidence
        if not item.get("is_fork")
        and any(
            term in normalized_text(item.get("title"), item.get("description"))
            for term in template_terms
        )
    ]
    score += min(len(relevant_original) * 5, 20)
    city = profile.get("city") or "待核验"
    if requested_city in ("北京", "重庆"):
        score += 10 if city == requested_city else 0
    elif city in ("北京", "重庆"):
        score += 8
    if profile.get("source_updated_at"):
        score += 2
    return (
        max(0, min(score, 100)),
        requested_role or template.get("name", "自定义岗位"),
        ranked[:6],
    )


def template_match_breakdown(
    profile: Dict[str, Any], evidence: List[Dict[str, Any]], template: Dict[str, Any]
) -> Dict[str, Any]:
    """Return explainable rule hits for the UI and optional AI prompt."""
    text = normalized_text(
        profile.get("bio"),
        profile.get("company"),
        profile.get("display_name"),
        profile.get("username"),
        *["{} {}".format(item.get("title") or "", item.get("description") or "") for item in evidence],
    )
    result: Dict[str, Any] = {}
    for key in ("synonyms", "required_terms", "preferred_terms", "evidence_terms", "exclude_terms"):
        result[key] = [term for term in template.get(key, []) if str(term).lower() in text]
    result["missing_required_terms"] = [
        term for term in template.get("required_terms", []) if str(term).lower() not in text
    ]
    return result


def keyword_for_role(role: str, role_template: Optional[Dict[str, Any]] = None) -> str:
    if role_template:
        keywords = role_template.get("search_keywords") or []
        if keywords:
            return str(keywords[0])
    return {
        "AI Agent 工程师": "agent",
        "AI Coding 工程师": "coding agent",
        "AI 产品经理": "agent builder",
    }.get(role, "agent")
