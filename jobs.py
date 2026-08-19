import math
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import db
from contactability import candidate_priority_key, derive_contact_level, has_direct_contact
from collectors import (
    CollectorError,
    NetworkUnavailable,
    RateLimited,
    analyze_public_url,
    search_gitee,
    search_github,
    search_gitlab,
    search_huggingface,
    search_stackoverflow,
    suppress_shared_public_emails,
)
from scoring import keyword_for_role
from scoring import score_candidate, template_match_breakdown
from ollama_matcher import OllamaUnavailable, combine_scores, match_candidate


ALLOWED_ROLES = ("AI Agent 工程师", "AI Coding 工程师", "AI 产品经理")
ALLOWED_CITIES = ("北京", "重庆")
ALLOWED_SOURCES = ("github", "gitee", "gitlab", "huggingface", "stackoverflow")
SOURCE_LABELS = {
    "github": "GitHub",
    "gitee": "Gitee",
    "gitlab": "GitLab",
    "huggingface": "Hugging Face",
    "stackoverflow": "Stack Overflow",
    "public_url": "公开链接",
}
AI_REANALYSIS_KIND = "AI 批量重分析"
AI_REANALYSIS_MAX_CANDIDATES = 300
SEARCH_COLLECTORS = {
    "github": search_github,
    "gitee": search_gitee,
    "gitlab": search_gitlab,
    "huggingface": search_huggingface,
    "stackoverflow": search_stackoverflow,
}


class AIReanalysisInProgress(ValueError):
    """Raised when a second local AI retry is requested while one is active."""


def next_weekly_run(
    weekday: int,
    hour: int,
    minute: int,
    from_dt: Optional[datetime] = None,
) -> datetime:
    current = from_dt or datetime.now().astimezone()
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (weekday - current.weekday()) % 7
    candidate += timedelta(days=delta)
    if candidate <= current:
        candidate += timedelta(days=7)
    return candidate


def format_retry_time(value: str) -> str:
    """Format a retry timestamp without passing non-ASCII text to strftime."""
    retry_at = datetime.fromisoformat(value)
    return "{:02d}月{:02d}日 {:02d}:{:02d}".format(
        retry_at.month,
        retry_at.day,
        retry_at.hour,
        retry_at.minute,
    )


def normalize_config(
    config: Dict[str, Any],
    strict_roles: bool = False,
    allow_role_snapshots: bool = False,
) -> Dict[str, Any]:
    return _normalize_config(
        config,
        strict_roles=strict_roles,
        allow_role_snapshots=allow_role_snapshots,
    )


def _snapshots_by_role(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    snapshots: Dict[str, Dict[str, Any]] = {}
    raw_snapshots = config.get("role_template_snapshots") or []
    if not isinstance(raw_snapshots, list):
        return snapshots
    for item in raw_snapshots:
        if not isinstance(item, dict) or not isinstance(item.get("snapshot"), dict):
            continue
        snapshot = dict(item["snapshot"])
        name = str(item.get("name") or snapshot.get("name") or "").strip()
        if name:
            snapshots[name] = snapshot
    return snapshots


def _normalize_config(
    config: Dict[str, Any],
    strict_roles: bool = False,
    allow_role_snapshots: bool = False,
) -> Dict[str, Any]:
    mode = config.get("mode") or "search"
    if mode not in {"search", "url"}:
        raise ValueError("无效的采集方式")
    target = max(1, min(int(config.get("target") or 10), 50))
    roles = config.get("roles") or [config.get("role") or "AI Agent 工程师"]
    cities = config.get("cities") or [config.get("city") or "北京"]
    sources = config.get("sources") or ["github"]
    preserved_snapshots = _snapshots_by_role(config) if allow_role_snapshots else {}
    try:
        allowed_roles = list(db.active_role_names())
    except Exception:
        allowed_roles = list(ALLOWED_ROLES)
    if allow_role_snapshots:
        for role in preserved_snapshots:
            if role not in allowed_roles:
                allowed_roles.append(role)
    if not allowed_roles:
        raise ValueError("当前没有启用的岗位模板，请先启用或创建岗位模板")
    unknown_roles = [role for role in roles if role not in allowed_roles]
    if strict_roles and unknown_roles:
        raise ValueError("岗位模板不存在或已停用：{}".format("、".join(map(str, unknown_roles))))
    roles = [role for role in roles if role in allowed_roles] or [allowed_roles[0]]
    role_template_snapshots: List[Dict[str, Any]] = []
    for role in roles:
        template = preserved_snapshots.get(role)
        if template is None:
            try:
                template = db.get_role_template(role, require_active=True)
            except Exception:
                template = None
        if template:
            role_template_snapshots.append(
                {
                    "id": template["id"],
                    "name": template["name"],
                    "slug": template["slug"],
                    "version": template["version"],
                    "snapshot": template,
                }
            )
    cities = [city for city in cities if city in ALLOWED_CITIES] or ["北京"]
    sources = [source for source in sources if source in ALLOWED_SOURCES] or ["github"]
    keywords = str(config.get("keywords") or "").strip()
    url = str(config.get("url") or "").strip()
    prefer_contactable = config.get("prefer_contactable", True)
    if not isinstance(prefer_contactable, bool):
        raise ValueError("联系方式优先设置必须为布尔值")
    raw_ai = config.get("use_local_ai", config.get("enable_ai", config.get("ai_enabled", False)))
    if not isinstance(raw_ai, bool):
        raise ValueError("本地 AI 设置必须为布尔值")
    return {
        "mode": mode,
        "target": target,
        "roles": roles,
        "cities": cities,
        "sources": sources,
        "keywords": keywords,
        "url": url,
        "prefer_contactable": prefer_contactable,
        "use_local_ai": raw_ai,
        "enable_ai": raw_ai,
        "role_template_snapshots": role_template_snapshots,
    }


def rank_candidates(
    candidates: List[Dict[str, Any]], prefer_contactable: bool
) -> List[Dict[str, Any]]:
    for candidate in candidates:
        candidate["contact_level"] = derive_contact_level(candidate)
    return sorted(
        candidates,
        key=lambda candidate: candidate_priority_key(candidate, prefer_contactable),
    )


def _template_for_role(
    role: str, config: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    if config:
        snapshot = _snapshots_by_role(config).get(role)
        if snapshot:
            return snapshot
    try:
        return db.get_role_template(role, require_active=True)
    except Exception:
        return None


def _prepare_candidate(
    candidate: Dict[str, Any],
    role: str,
    city: str,
    use_local_ai: bool,
    role_template: Optional[Dict[str, Any]] = None,
    ai_state: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Apply the selected template, then optionally enrich the match locally."""
    template = role_template or _template_for_role(role)
    evidence = candidate.get("evidence") or []
    can_reuse_existing_rule = bool(
        template
        and template.get("is_builtin")
        and int(template.get("version") or 1) == 1
        and candidate.get("rule_match_score") is not None
    )
    if can_reuse_existing_rule:
        rule_score = int(candidate.get("rule_match_score") or 0)
        suggested_role = role
        ranked = (candidate.get("_legacy_ranked_evidence") or evidence)[:6]
    elif (
        not candidate.get("bio")
        and not candidate.get("company")
        and not evidence
        and candidate.get("match_score") is not None
    ):
        # Preserve collector/test supplied scores for minimal records that have
        # no evidence to re-score. Real profile collectors provide rule score.
        rule_score = int(candidate.get("match_score") or 0)
        suggested_role = role
        ranked = evidence[:6]
    else:
        rule_score, suggested_role, ranked = score_candidate(
            candidate, evidence, role, city, role_template=template
        )
    candidate["rule_match_score"] = rule_score
    candidate["match_score"] = rule_score
    candidate["suggested_role"] = suggested_role
    candidate["evidence"] = ranked
    if template:
        candidate["role_template_id"] = template["id"]
        candidate["role_template_version"] = template["version"]
        candidate["role_template_snapshot"] = template
        candidate["match_breakdown"] = template_match_breakdown(candidate, evidence, template)
    else:
        candidate["match_breakdown"] = {}
    candidate["ai_match_status"] = "未启用"
    candidate["ai_match_reason"] = ""
    candidate["ai_match_evidence"] = []
    candidate["ai_match_model"] = ""
    candidate["ai_match_at"] = None
    candidate["ai_match_score"] = None
    candidate["ai_match_confidence"] = None
    if use_local_ai and template:
        if ai_state and ai_state.get("failure_status"):
            candidate["ai_match_status"] = ai_state["failure_status"]
            candidate["ai_match_reason"] = ai_state.get("failure_reason", "")
            return candidate
        try:
            result = match_candidate(candidate, template)
            candidate["ai_match_score"] = result["match_score"]
            candidate["ai_match_confidence"] = result["confidence"]
            candidate["ai_match_status"] = "已完成"
            candidate["ai_match_reason"] = result.get("summary", "")
            candidate["ai_match_evidence"] = result.get("evidence", [])
            candidate["ai_match_model"] = result.get("model", "")
            candidate["ai_match_at"] = result.get("matched_at")
            candidate["match_score"] = combine_scores(
                rule_score, result["match_score"], result["confidence"]
            )
        except OllamaUnavailable as exc:
            candidate["ai_match_status"] = "不可用，已回退规则"
            candidate["ai_match_reason"] = str(exc)
            if ai_state is not None:
                ai_state["failure_status"] = candidate["ai_match_status"]
                ai_state["failure_reason"] = candidate["ai_match_reason"]
        except (ValueError, TypeError) as exc:
            candidate["ai_match_status"] = "返回无效，已回退规则"
            candidate["ai_match_reason"] = str(exc)
            if ai_state is not None:
                ai_state["failure_status"] = candidate["ai_match_status"]
                ai_state["failure_reason"] = candidate["ai_match_reason"]
    elif use_local_ai:
        candidate["ai_match_status"] = "无可用模板，已回退规则"
    return candidate


def _source_stat(source: str) -> Dict[str, Any]:
    return {
        "source": source,
        "label": SOURCE_LABELS.get(source, source),
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "discovered": 0,
        "errors": [],
    }


def _url_source_key(url: str) -> str:
    """Map a public URL host to the source key without fetching the URL."""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    for source, domains in {
        "github": ("github.com",),
        "gitee": ("gitee.com",),
        "gitlab": ("gitlab.com",),
        "huggingface": ("huggingface.co", "hf.co"),
        "stackoverflow": ("stackoverflow.com",),
    }.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return source
    return "public_url"


def _source_counts(source_stats: List[Dict[str, Any]]) -> Tuple[int, int]:
    return (
        sum(int(item.get("successes") or 0) for item in source_stats),
        sum(int(item.get("failures") or 0) for item in source_stats),
    )


def _ai_counts(candidates: List[Dict[str, Any]], requested: bool) -> Dict[str, int]:
    counts = {
        "ai_requested": 1 if requested else 0,
        "ai_completed_count": 0,
        "ai_fallback_count": 0,
        "ai_disabled_count": 0,
    }
    for candidate in candidates:
        status = str(candidate.get("ai_match_status") or "未启用")
        if status == "已完成":
            counts["ai_completed_count"] += 1
        elif "回退" in status:
            counts["ai_fallback_count"] += 1
        else:
            counts["ai_disabled_count"] += 1
    return counts


def _job_summary(metrics: Dict[str, Any], target: int, *, prefix: str = "") -> str:
    actual = int(metrics.get("result_count") or 0)
    parts = [
        "目标 {}".format(int(target or 0)),
        "实际 {}".format(actual),
        "新增 {}".format(int(metrics.get("inserted_count") or 0)),
        "已有 {}".format(int(metrics.get("existing_count") or 0)),
        "原始命中 {}".format(int(metrics.get("discovered_count") or 0)),
        "任务内去重 {}".format(int(metrics.get("duplicate_count") or 0)),
        "未入选 {}".format(int(metrics.get("filtered_count") or 0)),
        "公开联系方式 {}".format(int(metrics.get("direct_contact_count") or 0)),
        "来源成功 {}".format(int(metrics.get("source_success_count") or 0)),
        "来源失败 {}".format(int(metrics.get("source_failure_count") or 0)),
    ]
    if int(metrics.get("ai_requested") or 0):
        parts.extend(
            [
                "AI 完成 {}".format(int(metrics.get("ai_completed_count") or 0)),
                "AI 回退 {}".format(int(metrics.get("ai_fallback_count") or 0)),
            ]
        )
        if metrics.get("ai_failure_reason"):
            parts.append("AI 原因 {}".format(str(metrics["ai_failure_reason"])[:180]))
    else:
        parts.append("AI 未启用")
    if actual < int(target or 0):
        parts.append("缺口 {}".format(int(target or 0) - actual))
    if int(metrics.get("cancelled_count") or 0):
        parts.append("取消未保存 {}".format(int(metrics["cancelled_count"])))
    return (prefix + " · ".join(parts)).strip()


def _persist_job_metrics(job_id: int, metrics: Dict[str, Any]) -> None:
    fields = {
        key: metrics.get(key, 0)
        for key in (
            "target_count",
            "discovered_count",
            "unique_count",
            "duplicate_count",
            "filtered_count",
            "inserted_count",
            "existing_count",
            "source_success_count",
            "source_failure_count",
            "ai_requested",
            "ai_completed_count",
            "ai_fallback_count",
            "ai_disabled_count",
        )
    }
    if "source_stats" in metrics:
        fields["source_stats"] = metrics.get("source_stats") or []
    db.update_job(job_id, **fields)


def _failure_message(job_id: int, fallback: str) -> str:
    """Keep an exact collection summary when a job ends in a source error."""
    try:
        current = db.get_job(job_id) or {}
        summary = str(current.get("message") or "")
    except Exception:
        summary = ""
    if summary.startswith("目标 "):
        return summary + "；" + fallback
    return fallback


class JobManager:
    def __init__(self) -> None:
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._reanalysis_lock = threading.Lock()
        self._reanalysis_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        schedule = db.get_schedule()
        if schedule["enabled"] and not schedule.get("next_run_at"):
            next_run = next_weekly_run(
                schedule["weekday"], schedule["hour"], schedule["minute"]
            ).isoformat(timespec="seconds")
            db.save_schedule(schedule, next_run)
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="talent-radar-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def stop(self) -> None:
        self._scheduler_stop.set()

    def submit(
        self,
        kind: str,
        config: Dict[str, Any],
        *,
        allow_role_snapshots: bool = False,
    ) -> int:
        normalized = normalize_config(
            config,
            strict_roles=True,
            allow_role_snapshots=allow_role_snapshots,
        )
        if normalized["mode"] == "url" and not normalized["url"]:
            raise ValueError("请输入公开主页或项目链接")
        job_id = db.create_job(kind, normalized)
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, kind, normalized),
            name="talent-radar-job-{}".format(job_id),
            daemon=True,
        )
        thread.start()
        return job_id

    def submit_ai_reanalysis(
        self,
        limit: int = AI_REANALYSIS_MAX_CANDIDATES,
        *,
        include_archived: bool = False,
    ) -> Tuple[int, int]:
        """Start a background retry for candidates without completed AI.

        Candidate IDs are snapshotted before the worker starts.  This keeps a
        long-running local model call deterministic while allowing newly
        collected candidates to wait for the next retry.  The worker writes
        only the AI columns, so contact and human-review fields remain intact.
        """
        if not isinstance(include_archived, bool):
            raise ValueError("是否包含已归档候选人必须为布尔值")
        with self._reanalysis_lock:
            active = db.active_job_id(AI_REANALYSIS_KIND)
            if active:
                raise AIReanalysisInProgress(
                    "已有 AI 重分析任务正在进行（任务 {}）".format(active)
                )
            candidates = db.list_candidates_needing_ai(
                limit,
                include_archived=include_archived,
            )
            candidate_ids = [int(candidate["id"]) for candidate in candidates]
            config = {
                "mode": "ai_reanalysis",
                "target": len(candidate_ids),
                "candidate_ids": candidate_ids,
                "include_archived": include_archived,
                "use_local_ai": True,
                "enable_ai": True,
            }
            job_id = db.create_job(AI_REANALYSIS_KIND, config)
            for candidate_id in candidate_ids:
                db.link_job_candidate(job_id, candidate_id)
            if not candidate_ids:
                db.update_job(
                    job_id,
                    status="已完成",
                    progress=100,
                    message="人才池中没有需要重新分析的候选人",
                    completed_at=db.now_iso(),
                )
                return job_id, 0
            thread = threading.Thread(
                target=self._run_ai_reanalysis,
                args=(job_id, candidate_ids),
                name="talent-radar-ai-reanalysis-{}".format(job_id),
                daemon=True,
            )
            self._reanalysis_thread = thread
            thread.start()
            return job_id, len(candidate_ids)

    @staticmethod
    def _reanalysis_template(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        snapshot = candidate.get("role_template_snapshot")
        if isinstance(snapshot, dict) and str(snapshot.get("name") or "").strip():
            return snapshot
        role = str(candidate.get("suggested_role") or "").strip()
        if role:
            template = _template_for_role(role)
            if template:
                return template
        try:
            active_templates = db.list_role_templates(active_only=True)
        except Exception:
            active_templates = []
        return active_templates[0] if active_templates else None

    @staticmethod
    def _public_reanalysis_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Build the only candidate shape allowed into the local model call."""
        return {
            "bio": str(candidate.get("bio") or ""),
            "evidence": [
                {
                    "title": str(item.get("title") or "公开项目"),
                    "description": str(item.get("description") or ""),
                    "url": str(item.get("url") or ""),
                    "language": str(item.get("language") or ""),
                }
                for item in (candidate.get("evidence") or [])[:8]
                if isinstance(item, dict)
            ],
        }

    def _run_ai_reanalysis(
        self,
        job_id: int,
        candidate_ids: List[int],
    ) -> None:
        """Run the batch worker and never leave a job stuck in ``正在分析``."""
        try:
            self._run_ai_reanalysis_impl(job_id, candidate_ids)
        except Exception as exc:
            # Per-candidate failures are handled inside the implementation.
            # This final guard covers infrastructure errors (for example a
            # transient SQLite failure) so the UI still receives a terminal
            # task state instead of polling forever.
            try:
                db.update_job(
                    job_id,
                    status="执行失败",
                    progress=100,
                    error="AI 重分析任务异常：{}".format(str(exc)[:1800]),
                    message="AI 重分析任务未完成，请查看本地服务日志",
                    completed_at=db.now_iso(),
                )
            except Exception:
                # If the database itself is unavailable there is no safe
                # fallback write; the original exception remains visible in
                # the local process log when the worker is run by the server.
                pass
            traceback.print_exc()

    def _run_ai_reanalysis_impl(
        self,
        job_id: int,
        candidate_ids: List[int],
    ) -> None:
        total = len(candidate_ids)
        metrics: Dict[str, Any] = {
            "target_count": total,
            "result_count": 0,
            "discovered_count": total,
            "unique_count": total,
            "duplicate_count": 0,
            "filtered_count": 0,
            "inserted_count": 0,
            "existing_count": 0,
            "source_success_count": 0,
            "source_failure_count": 0,
            "ai_requested": 1,
            "ai_completed_count": 0,
            "ai_fallback_count": 0,
            "ai_disabled_count": 0,
        }
        _persist_job_metrics(job_id, metrics)
        db.update_job(
            job_id,
            status="正在分析",
            progress=1,
            started_at=db.now_iso(),
            message="正在使用本机 AI 重新分析人才池（0/{}）".format(total),
        )
        errors: List[str] = []

        for index, candidate_id in enumerate(candidate_ids, start=1):
            if db.job_cancel_requested(job_id):
                metrics["result_count"] = index - 1
                self._finish_cancelled(job_id, metrics)
                return

            candidate = db.get_candidate(candidate_id)
            status = "已完成"
            ai_score: Optional[int] = None
            confidence: Optional[float] = None
            reason = ""
            evidence: List[Dict[str, Any]] = []
            model = ""
            matched_at: Optional[str] = None
            if candidate:
                raw_rule_score = candidate.get("rule_match_score")
                if raw_rule_score is None:
                    raw_rule_score = candidate.get("match_score")
                try:
                    combined_score = max(0, min(100, int(raw_rule_score or 0)))
                except (TypeError, ValueError):
                    combined_score = 0
            else:
                combined_score = 0

            if not candidate:
                status = "不可用，已回退规则"
                reason = "候选人记录已不存在"
            else:
                template = self._reanalysis_template(candidate)
                if not template:
                    status = "无可用模板，已回退规则"
                    reason = "当前没有可用的岗位模板"
                else:
                    try:
                        # ``match_candidate`` further constrains its prompt,
                        # but passing this reduced shape makes the privacy
                        # boundary explicit even if the matcher changes.
                        result = match_candidate(
                            self._public_reanalysis_candidate(candidate), template
                        )
                        ai_score = int(result["match_score"])
                        confidence = float(result["confidence"])
                        reason = str(result.get("summary") or "")[:500]
                        evidence = result.get("evidence") or []
                        model = str(result.get("model") or "")
                        matched_at = result.get("matched_at")
                        combined_score = combine_scores(
                            combined_score, ai_score, confidence
                        )
                    except OllamaUnavailable as exc:
                        status = "不可用，已回退规则"
                        reason = str(exc)[:500]
                    except (ValueError, TypeError) as exc:
                        status = "返回无效，已回退规则"
                        reason = str(exc)[:500]
                    except Exception as exc:
                        status = "不可用，已回退规则"
                        reason = "AI 处理异常：{}".format(str(exc)[:450])

            try:
                if candidate:
                    db.update_candidate_ai_match(
                        candidate_id,
                        status=status,
                        combined_score=combined_score,
                        ai_score=ai_score,
                        confidence=confidence,
                        reason=reason,
                        evidence=evidence,
                        model=model,
                        matched_at=matched_at,
                    )
            except Exception as exc:
                # A single malformed/stale row must not stop the rest of the
                # local batch. Keep the error in the task log only.
                errors.append("候选人 {}：{}".format(candidate_id, str(exc)[:300]))
                status = "不可用，已回退规则"
                ai_score = None
                confidence = None
                reason = "AI 结果保存失败"

            if status == "已完成":
                metrics["ai_completed_count"] += 1
            elif "回退" in status or status == "无可用模板，已回退规则":
                metrics["ai_fallback_count"] += 1
                if reason:
                    errors.append(reason)
            else:
                metrics["ai_disabled_count"] += 1
            metrics["result_count"] = index
            metrics["existing_count"] = index
            _persist_job_metrics(job_id, metrics)
            progress = min(98, 5 + int(index * 90 / max(1, total)))
            db.update_job(
                job_id,
                progress=progress,
                result_count=index,
                message=(
                    "正在使用本机 AI 重新分析人才池（{}/{}） · AI 完成 {} · 回退 {}"
                ).format(
                    index,
                    total,
                    metrics["ai_completed_count"],
                    metrics["ai_fallback_count"],
                ),
            )

        final_status = "已完成" if not metrics["ai_fallback_count"] else "部分完成"
        error = "；".join(dict.fromkeys(errors))[:2000]
        final_message = (
            "AI 重分析完成：已处理 {}/{} · AI 完成 {} · 回退 {}"
        ).format(
            metrics["result_count"],
            total,
            metrics["ai_completed_count"],
            metrics["ai_fallback_count"],
        )
        if error:
            final_message += " · 原因：{}".format(error[:300])
        db.update_job(
            job_id,
            status=final_status,
            progress=100,
            result_count=metrics["result_count"],
            message=final_message,
            error=error,
            completed_at=db.now_iso(),
        )

    def _run_job(self, job_id: int, kind: str, config: Dict[str, Any]) -> None:
        db.update_job(
            job_id,
            status="正在采集",
            progress=2,
            message="正在连接公开来源",
            started_at=db.now_iso(),
        )
        try:
            if config["mode"] == "url":
                self._run_url_job(job_id, config)
            else:
                self._run_search_job(job_id, config)
            if kind == "每周自动":
                db.clear_schedule_retry()
        except NetworkUnavailable as exc:
            retry_at = db.defer_schedule_retry() if kind == "每周自动" else None
            fallback = (
                "网络恢复后将在 {} 自动补跑".format(
                    format_retry_time(retry_at)
                )
                if retry_at
                else "请检查 VPN 后重试"
            )
            db.update_job(
                job_id,
                status="网络不可用",
                progress=100,
                error=str(exc),
                message=_failure_message(job_id, fallback),
                completed_at=db.now_iso(),
            )
        except RateLimited as exc:
            retry_at = db.defer_schedule_retry() if kind == "每周自动" else None
            fallback = (
                "将在 {} 自动补跑".format(
                    format_retry_time(retry_at)
                )
                if retry_at
                else "来源限制了请求频率，请稍后重试"
            )
            db.update_job(
                job_id,
                status="API 限流",
                progress=100,
                error=str(exc),
                message=_failure_message(job_id, fallback),
                completed_at=db.now_iso(),
            )
        except (CollectorError, ValueError) as exc:
            if kind == "每周自动":
                db.clear_schedule_retry()
            db.update_job(
                job_id,
                status="执行失败",
                progress=100,
                error=str(exc),
                message=_failure_message(job_id, "任务未完成"),
                completed_at=db.now_iso(),
            )
        except Exception:
            db.update_job(
                job_id,
                status="执行失败",
                progress=100,
                error="发生未预期错误，请查看本地服务日志",
                message=_failure_message(job_id, "任务未完成"),
                completed_at=db.now_iso(),
            )
            raise

    def _run_url_job(self, job_id: int, config: Dict[str, Any]) -> None:
        # URL mode always represents one requested public profile.  Keep an
        # initial snapshot in the job row before touching the network so a
        # failed fetch still reports the exact target and source attempt.
        target = 1
        source_stat = _source_stat(_url_source_key(config.get("url", "")))
        source_stat["attempts"] = 1
        metrics: Dict[str, Any] = {
            "target_count": target,
            "result_count": 0,
            "discovered_count": 0,
            "unique_count": 0,
            "duplicate_count": 0,
            "filtered_count": 0,
            "inserted_count": 0,
            "existing_count": 0,
            "source_success_count": 0,
            "source_failure_count": 0,
            "source_stats": [source_stat],
            "direct_contact_count": 0,
            **_ai_counts([], bool(config.get("use_local_ai"))),
        }
        _persist_job_metrics(job_id, metrics)

        try:
            candidate = analyze_public_url(
                config["url"], config["roles"][0], config["cities"][0]
            )
        except Exception as exc:
            # CollectorError subclasses (including NetworkUnavailable and
            # RateLimited) are re-raised for _run_job to assign the final
            # status.  The source failure is persisted first so the task log
            # contains a precise, actionable reason.
            source_stat["failures"] = 1
            source_stat["errors"] = [str(exc)]
            metrics["source_failure_count"] = 1
            metrics["source_stats"] = [source_stat]
            _persist_job_metrics(job_id, metrics)
            db.update_job(
                job_id,
                message=_job_summary(metrics, target),
                error=str(exc),
            )
            raise

        source_stat["successes"] = 1
        source_stat["discovered"] = 1
        metrics.update(
            {
                "discovered_count": 1,
                "unique_count": 1,
                "source_success_count": 1,
                "source_stats": [source_stat],
            }
        )
        _persist_job_metrics(job_id, metrics)

        candidate["_requested_role"] = config["roles"][0]
        try:
            _prepare_candidate(
                candidate,
                config["roles"][0],
                config["cities"][0],
                config.get("use_local_ai", False),
                _template_for_role(config["roles"][0], config),
            )
        except Exception as exc:
            # The public source was reachable, but preparation/AI processing
            # failed.  Preserve the successful source and the one discovered
            # profile while recording the processing error for the outer job
            # handler.
            metrics["ai_failure_reason"] = str(exc)
            metrics.update(_ai_counts([candidate], bool(config.get("use_local_ai"))))
            _persist_job_metrics(job_id, metrics)
            db.update_job(
                job_id,
                message=_job_summary(metrics, target),
                error=str(exc),
            )
            raise

        if db.job_cancel_requested(job_id):
            metrics.update(
                {
                    "result_count": 0,
                    "filtered_count": 0,
                    "cancelled_count": 1,
                    "direct_contact_count": 0,
                    **_ai_counts([candidate], bool(config.get("use_local_ai"))),
                }
            )
            self._finish_cancelled(
                job_id,
                metrics,
            )
            return
        db.update_job(job_id, status="正在分析", progress=75, message="正在去重并保存证据")
        metrics = self._save_candidate_pool(
            job_id,
            {
                (
                    str(candidate.get("source") or "公开链接"),
                    str(candidate.get("external_id") or config["url"]),
                ): candidate
            },
            config,
            discovered_count=1,
            source_stats=[source_stat],
            prepared_candidates=[candidate],
            target_count=target,
        )
        db.update_job(
            job_id,
            status="已完成",
            progress=100,
            result_count=metrics["result_count"],
            message=_job_summary(metrics, target),
            completed_at=db.now_iso(),
        )

    def _run_search_job(self, job_id: int, config: Dict[str, Any]) -> None:
        prefer_contactable = config["prefer_contactable"]
        discovery_multiplier = (
            1.5
            if "github" in config["sources"] and not os.environ.get("GITHUB_TOKEN")
            else 2
        )
        discovery_target = (
            min(90, int(math.ceil(config["target"] * discovery_multiplier)))
            if prefer_contactable
            else config["target"]
        )
        custom_keywords = [
            item.strip() for item in config["keywords"].replace("，", ",").split(",") if item.strip()
        ][:3]
        combinations: List[Tuple[str, str, str, str]] = []
        for role in config["roles"]:
            template = _template_for_role(role, config)
            role_keywords = custom_keywords or [
                str(item).strip()
                for item in (template or {}).get("search_keywords", [])
                if str(item).strip()
            ][:5]
            if not role_keywords:
                role_keywords = [keyword_for_role(role, template)]
            for city in config["cities"]:
                for source in config["sources"]:
                    for keyword in role_keywords:
                        combinations.append((source, role, city, keyword))
        per_combination = max(
            1,
            min(15, int(math.ceil(discovery_target / max(1, len(combinations))))),
        )
        candidate_pool: Dict[Tuple[str, str], Dict[str, Any]] = {}
        source_errors: List[str] = []
        network_errors: List[str] = []
        rate_limit_errors: List[str] = []
        discovered_count = 0
        source_stats_by_source: Dict[str, Dict[str, Any]] = {
            source: _source_stat(source) for source in config["sources"]
        }

        for index, (source, role, city, keyword) in enumerate(combinations):
            if db.job_cancel_requested(job_id):
                metrics = self._save_candidate_pool(
                    job_id,
                    candidate_pool,
                    config,
                    discovered_count=discovered_count,
                    source_stats=list(source_stats_by_source.values()),
                )
                self._finish_cancelled(job_id, metrics)
                return
            progress = 5 + int((index / max(1, len(combinations))) * 70)
            db.update_job(
                job_id,
                progress=progress,
                message="正在采集 {} · {} · {} · {}".format(
                    SOURCE_LABELS.get(source, source), city, role, keyword
                ),
            )
            try:
                source_limit = (
                    min(per_combination, 4)
                    if source == "github" and not os.environ.get("GITHUB_TOKEN")
                    else per_combination
                )
                stat = source_stats_by_source[source]
                stat["attempts"] += 1
                candidates = SEARCH_COLLECTORS[source](keyword, city, role, source_limit)
            except (CollectorError, NetworkUnavailable, RateLimited) as exc:
                message = "{} / {} / {}：{}".format(
                    SOURCE_LABELS.get(source, source), city, role, str(exc)
                )
                source_errors.append(message)
                stat = source_stats_by_source[source]
                stat["failures"] += 1
                stat["errors"].append(message)
                if isinstance(exc, NetworkUnavailable):
                    network_errors.append(message)
                elif isinstance(exc, RateLimited):
                    rate_limit_errors.append(message)
                continue

            stat = source_stats_by_source[source]
            stat["successes"] += 1
            stat["discovered"] += len(candidates or [])
            discovered_count += len(candidates or [])
            for candidate in candidates or []:
                candidate["_requested_role"] = role
                key = (candidate["source"], str(candidate["external_id"]))
                candidate["contact_level"] = derive_contact_level(candidate)
                existing = candidate_pool.get(key)
                if existing is None or candidate_priority_key(
                    candidate, prefer_contactable
                ) < candidate_priority_key(existing, prefer_contactable):
                    candidate_pool[key] = candidate

        if db.job_cancel_requested(job_id):
            metrics = self._save_candidate_pool(
                job_id,
                candidate_pool,
                config,
                discovered_count=discovered_count,
                source_stats=list(source_stats_by_source.values()),
            )
            self._finish_cancelled(job_id, metrics)
            return
        if not candidate_pool and source_errors:
            source_stats = list(source_stats_by_source.values())
            source_success_count, source_failure_count = _source_counts(source_stats)
            failed_metrics = {
                "target_count": config["target"],
                "result_count": 0,
                "discovered_count": discovered_count,
                "unique_count": 0,
                "duplicate_count": discovered_count,
                "filtered_count": 0,
                "inserted_count": 0,
                "existing_count": 0,
                "source_success_count": source_success_count,
                "source_failure_count": source_failure_count,
                "source_stats": source_stats,
                "direct_contact_count": 0,
                **_ai_counts([], bool(config.get("use_local_ai"))),
            }
            _persist_job_metrics(job_id, failed_metrics)
            db.update_job(
                job_id,
                message=_job_summary(failed_metrics, config["target"]),
                error="；".join(source_errors[:5]),
            )
            if network_errors:
                raise NetworkUnavailable("；".join(network_errors[:3]))
            if rate_limit_errors:
                raise RateLimited("；".join(rate_limit_errors[:3]))
            raise CollectorError("；".join(source_errors[:3]))

        db.update_job(
            job_id,
            status="正在分析",
            progress=82,
            message="正在按匹配层和联系方式整理候选人",
        )
        metrics = self._save_candidate_pool(
            job_id,
            candidate_pool,
            config,
            discovered_count=discovered_count,
            source_stats=list(source_stats_by_source.values()),
        )

        status = (
            "部分完成"
            if source_errors or metrics["result_count"] < config["target"]
            else "已完成"
        )
        message = _job_summary(metrics, config["target"])
        db.update_job(
            job_id,
            status=status,
            progress=100,
            result_count=metrics["result_count"],
            message=message,
            error="；".join(source_errors[:5]),
            completed_at=db.now_iso(),
        )

    def _save_candidate_pool(
        self,
        job_id: int,
        candidate_pool: Dict[Tuple[str, str], Dict[str, Any]],
        config: Dict[str, Any],
        *,
        discovered_count: Optional[int] = None,
        source_stats: Optional[List[Dict[str, Any]]] = None,
        prepared_candidates: Optional[List[Dict[str, Any]]] = None,
        already_saved: bool = False,
        inserted_count: Optional[int] = None,
        existing_count: Optional[int] = None,
        target_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        pool_candidates = list(candidate_pool.values())
        prepared_for_metrics: List[Dict[str, Any]] = list(prepared_candidates or [])
        selected: List[Dict[str, Any]] = []
        saved_candidates: List[Dict[str, Any]] = (
            list(prepared_candidates or []) if already_saved else []
        )
        inserted = int(inserted_count) if inserted_count is not None else 0
        existing = int(existing_count) if existing_count is not None else 0
        if not already_saved:
            # A search-mode save starts its own inserted/existing counters;
            # callers may pass them only for URL-mode's already-saved path.
            inserted = 0
            existing = 0
        source_stats = source_stats or []
        source_success_count, source_failure_count = _source_counts(source_stats)
        target = int(
            target_count if target_count is not None else config.get("target") or 0
        )
        discovered = int(
            discovered_count if discovered_count is not None else len(pool_candidates)
        )
        selection_complete = False

        def partial_metrics(error: Optional[BaseException] = None) -> Dict[str, Any]:
            """Build a truthful snapshot even when saving stops mid-batch."""
            ai_candidates = saved_candidates or prepared_for_metrics
            snapshot: Dict[str, Any] = {
                "target_count": target,
                "result_count": len(saved_candidates),
                "discovered_count": discovered,
                "unique_count": len(pool_candidates),
                "duplicate_count": max(0, discovered - len(pool_candidates)),
                "filtered_count": (
                    max(0, len(pool_candidates) - len(selected))
                    if selection_complete
                    else 0
                ),
                "inserted_count": inserted,
                "existing_count": existing,
                "source_success_count": source_success_count,
                "source_failure_count": source_failure_count,
                "source_stats": source_stats,
                "direct_contact_count": sum(
                    1 for candidate in saved_candidates if has_direct_contact(candidate)
                ),
            }
            snapshot.update(_ai_counts(ai_candidates, bool(config.get("use_local_ai"))))
            if error is not None:
                snapshot["save_error"] = str(error)
            if snapshot.get("ai_fallback_count"):
                reasons = [
                    str(candidate.get("ai_match_reason") or "").strip()
                    for candidate in ai_candidates
                    if str(candidate.get("ai_match_reason") or "").strip()
                ]
                if reasons:
                    snapshot["ai_failure_reason"] = reasons[0]
            return snapshot

        try:
            suppress_shared_public_emails(pool_candidates)
            db.merge_existing_verified_contacts(pool_candidates)
            if prepared_candidates is None:
                ai_state: Dict[str, str] = {}
                for candidate in pool_candidates:
                    role = str(
                        candidate.get("_requested_role")
                        or candidate.get("suggested_role")
                        or config["roles"][0]
                    )
                    city = str(candidate.get("city") or config["cities"][0])
                    _prepare_candidate(
                        candidate,
                        role,
                        city,
                        config.get("use_local_ai", False),
                        _template_for_role(role, config),
                        ai_state=ai_state,
                    )
                    prepared_for_metrics.append(candidate)
            selected = rank_candidates(
                pool_candidates, config["prefer_contactable"]
            )[: config["target"]]
            selection_complete = True
            if not already_saved:
                for candidate in selected:
                    candidate_id, was_inserted = db.upsert_candidate(
                        candidate, job_id=job_id
                    )
                    inserted += 1 if was_inserted else 0
                    existing += 0 if was_inserted else 1
                    saved = db.get_candidate(candidate_id)
                    if saved:
                        saved_candidates.append(saved)
            metrics = partial_metrics()
            _persist_job_metrics(job_id, metrics)
            return metrics
        except Exception as exc:
            metrics = partial_metrics(exc)
            # Persist before re-raising.  _run_job's outer handler will set
            # the final failure status while _failure_message keeps this
            # exact partial summary instead of replacing it with all zeroes.
            _persist_job_metrics(job_id, metrics)
            db.update_job(
                job_id,
                result_count=metrics["result_count"],
                message=_job_summary(metrics, target)
                + "；保存阶段异常，已保留部分统计",
                error=str(exc),
            )
            raise

    def _finish_cancelled(self, job_id: int, metrics: Optional[Dict[str, Any]] = None) -> None:
        metrics = metrics or {
            "target_count": 0,
            "result_count": 0,
            "direct_contact_count": 0,
            **_ai_counts([], False),
        }
        _persist_job_metrics(job_id, metrics)
        db.update_job(
            job_id,
            status="已取消",
            progress=100,
            result_count=int(metrics.get("result_count") or 0),
            message=_job_summary(metrics, int(metrics.get("target_count") or 0), prefix="任务已取消；"),
            completed_at=db.now_iso(),
        )

    def _submit_due_schedule(self, config: Dict[str, Any]) -> int:
        try:
            return self.submit(
                "每周自动", config, allow_role_snapshots=True
            )
        except Exception as exc:
            job_id = db.create_job("每周自动", config)
            db.update_job(
                job_id,
                status="执行失败",
                progress=100,
                error=(
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "定时任务创建失败"
                ),
                message="定时任务配置无效，请检查岗位模板",
                completed_at=db.now_iso(),
            )
            return job_id

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.wait(20):
            try:
                schedule = db.get_schedule()
                if not schedule["enabled"]:
                    continue
                next_run = next_weekly_run(
                    schedule["weekday"], schedule["hour"], schedule["minute"]
                ).isoformat(timespec="seconds")
                config = db.claim_due_schedule(next_run)
                if config:
                    self._submit_due_schedule(config)
            except Exception:
                # Scheduler failures are retried on the next tick and never stop the web server.
                continue
