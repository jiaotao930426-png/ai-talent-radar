"""Source capability metadata and opt-in public endpoint health checks.

Health checks intentionally do not use login state, cookies, browser automation,
or platform-specific anti-bot workarounds. They only make one small request to
the documented/public endpoint used by each existing collector.
"""

import http.client
import json
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


USER_AGENT = "AI-Talent-Radar/0.1 (local source health check)"
PROBE_TIMEOUT_SECONDS = 4
PROBE_CACHE_SECONDS = 30


SOURCE_CATALOG: Tuple[Dict[str, Any], ...] = (
    {
        "key": "github",
        "label": "GitHub",
        "group": "开源社区",
        "mode": "automatic",
        "description": "用户与仓库公开 API",
        "probe_url": "https://api.github.com/rate_limit",
        "expected": "json",
        "next_step": "检查网络；需要更高配额时配置 GitHub Token",
    },
    {
        "key": "gitee",
        "label": "Gitee",
        "group": "开源社区",
        "mode": "automatic",
        "description": "公开项目搜索与用户 API",
        "probe_url": "https://so.gitee.com/v1/search/widget/wong1slagnlmzwvsu5ya?q=agent&from=0&size=1",
        "expected": "json",
        "next_step": "检查网络；搜索接口异常时稍后重试",
    },
    {
        "key": "gitlab",
        "label": "GitLab",
        "group": "开源社区",
        "mode": "automatic",
        "description": "公开项目与用户 API",
        "probe_url": "https://gitlab.com/api/v4/version",
        "expected": "json",
        "next_step": "检查网络或稍后重试",
    },
    {
        "key": "huggingface",
        "label": "Hugging Face",
        "group": "模型社区",
        "mode": "automatic",
        "description": "公开模型 API",
        "probe_url": "https://huggingface.co/api/models?limit=1",
        "expected": "json",
        "next_step": "检查网络；候选人的城市和邮箱仍需人工核验",
    },
    {
        "key": "stackoverflow",
        "label": "Stack Overflow",
        "group": "技术论坛",
        "mode": "automatic",
        "description": "Stack Exchange 公开 API",
        "probe_url": "https://api.stackexchange.com/2.3/info?site=stackoverflow",
        "expected": "json",
        "next_step": "检查网络；配额耗尽后等待下个配额周期",
    },
    {
        "key": "modelscope",
        "label": "ModelScope",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "尚未实现自动采集器",
        "next_step": "核实官方开放接口或授权合作",
    },
    {
        "key": "gitcode",
        "label": "GitCode",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "尚未实现自动采集器",
        "next_step": "核实官方开放接口或授权合作",
    },
    {
        "key": "juejin",
        "label": "掘金",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "尚未实现自动采集器",
        "next_step": "优先评估公开链接低频解析或官方合作",
    },
    {
        "key": "csdn",
        "label": "CSDN",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "尚未实现自动采集器",
        "next_step": "优先评估公开链接低频解析或官方合作",
    },
    {
        "key": "segmentfault",
        "label": "SegmentFault",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "尚未实现自动采集器",
        "next_step": "先确认公开服务稳定性，再评估公开链接解析",
    },
    {
        "key": "zhihu",
        "label": "知乎",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "不绕过登录、验证码或访问控制",
        "next_step": "使用官方开放平台、授权数据或人工链接",
    },
    {
        "key": "bilibili",
        "label": "B站",
        "group": "国内社区与社交媒体",
        "mode": "planned",
        "description": "不绕过登录、验证码或访问控制",
        "next_step": "使用官方开放平台、授权数据或人工链接",
    },
    {
        "key": "douyin",
        "label": "抖音",
        "group": "国内社区与社交媒体",
        "mode": "manual",
        "description": "仅人工粘贴候选人主动公开的链接",
        "next_step": "候选人主动提交或使用官方授权入口",
    },
    {
        "key": "xiaohongshu",
        "label": "小红书",
        "group": "国内社区与社交媒体",
        "mode": "manual",
        "description": "仅人工粘贴候选人主动公开的链接",
        "next_step": "候选人主动提交或使用官方授权入口",
    },
    {
        "key": "weibo",
        "label": "微博",
        "group": "国内社区与社交媒体",
        "mode": "manual",
        "description": "仅人工粘贴候选人主动公开的链接",
        "next_step": "候选人主动提交或使用官方授权入口",
    },
)

_cache_lock = threading.Lock()
_cached_probe: Optional[Tuple[float, List[Dict[str, Any]]]] = None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _static_result(source: Dict[str, Any]) -> Dict[str, Any]:
    mode = source["mode"]
    if mode == "automatic":
        status = "not_checked"
        status_label = "待检测"
        detail = "已接入公开接口，尚未执行本次连通性检测"
    elif mode == "planned":
        status = "not_implemented"
        status_label = "规划中"
        detail = source["description"]
    else:
        status = "manual_only"
        status_label = "人工链接"
        detail = source["description"]
    return {
        "key": source["key"],
        "label": source["label"],
        "group": source["group"],
        "mode": mode,
        "description": source["description"],
        "status": status,
        "status_label": status_label,
        "detail": detail,
        "next_step": source["next_step"],
        "checked_at": "",
    }


def _error_result(source: Dict[str, Any], status: str, status_label: str, detail: str) -> Dict[str, Any]:
    result = _static_result(source)
    result.update(
        {
            "status": status,
            "status_label": status_label,
            "detail": detail,
            "checked_at": _now_iso(),
        }
    )
    return result


def _probe_url(url: str, expected: str) -> Tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/html;q=0.9",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            body = response.read(128 * 1024)
            if expected == "json":
                try:
                    json.loads(body.decode("utf-8", errors="replace"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError("接口返回的不是有效 JSON，可能是登录页或挑战页") from exc
            return "available", "公开接口可访问"
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "auth_required", "公开接口要求授权"
        if exc.code == 407:
            return "proxy_auth_required", "网络代理要求授权"
        if exc.code == 429:
            return "rate_limited", "公开接口暂时限制请求频率"
        if exc.code in (403, 451):
            return "access_blocked", "公开接口拒绝了当前请求"
        if 500 <= exc.code <= 599:
            return "upstream_error", "平台公开接口暂时异常"
        return "http_error", "公开接口返回 HTTP {}".format(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", None)
        if reason:
            return "network_unavailable", "无法连接公开接口：{}".format(str(reason)[:120])
        return "network_unavailable", "无法连接公开接口，可能需要检查网络或 VPN"
    except ValueError as exc:
        return "challenge", str(exc)


def _probed_result(source: Dict[str, Any]) -> Dict[str, Any]:
    result = _static_result(source)
    status, detail = _probe_url(source["probe_url"], source["expected"])
    labels = {
        "available": "可访问",
        "auth_required": "需要授权",
        "proxy_auth_required": "代理需授权",
        "rate_limited": "频率受限",
        "access_blocked": "访问被拒",
        "upstream_error": "上游异常",
        "http_error": "接口异常",
        "network_unavailable": "网络不可用",
        "challenge": "挑战页/验证码",
    }
    result.update(
        {
            "status": status,
            "status_label": labels.get(status, "检测失败"),
            "detail": detail,
            "checked_at": _now_iso(),
        }
    )
    return result


def get_source_health(probe: bool = False) -> Dict[str, Any]:
    """Return source capability metadata, optionally probing automatic sources."""
    global _cached_probe
    if not probe:
        return {"items": [deepcopy(_static_result(source)) for source in SOURCE_CATALOG], "checked_at": ""}

    now = datetime.now().timestamp()
    with _cache_lock:
        if _cached_probe and now - _cached_probe[0] < PROBE_CACHE_SECONDS:
            return {"items": deepcopy(_cached_probe[1]), "checked_at": _cached_probe[1][0].get("checked_at", "")}

    results = []
    for source in SOURCE_CATALOG:
        if source["mode"] == "automatic":
            results.append(_probed_result(source))
        else:
            results.append(_static_result(source))
    checked_at = next((item["checked_at"] for item in results if item["checked_at"]), _now_iso())
    with _cache_lock:
        _cached_probe = (now, results)
    return {"items": deepcopy(results), "checked_at": checked_at}
