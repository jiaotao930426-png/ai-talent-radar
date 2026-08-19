"""Role-template definitions and validation for configurable matching."""

import hashlib
import re
from typing import Any, Dict, Iterable, List


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


BUILTIN_ROLE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "slug": "ai-agent-engineer",
        "name": "AI Agent 工程师",
        "description": "负责 Agent、工具调用、记忆、规划或多智能体系统的研发。",
        "synonyms": ["Agent 工程师", "智能体工程师", "AI Agent"],
        "required_terms": ["agent"],
        "preferred_terms": ["langgraph", "multi-agent", "tool calling", "memory", "planning"],
        "evidence_terms": ["mcp", "rag", "workflow", "智能体"],
        "exclude_terms": ["实习生", "招聘顾问"],
        "search_keywords": ["agent", "langgraph", "智能体"],
        "weights": {"required": 14, "preferred": 8, "evidence": 7, "exclude": 18},
    },
    {
        "slug": "ai-coding-engineer",
        "name": "AI Coding 工程师",
        "description": "负责 AI coding、代码智能体、开发者工具或 IDE 集成。",
        "synonyms": ["AI 编程工程师", "Coding Agent 工程师", "开发者工具工程师"],
        "required_terms": ["coding"],
        "preferred_terms": ["coding agent", "agentic coding", "codex", "claude code", "developer tool"],
        "evidence_terms": ["mcp", "ide", "代码智能体", "代码"],
        "exclude_terms": ["实习生", "招聘顾问"],
        "search_keywords": ["coding agent", "agentic coding", "developer tool"],
        "weights": {"required": 14, "preferred": 8, "evidence": 7, "exclude": 18},
    },
    {
        "slug": "ai-product-manager",
        "name": "AI 产品经理",
        "description": "负责 AI 产品、Agent 产品、工作流和用户价值设计。",
        "synonyms": ["AI 产品", "智能产品经理", "Agent 产品经理"],
        "required_terms": ["product"],
        "preferred_terms": ["ai product", "agent builder", "workflow", "dify", "coze"],
        "evidence_terms": ["agent", "用户", "产品"],
        "exclude_terms": ["实习生", "招聘顾问"],
        "search_keywords": ["AI product", "agent builder", "AI 产品"],
        "weights": {"required": 14, "preferred": 8, "evidence": 7, "exclude": 18},
    },
]


LIST_FIELDS = (
    "synonyms",
    "required_terms",
    "preferred_terms",
    "evidence_terms",
    "exclude_terms",
    "search_keywords",
)


def _clean_terms(value: Any, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("{} 必须是字符串数组".format(field))
    cleaned: List[str] = []
    for item in value:
        term = str(item or "").strip()
        if not term:
            continue
        if len(term) > 100:
            raise ValueError("{} 中的单项不能超过100个字符".format(field))
        if term.lower() not in {existing.lower() for existing in cleaned}:
            cleaned.append(term)
    return cleaned[:100]


def normalize_template(payload: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("岗位模板格式无效")
    result: Dict[str, Any] = {}
    if not partial or "slug" in payload:
        slug = str(payload.get("slug") or "").strip().lower()
        if not slug and not partial:
            name_seed = str(payload.get("name") or "岗位").strip().encode("utf-8")
            slug = "role-" + hashlib.sha1(name_seed).hexdigest()[:12]
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValueError("岗位模板 slug 只能包含小写字母、数字和连字符")
        result["slug"] = slug
    if not partial or "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValueError("岗位模板名称不能为空且不超过80个字符")
        result["name"] = name
    if not partial or "description" in payload:
        description = str(payload.get("description") or "").strip()
        if len(description) > 500:
            raise ValueError("岗位模板描述不能超过500个字符")
        result["description"] = description
    for field in LIST_FIELDS:
        if not partial or field in payload:
            result[field] = _clean_terms(payload.get(field), field)
    if not partial or "weights" in payload:
        raw_weights = payload.get("weights") or {}
        if not isinstance(raw_weights, dict):
            raise ValueError("weights 必须是对象")
        weights: Dict[str, int] = {}
        for key in ("required", "preferred", "evidence", "exclude"):
            value = raw_weights.get(key, 10 if key != "exclude" else 15)
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("weights 必须是整数") from exc
            if not 0 <= number <= 50:
                raise ValueError("weights 必须在0到50之间")
            weights[key] = number
        result["weights"] = weights
    if not partial:
        result.setdefault("synonyms", [])
        result.setdefault("required_terms", [])
        result.setdefault("preferred_terms", [])
        result.setdefault("evidence_terms", [])
        result.setdefault("exclude_terms", [])
        result.setdefault("search_keywords", [])
        result.setdefault("weights", {"required": 10, "preferred": 10, "evidence": 10, "exclude": 15})
    return result


def builtin_templates() -> Iterable[Dict[str, Any]]:
    for template in BUILTIN_ROLE_TEMPLATES:
        # Return a copy so callers cannot mutate the seed definitions.
        yield normalize_template(dict(template))
