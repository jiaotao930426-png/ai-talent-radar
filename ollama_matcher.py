"""Optional local Ollama matcher.

The module deliberately uses urllib from the standard library. It sends only
public profile/project evidence and never contact details or hidden attributes.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT_SECONDS = 240
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
PUBLIC_EMAIL_PATTERN = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
# Redact common mainland mobile numbers and phone-like digit groups embedded
# in otherwise public profile text.  URLs are kept unchanged because the
# matcher needs exact project links for evidence validation.
PUBLIC_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)"
)


def _redact_public_text(value: Any) -> str:
    """Remove contact-like strings before text enters or leaves local AI."""
    text = str(value or "")
    text = PUBLIC_EMAIL_PATTERN.sub("[公开邮箱已隐藏]", text)
    return PUBLIC_PHONE_PATTERN.sub("[公开电话已隐藏]", text)

# A native install should be usable after a reboot without requiring the user
# to remember to start Ollama first.  Docker is deliberately excluded: a
# container cannot safely start a process on the host and Compose points at
# ``host.docker.internal`` instead.
OLLAMA_HEALTH_TIMEOUT_SECONDS = 3.0
OLLAMA_START_WAIT_SECONDS = 30.0
OLLAMA_HEALTH_CACHE_SECONDS = 5.0
OLLAMA_START_RETRY_SECONDS = 5.0
OLLAMA_MODEL_DISCOVERY_GRACE_SECONDS = 3.0
_AUTOSTART_LOCK = threading.Lock()
_HEALTHY_CACHE: Dict[str, float] = {}
_START_ATTEMPTS: Dict[str, float] = {}
_START_ERRORS: Dict[str, str] = {}


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


def _is_loopback_url(base_url: str) -> bool:
    hostname = urllib.parse.urlparse(base_url).hostname
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _autostart_enabled(base_url: str) -> bool:
    if not _is_loopback_url(base_url):
        return False
    raw = str(os.environ.get("OLLAMA_AUTOSTART", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _ollama_binary() -> Optional[str]:
    configured = str(os.environ.get("OLLAMA_BIN") or "").strip()
    candidates = [configured] if configured else []
    candidates.extend(
        [
            shutil.which("ollama") or "",
            os.path.expanduser("~/.npm-global/bin/ollama"),
            os.path.expanduser("~/.local/bin/ollama"),
            "/opt/homebrew/bin/ollama",
            "/usr/local/bin/ollama",
            "/Applications/Ollama.app/Contents/Resources/ollama",
        ]
    )
    seen = set()
    for candidate in candidates:
        path = os.path.expanduser(str(candidate or "")).strip()
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _tags(base_url: str, timeout: float = OLLAMA_HEALTH_TIMEOUT_SECONDS) -> List[str]:
    request = urllib.request.Request(
        base_url + "/api/tags",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_response = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OllamaUnavailable("本地 Ollama 服务未运行") from exc
    try:
        payload = json.loads(raw_response)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OllamaUnavailable("本地 Ollama 健康检查返回格式无效") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise OllamaUnavailable("本地 Ollama 健康检查返回格式无效")
    names = []
    for item in payload["models"]:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            names.append(str(item["name"]).strip())
    return names


def _cache_key(base_url: str, model: str) -> str:
    return "{}|{}".format(base_url, model)


def _invalidate_health_cache(base_url: str, model: str) -> None:
    key = _cache_key(base_url, model)
    _HEALTHY_CACHE.pop(key, None)


def _start_ollama(binary: str) -> None:
    try:
        subprocess.Popen(
            [binary, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise OllamaUnavailable("无法自动启动本地 Ollama 服务") from exc


def ensure_ollama_available(
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = OLLAMA_START_WAIT_SECONDS,
) -> None:
    """Ensure the configured local Ollama endpoint and model are available.

    The check is intentionally performed once per short cache window rather
    than once per candidate.  It may start ``ollama serve`` only for a native
    loopback endpoint; it never downloads a model and never starts a host
    process from Docker.
    """
    base_url = base_url or _base_url()
    model = model or configured_model()
    key = _cache_key(base_url, model)
    now = time.monotonic()
    if key in _HEALTHY_CACHE and now - _HEALTHY_CACHE[key] < OLLAMA_HEALTH_CACHE_SECONDS:
        return

    with _AUTOSTART_LOCK:
        now = time.monotonic()
        if key in _HEALTHY_CACHE and now - _HEALTHY_CACHE[key] < OLLAMA_HEALTH_CACHE_SECONDS:
            return
        try:
            names = _tags(base_url)
        except OllamaUnavailable as initial_error:
            if not _autostart_enabled(base_url):
                raise OllamaUnavailable(
                    "本地 Ollama 不可用，请先启动 Ollama 服务"
                ) from initial_error
            last_attempt = _START_ATTEMPTS.get(key, 0.0)
            if key in _START_ATTEMPTS and now - last_attempt < OLLAMA_START_RETRY_SECONDS:
                raise OllamaUnavailable(
                    _START_ERRORS.get(key, "本地 Ollama 服务未运行，请稍后重试")
                ) from initial_error
            binary = _ollama_binary()
            if not binary:
                message = (
                    "本地 Ollama 未运行，且未找到 ollama 可执行文件；"
                    "请安装 Ollama 或手动启动服务"
                )
                _START_ATTEMPTS[key] = now
                _START_ERRORS[key] = message
                raise OllamaUnavailable(message) from initial_error
            _START_ATTEMPTS[key] = now
            try:
                _start_ollama(binary)
            except OllamaUnavailable as exc:
                _START_ERRORS[key] = str(exc)
                raise
            deadline = time.monotonic() + max(1.0, float(timeout))
            names = []
            connected = False
            connected_at = 0.0
            while time.monotonic() < deadline:
                try:
                    names = _tags(base_url)
                    if not connected:
                        connected = True
                        connected_at = time.monotonic()
                    if model in names:
                        break
                    if names and time.monotonic() - connected_at >= OLLAMA_MODEL_DISCOVERY_GRACE_SECONDS:
                        break
                except OllamaUnavailable:
                    time.sleep(0.5)
            if not connected:
                message = "已尝试自动启动 Ollama，但服务仍未就绪"
                _START_ERRORS[key] = message
                raise OllamaUnavailable(message) from initial_error

        if model not in names:
            message = (
                "Ollama 服务已连接，但未找到模型 {}；"
                "请先在本机执行 ollama pull {}，系统不会自动下载模型"
            ).format(model, model)
            _START_ERRORS[key] = message
            raise OllamaUnavailable(message)
        _HEALTHY_CACHE[key] = time.monotonic()
        _START_ERRORS.pop(key, None)


def _public_payload(candidate: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role_template": {
            "name": _redact_public_text(template.get("name", "")),
            "description": _redact_public_text(template.get("description", "")),
            "required_terms": template.get("required_terms", []),
            "preferred_terms": template.get("preferred_terms", []),
            "evidence_terms": template.get("evidence_terms", []),
            "exclude_terms": template.get("exclude_terms", []),
        },
        "candidate": {
            "bio": _redact_public_text(candidate.get("bio", "")),
            "projects": [
                {
                    "title": _redact_public_text(item.get("title", "")),
                    "description": _redact_public_text(item.get("description", "")),
                    "url": item.get("url", ""),
                    "language": _redact_public_text(item.get("language", "")),
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
        "DATA\n" + payload + "\nEND_DATA\n"
        + OUTPUT_CONTRACT
        + "\nOutput only the six-field classification object. Never return the input wrapper."
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
    if "candidate" in payload or "role_template" in payload:
        raise ValueError("Ollama 回显了输入数据")
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
    summary = _redact_public_text(
        payload.get("summary") or payload.get("reason") or ""
    )[:500]
    if score == 0 and confidence == 0 and not matched and not gaps and not evidence and not summary:
        raise ValueError("Ollama 返回了空的匹配结果")
    clean_evidence: List[Dict[str, str]] = []
    for item in evidence[:8]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        clean_evidence.append({
            "title": _redact_public_text(item.get("title") or "公开项目"),
            "url": url,
            "reason": _redact_public_text(item.get("reason") or "")[:400],
        })
    return {
        "match_score": score,
        "confidence": confidence,
        "matched_skills": [_redact_public_text(item)[:100] for item in matched[:30]],
        "evidence": clean_evidence,
        "gaps": [_redact_public_text(item)[:200] for item in gaps[:30]],
        "summary": summary,
    }


def match_candidate(
    candidate: Dict[str, Any], template: Dict[str, Any], timeout: Optional[float] = None
) -> Dict[str, Any]:
    """Ask the local model for a structured match result."""
    base_url = _base_url()
    model = configured_model()
    ensure_ollama_available(base_url=base_url, model=model)
    body = json.dumps(
        {
            "model": model,
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
        with urllib.request.urlopen(
            request,
            timeout=timeout
            or float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        ) as response:
            raw_response = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        _invalidate_health_cache(base_url, model)
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
    result["model"] = model
    result["matched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return result


def combine_scores(rule_score: int, ai_score: int, ai_confidence: float) -> int:
    """Blend rules and AI while discounting low-confidence model output."""
    confidence = max(0.0, min(1.0, float(ai_confidence)))
    ai_weight = 0.4 * confidence
    blended = float(rule_score) * (1.0 - ai_weight) + float(ai_score) * ai_weight
    return max(0, min(100, int(round(blended))))
