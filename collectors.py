import html
import html.parser
import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scoring import city_matches, detect_city, education_status, score_candidate


USER_AGENT = "AI-Talent-Radar/0.1 (local public-data recruitment research)"
GITEE_WIDGET = "wong1slagnlmzwvsu5ya"
FETCH_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.5
PUBLIC_PROFILE_HOSTS = {"github.com", "www.github.com", "gitee.com", "www.gitee.com"}
PUBLIC_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9._%+-])"
)


class CollectorError(RuntimeError):
    pass


class NetworkUnavailable(CollectorError):
    pass


class RateLimited(CollectorError):
    pass


def _usable_public_email(value: Any) -> str:
    email = urllib.parse.unquote(str(value or "")).split("?", 1)[0].strip().lower()
    if not PUBLIC_EMAIL_PATTERN.fullmatch(email):
        return ""
    if "noreply" in email or email.endswith("@users.noreply.github.com"):
        return ""
    return email


class _PublicEmailParser(html.parser.HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.mailto: List[str] = []
        self.visible: List[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = {str(key).lower(): str(value or "").lower() for key, value in attrs}
        footer_marker = " ".join(
            attributes.get(key, "") for key in ("id", "class")
        )
        should_ignore = (
            tag in {"script", "style", "template", "footer"}
            or attributes.get("role") == "contentinfo"
            or "footer" in footer_marker
        )
        if self._ignored_depth and tag not in self.VOID_TAGS:
            self._ignored_depth += 1
        elif should_ignore:
            self._ignored_depth = 1
        if self._ignored_depth:
            return
        for key, value in attrs:
            if key.lower() != "href" or not str(value or "").lower().startswith("mailto:"):
                continue
            email = _usable_public_email(str(value)[7:])
            if email and email not in self.mailto:
                self.mailto.append(email)

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_depth:
            self._ignored_depth -= 1

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        for match in PUBLIC_EMAIL_PATTERN.findall(data or ""):
            email = _usable_public_email(match)
            if email and email not in self.visible:
                self.visible.append(email)


def extract_public_profile_email(markup: str) -> str:
    parser = _PublicEmailParser()
    parser.feed(markup)
    if len(parser.mailto) == 1:
        return parser.mailto[0]
    if len(parser.mailto) > 1:
        return ""
    return parser.visible[0] if len(parser.visible) == 1 else ""


def suppress_shared_public_emails(candidates: List[Dict[str, Any]]) -> int:
    counts = Counter(
        (str(candidate.get("source") or ""), _usable_public_email(candidate.get("contact_email")))
        for candidate in candidates
        if _usable_public_email(candidate.get("contact_email"))
    )
    suppressed = 0
    for candidate in candidates:
        key = (
            str(candidate.get("source") or ""),
            _usable_public_email(candidate.get("contact_email")),
        )
        if key[1] and counts[key] > 1:
            candidate["contact_email"] = ""
            candidate["contact_email_source_url"] = ""
            candidate["contact_email_verified_at"] = None
            suppressed += 1
    return suppressed


def public_profile_email(url: str, timeout: int = 18) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in PUBLIC_PROFILE_HOSTS:
        return ""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_host = (urllib.parse.urlparse(response.geturl()).hostname or "").lower()
                if final_host not in PUBLIC_PROFILE_HOSTS:
                    raise CollectorError("公开主页跳转到了未支持的网站")
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "text/html" not in content_type:
                    raise CollectorError("公开主页没有返回 HTML")
                markup = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
            return extract_public_profile_email(markup)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise RateLimited("公开主页限制了请求频率") from exc
            if attempt + 1 >= FETCH_ATTEMPTS:
                raise CollectorError("公开主页返回 HTTP {}".format(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            if attempt + 1 >= FETCH_ATTEMPTS:
                raise NetworkUnavailable("公开主页暂时无法访问") from exc
        time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    return ""


def enrich_public_email(candidate: Dict[str, Any]) -> None:
    email = _usable_public_email(candidate.get("contact_email"))
    profile_url = str(candidate.get("profile_url") or "").strip()
    if not email and candidate.get("source") in {"github", "gitee"}:
        try:
            email = public_profile_email(profile_url)
        except CollectorError:
            email = ""
    if email:
        candidate["contact_email"] = email
        candidate["contact_email_source_url"] = profile_url
        candidate["contact_email_verified_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )


def fetch_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 22) -> Any:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise RateLimited("来源限制了请求频率，请稍后重试")
            if exc.code == 404:
                raise CollectorError("公开页面或账号不存在")
            if exc.code not in (500, 502, 503, 504) or attempt + 1 >= FETCH_ATTEMPTS:
                raise CollectorError("来源返回 HTTP {}".format(exc.code))
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            if attempt + 1 >= FETCH_ATTEMPTS:
                raise NetworkUnavailable("网络或 VPN 不可用，无法连接公开来源") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise CollectorError("来源返回了无法解析的数据") from exc
        time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    raise NetworkUnavailable("网络或 VPN 不可用，无法连接公开来源")


def github_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return headers


def github_user(username: str, requested_role: str, requested_city: str) -> Dict[str, Any]:
    safe_user = urllib.parse.quote(username, safe="")
    profile = fetch_json(
        "https://api.github.com/users/{}".format(safe_user), headers=github_headers()
    )
    repos = fetch_json(
        "https://api.github.com/users/{}/repos?sort=updated&direction=desc&per_page=100".format(safe_user),
        headers=github_headers(),
    )
    location = profile.get("location") or ""
    detected_city = detect_city(location)
    evidence = [
        {
            "title": repo.get("name") or "公开仓库",
            "url": repo.get("html_url") or "",
            "description": repo.get("description") or "",
            "stars": repo.get("stargazers_count") or 0,
            "language": repo.get("language") or "",
            "is_fork": bool(repo.get("fork")),
        }
        for repo in repos
        if repo.get("html_url")
    ]
    candidate = {
        "source": "github",
        "external_id": profile.get("id") or username,
        "username": profile.get("login") or username,
        "display_name": profile.get("name") or profile.get("login") or username,
        "city": detected_city,
        "bio": profile.get("bio") or "",
        "company": profile.get("company") or "",
        "profile_url": profile.get("html_url") or "https://github.com/{}".format(username),
        "contact_email": profile.get("email") or "",
        "contact_url": profile.get("blog") or profile.get("html_url") or "",
        "education_status": education_status(
            "{} {}".format(profile.get("bio") or "", profile.get("company") or "")
        ),
        "age_status": "待本人确认",
        "source_updated_at": profile.get("updated_at") or "",
    }
    enrich_public_email(candidate)
    score, role, ranked = score_candidate(candidate, evidence, requested_role, requested_city)
    candidate.update({
        "match_score": score,
        "rule_match_score": score,
        "suggested_role": role,
        "evidence": evidence,
        "_legacy_ranked_evidence": ranked,
    })
    return candidate


def search_github(keyword: str, city: str, role: str, limit: int) -> List[Dict[str, Any]]:
    city_query = {"北京": "Beijing", "重庆": "Chongqing"}.get(city, city)
    query_parts = [keyword.strip() or "agent", "in:bio", "repos:>2"]
    if city_query and city_query != "全部":
        query_parts.append("location:{}".format(city_query))
    query = " ".join(query_parts)
    url = "https://api.github.com/search/users?{}".format(
        urllib.parse.urlencode({"q": query, "per_page": max(1, min(limit, 30))})
    )
    result = fetch_json(url, headers=github_headers())
    candidates = []
    for item in result.get("items", []):
        try:
            candidate = github_user(item["login"], role, city)
        except (NetworkUnavailable, RateLimited):
            raise
        except CollectorError:
            continue
        if city in ("北京", "重庆") and candidate["city"] != city:
            continue
        candidates.append(candidate)
    return candidates


def gitee_user(username: str) -> Dict[str, Any]:
    safe_user = urllib.parse.quote(username, safe="")
    return fetch_json("https://gitee.com/api/v5/users/{}".format(safe_user))


def gitee_candidate_from_repo(
    repo_hit: Dict[str, Any], requested_role: str, requested_city: str
) -> Dict[str, Any]:
    fields = repo_hit.get("fields") or {}
    owners = fields.get("owner.path.keyword") or []
    if not owners:
        raise CollectorError("Gitee 项目缺少公开作者信息")
    username = owners[0]
    profile = gitee_user(username)
    bio = profile.get("bio") or ""
    city = detect_city(bio)
    title = (fields.get("title") or [username])[0]
    url = (fields.get("url") or [profile.get("html_url")])[0]
    description = (fields.get("description") or [""])[0]
    evidence = [
        {
            "title": title,
            "url": url,
            "description": description,
            "stars": (fields.get("count.star") or [0])[0],
            "language": ", ".join(fields.get("langs") or []),
            "is_fork": bool((fields.get("fork") or [0])[0]),
        }
    ]
    candidate = {
        "source": "gitee",
        "external_id": profile.get("id") or username,
        "username": profile.get("login") or username,
        "display_name": profile.get("name") or username,
        "city": city,
        "bio": bio,
        "company": profile.get("company") or "",
        "profile_url": profile.get("html_url") or "https://gitee.com/{}".format(username),
        "contact_email": profile.get("email") or "",
        "contact_url": profile.get("blog") or profile.get("html_url") or "",
        "education_status": education_status("{} {}".format(bio, profile.get("company") or "")),
        "age_status": "待本人确认",
        "source_updated_at": profile.get("updated_at") or "",
    }
    enrich_public_email(candidate)
    score, role, ranked = score_candidate(candidate, evidence, requested_role, requested_city)
    if requested_city in ("北京", "重庆") and city == "待核验":
        score = max(0, score - 12)
    candidate.update({
        "match_score": score,
        "rule_match_score": score,
        "suggested_role": role,
        "evidence": evidence,
        "_legacy_ranked_evidence": ranked,
    })
    return candidate


def search_gitee(keyword: str, city: str, role: str, limit: int) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {"q": keyword.strip() or "agent", "from": 0, "size": max(1, min(limit, 30))}
    )
    result = fetch_json(
        "https://so.gitee.com/v1/search/widget/{}?{}".format(GITEE_WIDGET, params)
    )
    candidates = []
    seen = set()
    for hit in ((result.get("hits") or {}).get("hits") or []):
        owners = (hit.get("fields") or {}).get("owner.path.keyword") or []
        if not owners or owners[0] in seen:
            continue
        seen.add(owners[0])
        try:
            candidate = gitee_candidate_from_repo(hit, role, city)
        except (NetworkUnavailable, RateLimited):
            raise
        except CollectorError:
            continue
        candidates.append(candidate)
    return candidates


def _target_city_allows(candidate_city: str, requested_city: str) -> bool:
    if requested_city not in ("北京", "重庆"):
        return True
    return candidate_city in (requested_city, "待核验")


def _strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _timestamp_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def gitlab_user(username: str) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"username": username})
    users = fetch_json("https://gitlab.com/api/v4/users?{}".format(query))
    if not isinstance(users, list) or not users:
        raise CollectorError("GitLab 公开账号不存在")
    return users[0]


def gitlab_user_projects(user_id: Any, limit: int = 50) -> List[Dict[str, Any]]:
    safe_id = urllib.parse.quote(str(user_id), safe="")
    query = urllib.parse.urlencode(
        {
            "visibility": "public",
            "order_by": "last_activity_at",
            "sort": "desc",
            "simple": "true",
            "per_page": max(1, min(limit, 100)),
        }
    )
    projects = fetch_json(
        "https://gitlab.com/api/v4/users/{}/projects?{}".format(safe_id, query)
    )
    return projects if isinstance(projects, list) else []


def gitlab_candidate(
    profile: Dict[str, Any],
    projects: List[Dict[str, Any]],
    requested_role: str,
    requested_city: str,
) -> Dict[str, Any]:
    location = profile.get("location") or ""
    bio = profile.get("bio") or ""
    evidence = [
        {
            "title": project.get("name") or project.get("path") or "公开项目",
            "url": project.get("web_url") or "",
            "description": project.get("description") or "",
            "stars": project.get("star_count") or 0,
            "language": "",
            "is_fork": bool(project.get("forked_from_project")),
        }
        for project in projects
        if project.get("web_url")
    ]
    username = profile.get("username") or "unknown"
    candidate = {
        "source": "gitlab",
        "external_id": profile.get("id") or username,
        "username": username,
        "display_name": profile.get("name") or username,
        "city": detect_city(location),
        "bio": bio,
        "company": profile.get("organization") or "",
        "profile_url": profile.get("web_url") or "https://gitlab.com/{}".format(username),
        "contact_email": profile.get("public_email") or "",
        "contact_url": profile.get("website_url") or profile.get("web_url") or "",
        "education_status": education_status("{} {}".format(bio, profile.get("organization") or "")),
        "age_status": "待本人确认",
        "source_updated_at": profile.get("last_activity_on") or "",
    }
    enrich_public_email(candidate)
    score, role, ranked = score_candidate(candidate, evidence, requested_role, requested_city)
    candidate.update({
        "match_score": score,
        "rule_match_score": score,
        "suggested_role": role,
        "evidence": evidence,
        "_legacy_ranked_evidence": ranked,
    })
    return candidate


def search_gitlab(keyword: str, city: str, role: str, limit: int) -> List[Dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "search": keyword.strip() or "agent",
            "visibility": "public",
            "order_by": "last_activity_at",
            "sort": "desc",
            "simple": "true",
            "per_page": max(1, min(limit * 3, 60)),
        }
    )
    projects = fetch_json("https://gitlab.com/api/v4/projects?{}".format(query))
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for project in projects if isinstance(projects, list) else []:
        namespace = project.get("namespace") or {}
        if namespace.get("kind") != "user" or not namespace.get("path"):
            continue
        grouped.setdefault(str(namespace["path"]), []).append(project)
    candidates = []
    for username, user_projects in grouped.items():
        try:
            profile = gitlab_user(username)
            candidate = gitlab_candidate(profile, user_projects, role, city)
        except (NetworkUnavailable, RateLimited):
            raise
        except CollectorError:
            continue
        if _target_city_allows(candidate["city"], city):
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def _huggingface_author(model: Dict[str, Any]) -> str:
    author = str(model.get("author") or "").strip()
    model_id = str(model.get("id") or model.get("modelId") or "")
    if not author and "/" in model_id:
        author = model_id.split("/", 1)[0]
    return author


def huggingface_candidate(
    username: str,
    models: List[Dict[str, Any]],
    requested_role: str,
    requested_city: str,
) -> Dict[str, Any]:
    evidence = []
    last_modified = ""
    for model in models:
        model_id = str(model.get("id") or model.get("modelId") or "")
        if not model_id:
            continue
        tags = [str(tag) for tag in (model.get("tags") or [])[:10]]
        description = " · ".join(
            item
            for item in [str(model.get("pipeline_tag") or ""), ", ".join(tags)]
            if item
        )
        evidence.append(
            {
                "title": model_id.split("/", 1)[-1],
                "url": "https://huggingface.co/{}".format(model_id),
                "description": description,
                "stars": model.get("likes") or 0,
                "language": str(model.get("library_name") or model.get("pipeline_tag") or ""),
                "is_fork": False,
            }
        )
        last_modified = max(last_modified, str(model.get("lastModified") or ""))
    bio = "公开模型：{}".format("、".join(item["title"] for item in evidence[:4])) if evidence else ""
    candidate = {
        "source": "huggingface",
        "external_id": username,
        "username": username,
        "display_name": username,
        "city": "待核验",
        "bio": bio,
        "company": "",
        "profile_url": "https://huggingface.co/{}".format(username),
        "contact_email": "",
        "contact_url": "https://huggingface.co/{}".format(username),
        "education_status": "待核验",
        "age_status": "待本人确认",
        "source_updated_at": last_modified,
    }
    score, role, ranked = score_candidate(candidate, evidence, requested_role, requested_city)
    candidate.update({
        "match_score": score,
        "rule_match_score": score,
        "suggested_role": role,
        "evidence": evidence,
        "_legacy_ranked_evidence": ranked,
    })
    return candidate


def huggingface_user(username: str, role: str, city: str) -> Dict[str, Any]:
    query = urllib.parse.urlencode(
        {"author": username, "limit": 100, "sort": "lastModified", "direction": -1}
    )
    models = fetch_json("https://huggingface.co/api/models?{}".format(query))
    if not isinstance(models, list):
        raise CollectorError("Hugging Face 返回了无效的公开模型数据")
    return huggingface_candidate(username, models, role, city)


def search_huggingface(keyword: str, city: str, role: str, limit: int) -> List[Dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "search": keyword.strip() or "agent",
            "limit": max(1, min(limit * 4, 80)),
            "sort": "lastModified",
            "direction": -1,
        }
    )
    models = fetch_json("https://huggingface.co/api/models?{}".format(query))
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for model in models if isinstance(models, list) else []:
        author = _huggingface_author(model)
        if author:
            grouped.setdefault(author, []).append(model)
    return [
        huggingface_candidate(username, user_models, role, city)
        for username, user_models in list(grouped.items())[:limit]
    ]


def stackoverflow_user(user_id: Any) -> Dict[str, Any]:
    safe_id = urllib.parse.quote(str(user_id), safe="")
    query = urllib.parse.urlencode({"site": "stackoverflow", "pagesize": 1})
    result = fetch_json(
        "https://api.stackexchange.com/2.3/users/{}?{}".format(safe_id, query)
    )
    items = result.get("items") if isinstance(result, dict) else None
    if not items:
        raise CollectorError("Stack Overflow 公开账号不存在")
    return items[0]


def stackoverflow_candidate(
    profile: Dict[str, Any],
    questions: List[Dict[str, Any]],
    requested_role: str,
    requested_city: str,
) -> Dict[str, Any]:
    evidence = [
        {
            "title": _strip_html(question.get("title")) or "公开问答",
            "url": question.get("link") or "",
            "description": ", ".join(question.get("tags") or []),
            "stars": max(0, int(question.get("score") or 0)),
            "language": ", ".join(question.get("tags") or []),
            "is_fork": False,
        }
        for question in questions
        if question.get("link")
    ]
    user_id = profile.get("user_id")
    display_name = _strip_html(profile.get("display_name")) or str(user_id)
    bio = _strip_html(profile.get("about_me"))
    candidate = {
        "source": "stackoverflow",
        "external_id": user_id or display_name,
        "username": str(user_id or display_name),
        "display_name": display_name,
        "city": detect_city(profile.get("location") or ""),
        "bio": bio,
        "company": "",
        "profile_url": profile.get("link") or "",
        "contact_email": "",
        "contact_url": profile.get("website_url") or profile.get("link") or "",
        "education_status": education_status(bio),
        "age_status": "待本人确认",
        "source_updated_at": _timestamp_iso(profile.get("last_access_date")),
    }
    score, role, ranked = score_candidate(candidate, evidence, requested_role, requested_city)
    candidate.update({
        "match_score": score,
        "rule_match_score": score,
        "suggested_role": role,
        "evidence": evidence,
        "_legacy_ranked_evidence": ranked,
    })
    return candidate


def search_stackoverflow(keyword: str, city: str, role: str, limit: int) -> List[Dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "site": "stackoverflow",
            "q": keyword.strip() or "LLM agent",
            "order": "desc",
            "sort": "activity",
            "pagesize": max(1, min(limit * 5, 100)),
        }
    )
    result = fetch_json(
        "https://api.stackexchange.com/2.3/search/advanced?{}".format(query)
    )
    if isinstance(result, dict) and result.get("error_message"):
        raise CollectorError(str(result["error_message"]))
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for question in (result.get("items") or []) if isinstance(result, dict) else []:
        owner = question.get("owner") or {}
        user_id = owner.get("user_id")
        if user_id:
            grouped.setdefault(str(user_id), []).append(question)
    candidates = []
    for user_id, questions in grouped.items():
        try:
            profile = stackoverflow_user(user_id)
            candidate = stackoverflow_candidate(profile, questions, role, city)
        except (NetworkUnavailable, RateLimited):
            raise
        except CollectorError:
            continue
        if _target_city_allows(candidate["city"], city):
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def analyze_public_url(url: str, role: str = "全部", city: str = "全部") -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(url.strip())
    host = parsed.netloc.lower().split(":")[0]
    parts = [part for part in parsed.path.split("/") if part]
    if host in ("github.com", "www.github.com") and parts:
        return github_user(parts[0], role, city)
    if host in ("gitee.com", "www.gitee.com") and parts:
        username = parts[0]
        if len(parts) >= 2:
            query = urllib.parse.urlencode({"q": parts[1], "from": 0, "size": 20})
            result = fetch_json(
                "https://so.gitee.com/v1/search/widget/{}?{}".format(GITEE_WIDGET, query)
            )
            for hit in ((result.get("hits") or {}).get("hits") or []):
                fields = hit.get("fields") or {}
                owners = fields.get("owner.path.keyword") or []
                paths = fields.get("path") or []
                if owners and paths and owners[0].lower() == username.lower() and paths[0].lower() == parts[1].lower():
                    return gitee_candidate_from_repo(hit, role, city)
        profile = gitee_user(username)
        candidate = {
            "source": "gitee",
            "external_id": profile.get("id") or username,
            "username": profile.get("login") or username,
            "display_name": profile.get("name") or username,
            "city": detect_city(profile.get("bio") or ""),
            "bio": profile.get("bio") or "",
            "company": profile.get("company") or "",
            "profile_url": profile.get("html_url") or url,
            "contact_email": profile.get("email") or "",
            "contact_url": profile.get("blog") or profile.get("html_url") or url,
            "education_status": education_status(profile.get("bio") or ""),
            "age_status": "待本人确认",
            "source_updated_at": profile.get("updated_at") or "",
            "evidence": [],
        }
        enrich_public_email(candidate)
        score, suggested_role, ranked = score_candidate(candidate, [], role, city)
        candidate.update({"match_score": score, "rule_match_score": score, "suggested_role": suggested_role, "evidence": ranked})
        return candidate
    if host in ("gitlab.com", "www.gitlab.com") and parts:
        username = parts[0]
        profile = gitlab_user(username)
        projects = gitlab_user_projects(profile.get("id") or username)
        return gitlab_candidate(profile, projects, role, city)
    if host in ("huggingface.co", "www.huggingface.co") and parts:
        return huggingface_user(parts[0], role, city)
    if host in ("stackoverflow.com", "www.stackoverflow.com") and len(parts) >= 2 and parts[0] == "users":
        profile = stackoverflow_user(parts[1])
        return stackoverflow_candidate(profile, [], role, city)
    raise CollectorError(
        "当前支持 GitHub、Gitee、GitLab、Hugging Face 和 Stack Overflow 的公开链接"
    )
