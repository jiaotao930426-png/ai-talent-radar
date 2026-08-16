"""Optional local Ollama matcher.

The module deliberately uses urllib from the standard library. It sends only
public profile/project evidence and never contact details or hidden attributes.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"
JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
# Qwen3 reliably follows an explicit JSON contract with ``format=json``. Its
# constrained JSON-Schema mode can otherwise emit a valid object containing
# only schema defaults, which is indistinguishable from a real score of zero.
OUTPUT_CONTRACT = (
    "Return exactly one JSON object with these keys and types: "
    '{"match_score": integer 0-100, "confidence": number 0-1, '
    '"matched_skills": [string], "evidence": '
    '[{"title": string, "url": string, "reason": string}], '
    '"gaps": [string], "summary": string}. '
    "Fill every field from the supplied data. If the match is weak, use a "
    "low score and explain the missing evidence in gaps; do not return an "
    "empty default object."
)
class OllamaUnavailable(RuntimeError):
    pass


def _base_url() -> str:
    value = str(os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
    if (
        parsed.scheme != "http"
        or parsed.hostname not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OLLAMA_BASE_URL 只能指向本机或 Docker 宿主机 HTTP 服务")
    return value


def configured_model() -> str:
    return str(os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL).strip()[:120]


def _public_payload(candidate: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role_template": {
            "name": template.get("name", ""),
            "description": template.get("description", ""),
            "required_terms": template.get("required_terms", []),
            "preferred_terms": template.get("preferred_terms", []),
            "evidence_terms": template.get("evidence_terms", []),
            "exclude_terms": template.get("exclude_terms", []),
        },
        "candidate": {
            "display_name": candidate.get("display_name", ""),
            "username": candidate.get("username", ""),
            "bio": candidate.get("bio", ""),
            "company": candidate.get("company", ""),
            "city": candidate.get("city", "待核验"),
            "projects": [
                {
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "url": item.get("url", ""),
                    "language": item.get("language", ""),
                }
                for item in (candidate.get("evidence") or [])[:8]
            ],
        },
    }


def _prompt(candidate: Dict[str, Any], template: Dict[str, Any]) -> str:
    payload = json.dumps(_public_payload(candidate, template), ensure_ascii=False)
    return (
        "You are a strict technical matching classifier. Analyze the candidate against the role "
        "using only the supplied public data. Do not copy the input object. Return a new object "
        "that follows the provided JSON schema. Use only exact project URLs from the data in "
        "evidence. Do not infer age, education, phone, email, gender, family, work willingness, "
        "or any undisclosed attribute. Do not invent projects, experience, or links. "
        "If evidence is weak, lower the score and confidence and list gaps.\n"
        "ROLE_AND_CANDIDATE_DATA\n" + payload + "\nEND_ROLE_AND_CANDIDATE_DATA\n"
        + OUTPUT_CONTRACT
        + "\nNow output only the classification object."
    )


def _parse_json(text: str) -> Dict[str, Any]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    match = JSON_OBJECT_PATTERN.search(cleaned)
    if not match:
        raise ValueError("Ollama 未返回 JSON")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Ollama 返回的 JSON 格式无效")
    try:
        # ``score`` is accepted as a compatibility alias used by some Qwen
        # responses even when the prompt asks for ``match_score``.
        raw_score = float(payload.get("match_score", payload.get("score", 0)))
        # Some Qwen responses express a 0-1 score despite the integer schema.
        if 0 <= raw_score <= 1:
            raw_score *= 100
        score = max(0, min(100, int(round(raw_score))))
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0))))
    except (TypeError, ValueError) as exc:
        raise ValueError("Ollama 返回的分数格式无效") from exc
    matched = payload.get("matched_skills") if isinstance(payload.get("matched_skills"), list) else []
    gaps = payload.get("gaps") if isinstance(payload.get("gaps"), list) else []
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    summary = str(payload.get("summary") or payload.get("reason") or "")[:500]
    if score == 0 and confidence == 0 and not matched and not gaps and not evidence and not summary:
        raise ValueError("Ollama 返回了空的匹配结果")
    clean_evidence: List[Dict[str, str]] = []
    for item in evidence[:8]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        clean_evidence.append({
            "title": str(item.get("title") or "公开项目"),
            "url": url,
            "reason": str(item.get("reason") or "")[:400],
        })
    return {
        "match_score": score,
        "confidence": confidence,
        "matched_skills": [str(item)[:100] for item in matched[:30]],
        "evidence": clean_evidence,
        "gaps": [str(item)[:200] for item in gaps[:30]],
        "summary": summary,
    }


def match_candidate(
    candidate: Dict[str, Any], template: Dict[str, Any], timeout: Optional[float] = None
) -> Dict[str, Any]:
    """Ask the local model for a structured match result."""
    base_url = _base_url()
    body = json.dumps(
        {
            "model": configured_model(),
            "prompt": _prompt(candidate, template),
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.1},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "45"))) as response:
            raw_response = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OllamaUnavailable("本地 Ollama 不可用") from exc
    try:
        response_payload = json.loads(raw_response)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OllamaUnavailable("本地 Ollama 返回格式无效") from exc
    if not isinstance(response_payload, dict):
        raise OllamaUnavailable("本地 Ollama 返回格式无效")
    # Qwen3 may place the structured result in `thinking` while leaving
    # `response` empty. Parse only the JSON object and never persist the
    # surrounding reasoning text.
    raw_result = response_payload.get("response") or response_payload.get("thinking") or ""
    result = _parse_json(raw_result)
    allowed_urls = {
        str(item.get("url") or "").strip()
        for item in (candidate.get("evidence") or [])
        if str(item.get("url") or "").strip()
    }
    # Keep only citations that already exist in the public evidence supplied to
    # the model. This prevents a malformed or hallucinated link entering the DB.
    result["evidence"] = [
        item for item in result.get("evidence", []) if item.get("url") in allowed_urls
    ]
    result["model"] = configured_model()
    result["matched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return result


def combine_scores(rule_score: int, ai_score: int, ai_confidence: float) -> int:
    """Blend rules and AI while discounting low-confidence model output."""
    confidence = max(0.0, min(1.0, float(ai_confidence)))
    ai_weight = 0.4 * confidence
    blended = float(rule_score) * (1.0 - ai_weight) + float(ai_score) * ai_weight
    return max(0, min(100, int(round(blended))))
