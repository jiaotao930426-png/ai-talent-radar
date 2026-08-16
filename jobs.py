import math
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
}
SEARCH_COLLECTORS = {
    "github": search_github,
    "gitee": search_gitee,
    "gitlab": search_gitlab,
    "huggingface": search_huggingface,
    "stackoverflow": search_stackoverflow,
}


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
        except (ValueError, TypeError) as exc:
            candidate["ai_match_status"] = "返回无效，已回退规则"
            candidate["ai_match_reason"] = str(exc)
    elif use_local_ai:
        candidate["ai_match_status"] = "无可用模板，已回退规则"
    return candidate


class JobManager:
    def __init__(self) -> None:
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

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
            db.update_job(
                job_id,
                status="网络不可用",
                progress=100,
                error=str(exc),
                message=(
                    "网络恢复后将在 {} 自动补跑".format(
                        format_retry_time(retry_at)
                    )
                    if retry_at
                    else "请检查 VPN 后重试"
                ),
                completed_at=db.now_iso(),
            )
        except RateLimited as exc:
            retry_at = db.defer_schedule_retry() if kind == "每周自动" else None
            db.update_job(
                job_id,
                status="API 限流",
                progress=100,
                error=str(exc),
                message=(
                    "将在 {} 自动补跑".format(
                        format_retry_time(retry_at)
                    )
                    if retry_at
                    else "来源限制了请求频率，请稍后重试"
                ),
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
                message="任务未完成",
                completed_at=db.now_iso(),
            )
        except Exception:
            db.update_job(
                job_id,
                status="执行失败",
                progress=100,
                error="发生未预期错误，请查看本地服务日志",
                message="任务未完成",
                completed_at=db.now_iso(),
            )
            raise

    def _run_url_job(self, job_id: int, config: Dict[str, Any]) -> None:
        candidate = analyze_public_url(
            config["url"], config["roles"][0], config["cities"][0]
        )
        candidate["_requested_role"] = config["roles"][0]
        _prepare_candidate(
            candidate,
            config["roles"][0],
            config["cities"][0],
            config.get("use_local_ai", False),
            _template_for_role(config["roles"][0], config),
        )
        if db.job_cancel_requested(job_id):
            self._finish_cancelled(job_id)
            return
        db.update_job(job_id, status="正在分析", progress=75, message="正在去重并保存证据")
        db.upsert_candidate(candidate, job_id=job_id)
        db.update_job(
            job_id,
            status="已完成",
            progress=100,
            result_count=1,
            message="已加入人才池",
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

        for index, (source, role, city, keyword) in enumerate(combinations):
            if db.job_cancel_requested(job_id):
                result_count, _ = self._save_candidate_pool(job_id, candidate_pool, config)
                self._finish_cancelled(job_id, result_count)
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
                candidates = SEARCH_COLLECTORS[source](keyword, city, role, source_limit)
            except (CollectorError, NetworkUnavailable, RateLimited) as exc:
                message = "{} / {} / {}：{}".format(
                    SOURCE_LABELS.get(source, source), city, role, str(exc)
                )
                source_errors.append(message)
                if isinstance(exc, NetworkUnavailable):
                    network_errors.append(message)
                elif isinstance(exc, RateLimited):
                    rate_limit_errors.append(message)
                continue

            for candidate in candidates:
                candidate["_requested_role"] = role
                key = (candidate["source"], str(candidate["external_id"]))
                candidate["contact_level"] = derive_contact_level(candidate)
                existing = candidate_pool.get(key)
                if existing is None or candidate_priority_key(
                    candidate, prefer_contactable
                ) < candidate_priority_key(existing, prefer_contactable):
                    candidate_pool[key] = candidate

        if db.job_cancel_requested(job_id):
            result_count, _ = self._save_candidate_pool(job_id, candidate_pool, config)
            self._finish_cancelled(job_id, result_count)
            return
        if not candidate_pool and source_errors:
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
        result_count, direct_contact_count = self._save_candidate_pool(
            job_id, candidate_pool, config
        )

        status = "部分完成" if source_errors or result_count < config["target"] else "已完成"
        message = "已生成 {} 名候选人，其中 {} 人有公开联系方式".format(
            result_count, direct_contact_count
        )
        if result_count < config["target"]:
            message += "，低于目标 {} 人".format(config["target"])
        db.update_job(
            job_id,
            status=status,
            progress=100,
            result_count=result_count,
            message=message,
            error="；".join(source_errors[:5]),
            completed_at=db.now_iso(),
        )

    def _save_candidate_pool(
        self,
        job_id: int,
        candidate_pool: Dict[Tuple[str, str], Dict[str, Any]],
        config: Dict[str, Any],
    ) -> Tuple[int, int]:
        pool_candidates = list(candidate_pool.values())
        suppress_shared_public_emails(pool_candidates)
        db.merge_existing_verified_contacts(pool_candidates)
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
            )
        selected = rank_candidates(
            pool_candidates, config["prefer_contactable"]
        )[: config["target"]]
        saved_candidates = []
        for candidate in selected:
            candidate_id, _ = db.upsert_candidate(candidate, job_id=job_id)
            saved = db.get_candidate(candidate_id)
            if saved:
                saved_candidates.append(saved)
        return len(saved_candidates), sum(
            1 for candidate in saved_candidates if has_direct_contact(candidate)
        )

    def _finish_cancelled(self, job_id: int, result_count: int = 0) -> None:
        db.update_job(
            job_id,
            status="已取消",
            progress=100,
            result_count=result_count,
            message="任务已取消，已保存完成的结果",
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
