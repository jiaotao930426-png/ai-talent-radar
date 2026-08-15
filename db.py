import json
import os
import re
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from contactability import MATCH_HIGH_SCORE, MATCH_MEDIUM_SCORE, derive_contact_level


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "talent_radar.db"

REVIEW_STATUSES = {"待审核", "优先联系", "需要核验", "人才储备", "不符合"}
EDUCATION_VERIFICATIONS = {"待本人确认", "本科及以上", "不符合"}
AGE_STATUSES = {"待本人确认", "30岁以下", "不符合"}
WORK_LOCATION_STATUSES = {"待本人确认", "接受北京", "接受重庆", "接受北京/重庆", "不接受"}
AGENT_EXPERIENCE_STATUSES = {
    "待人工核验",
    "原创 Agent 项目",
    "参与 Agent 项目",
    "仅关键词命中",
    "无相关经验",
}
CONTACT_STAGES = {"未联系", "已联系", "已回复", "进入面试", "已发 Offer", "已录用", "不再推进"}
PUBLIC_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_public_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def safe_public_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if (
        len(email) > 320
        or any(character in email for character in "\r\n\x00")
        or not PUBLIC_EMAIL_PATTERN.fullmatch(email)
        or "noreply" in email
    ):
        return ""
    return email


def candidate_order_sql(alias: str = "") -> str:
    prefix = "{}.".format(alias) if alias else ""
    return """
        CASE
            WHEN {p}match_score >= {high} THEN 2
            WHEN {p}match_score >= {medium} THEN 1
            ELSE 0 END DESC,
        CASE {p}contact_level
            WHEN 'A' THEN 4 WHEN 'B' THEN 3 WHEN 'C' THEN 2 ELSE 1 END DESC,
        {p}match_score DESC, {p}id DESC
    """.format(p=prefix, high=MATCH_HIGH_SCORE, medium=MATCH_MEDIUM_SCORE)


def db_path() -> Path:
    configured = os.environ.get("TALENT_RADAR_DB")
    return Path(configured).expanduser() if configured else DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        external_id TEXT NOT NULL,
        username TEXT NOT NULL,
        display_name TEXT NOT NULL,
        city TEXT NOT NULL DEFAULT '待核验',
        bio TEXT NOT NULL DEFAULT '',
        company TEXT NOT NULL DEFAULT '',
        profile_url TEXT NOT NULL,
        contact_email TEXT NOT NULL DEFAULT '',
        contact_email_source_url TEXT NOT NULL DEFAULT '',
        contact_email_verified_at TEXT,
        contact_level TEXT NOT NULL DEFAULT 'D',
        contact_url TEXT NOT NULL DEFAULT '',
        suggested_role TEXT NOT NULL DEFAULT 'AI Agent 工程师',
        match_score INTEGER NOT NULL DEFAULT 0,
        education_status TEXT NOT NULL DEFAULT '待核验',
        education_verification TEXT NOT NULL DEFAULT '待本人确认',
        age_status TEXT NOT NULL DEFAULT '待本人确认',
        work_location_status TEXT NOT NULL DEFAULT '待本人确认',
        agent_experience_status TEXT NOT NULL DEFAULT '待人工核验',
        contact_stage TEXT NOT NULL DEFAULT '未联系',
        contact_updated_at TEXT,
        review_status TEXT NOT NULL DEFAULT '待审核',
        review_note TEXT NOT NULL DEFAULT '',
        archived_at TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        source_updated_at TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source, external_id)
    );

    CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        stars INTEGER NOT NULL DEFAULT 0,
        language TEXT NOT NULL DEFAULT '',
        is_fork INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(candidate_id, url)
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '等待执行',
        progress INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        config_json TEXT NOT NULL,
        result_count INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS job_candidates (
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
        PRIMARY KEY(job_id, candidate_id)
    );

    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        enabled INTEGER NOT NULL DEFAULT 0,
        weekday INTEGER NOT NULL DEFAULT 0,
        hour INTEGER NOT NULL DEFAULT 10,
        minute INTEGER NOT NULL DEFAULT 0,
        config_json TEXT NOT NULL,
        last_run_at TEXT,
        next_run_at TEXT,
        retry_at TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_candidates_review ON candidates(review_status);
    CREATE INDEX IF NOT EXISTS idx_candidates_city ON candidates(city);
    CREATE INDEX IF NOT EXISTS idx_candidates_role ON candidates(suggested_role);
    CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
    """
    default_config = {
        "roles": ["AI Agent 工程师", "AI Coding 工程师", "AI 产品经理"],
        "cities": ["北京", "重庆"],
        "sources": ["github", "gitee"],
        "target": 30,
        "keywords": "",
        "prefer_contactable": True,
    }
    with connect() as connection:
        connection.executescript(schema)
        candidate_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(candidates)").fetchall()
        }
        migrations = {
            "education_verification": "TEXT NOT NULL DEFAULT '待本人确认'",
            "work_location_status": "TEXT NOT NULL DEFAULT '待本人确认'",
            "agent_experience_status": "TEXT NOT NULL DEFAULT '待人工核验'",
            "contact_stage": "TEXT NOT NULL DEFAULT '未联系'",
            "contact_updated_at": "TEXT",
            "archived_at": "TEXT",
            "contact_email_source_url": "TEXT NOT NULL DEFAULT ''",
            "contact_email_verified_at": "TEXT",
            "contact_level": "TEXT NOT NULL DEFAULT 'D'",
        }
        for column, definition in migrations.items():
            if column not in candidate_columns:
                connection.execute(
                    "ALTER TABLE candidates ADD COLUMN {} {}".format(column, definition)
                )
        schedule_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(schedules)").fetchall()
        }
        schedule_migrations = {
            "retry_at": "TEXT",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in schedule_migrations.items():
            if column not in schedule_columns:
                connection.execute(
                    "ALTER TABLE schedules ADD COLUMN {} {}".format(column, definition)
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidates_archived ON candidates(archived_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidates_contact_level ON candidates(contact_level)"
        )
        contact_rows = connection.execute(
            """
            SELECT id, profile_url, contact_url, contact_email,
                   contact_email_source_url, contact_email_verified_at,
                   contact_level
            FROM candidates
            """
        ).fetchall()
        contact_updates = []
        for row in contact_rows:
            level = derive_contact_level(dict(row))
            if level != row["contact_level"]:
                contact_updates.append((level, int(row["id"])))
        if contact_updates:
            connection.executemany(
                "UPDATE candidates SET contact_level = ? WHERE id = ?",
                contact_updates,
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO schedules
                (id, enabled, weekday, hour, minute, config_json, updated_at)
            VALUES (1, 0, 0, 10, 0, ?, ?)
            """,
            (json.dumps(default_config, ensure_ascii=False), now_iso()),
        )
        connection.execute(
            """
            UPDATE jobs
            SET status = '执行中断', error = '服务重启导致任务中断', completed_at = ?
            WHERE status IN ('等待执行', '正在采集', '正在分析', '请求取消')
            """,
            (now_iso(),),
        )


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def create_job(kind: str, config: Dict[str, Any]) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO jobs (kind, config_json, created_at)
            VALUES (?, ?, ?)
            """,
            (kind, json.dumps(config, ensure_ascii=False), now_iso()),
        )
        return int(cursor.lastrowid)


def update_job(job_id: int, **fields: Any) -> None:
    allowed = {
        "status",
        "progress",
        "message",
        "result_count",
        "error",
        "started_at",
        "completed_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = ", ".join("{} = ?".format(key) for key in values)
    params = list(values.values()) + [job_id]
    with connect() as connection:
        connection.execute("UPDATE jobs SET {} WHERE id = ?".format(assignments), params)


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        job = row_to_dict(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        if not job:
            return None
        job["config"] = json.loads(job.pop("config_json"))
        job["candidates"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.id, c.display_name, c.username, c.city, c.suggested_role,
                       c.match_score, c.profile_url, c.review_status, c.contact_level
                FROM job_candidates jc
                JOIN candidates c ON c.id = jc.candidate_id
                WHERE jc.job_id = ?
                ORDER BY {}
                """.format(candidate_order_sql("c")),
                (job_id,),
            ).fetchall()
        ]
        return job


def list_jobs(limit: int = 30) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    jobs = []
    for row in rows:
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json"))
        jobs.append(item)
    return jobs


def job_cancel_requested(job_id: int) -> bool:
    with connect() as connection:
        row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["status"] == "请求取消")


def request_job_cancel(job_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE jobs SET status = '请求取消', message = '将在当前步骤结束后取消'
            WHERE id = ? AND status IN ('等待执行', '正在采集', '正在分析')
            """,
            (job_id,),
        )
        return cursor.rowcount > 0


def merge_existing_verified_contacts(candidates: Iterable[Dict[str, Any]]) -> None:
    with connect() as connection:
        for candidate in candidates:
            if derive_contact_level(candidate) == "A":
                continue
            source = str(candidate.get("source") or "")
            external_id = str(candidate.get("external_id") or "")
            if not source or not external_id:
                continue
            existing = connection.execute(
                """
                SELECT profile_url, contact_url, contact_email,
                       contact_email_source_url, contact_email_verified_at
                FROM candidates WHERE source = ? AND external_id = ?
                """,
                (source, external_id),
            ).fetchone()
            if not existing or derive_contact_level(dict(existing)) != "A":
                continue
            candidate["contact_email"] = existing["contact_email"]
            candidate["contact_email_source_url"] = existing[
                "contact_email_source_url"
            ]
            candidate["contact_email_verified_at"] = existing[
                "contact_email_verified_at"
            ]


def upsert_candidate(candidate: Dict[str, Any], job_id: Optional[int] = None) -> Tuple[int, bool]:
    timestamp = now_iso()
    profile_url = safe_public_url(candidate.get("profile_url"))
    if not profile_url:
        raise ValueError("候选人的公开主页链接无效")
    contact_url = safe_public_url(candidate.get("contact_url")) or profile_url
    fields = {
        "source": candidate["source"],
        "external_id": str(candidate["external_id"]),
        "username": candidate.get("username") or candidate.get("display_name") or "未知",
        "display_name": candidate.get("display_name") or candidate.get("username") or "未知",
        "city": candidate.get("city") or "待核验",
        "bio": candidate.get("bio") or "",
        "company": candidate.get("company") or "",
        "profile_url": profile_url,
        "contact_email": safe_public_email(candidate.get("contact_email")),
        "contact_email_source_url": safe_public_url(candidate.get("contact_email_source_url")),
        "contact_email_verified_at": candidate.get("contact_email_verified_at") or None,
        "contact_url": contact_url,
        "suggested_role": candidate.get("suggested_role") or "AI Agent 工程师",
        "match_score": int(candidate.get("match_score") or 0),
        "education_status": candidate.get("education_status") or "待核验",
        "age_status": candidate.get("age_status") or "待本人确认",
        "source_updated_at": candidate.get("source_updated_at") or "",
    }
    with connect() as connection:
        existing = connection.execute(
            """
            SELECT id, contact_email, contact_email_source_url, contact_email_verified_at
            FROM candidates WHERE source = ? AND external_id = ?
            """,
            (fields["source"], fields["external_id"]),
        ).fetchone()
        inserted = existing is None
        if existing is not None:
            existing_verified = bool(
                existing["contact_email"]
                and existing["contact_email_source_url"]
                and existing["contact_email_verified_at"]
            )
            incoming_verified = bool(
                fields["contact_email"]
                and fields["contact_email_source_url"]
                and fields["contact_email_verified_at"]
            )
            if existing_verified and not incoming_verified:
                fields["contact_email"] = existing["contact_email"] or ""
                fields["contact_email_source_url"] = existing["contact_email_source_url"] or ""
                fields["contact_email_verified_at"] = existing["contact_email_verified_at"]
            elif not fields["contact_email"]:
                fields["contact_email"] = existing["contact_email"] or ""
                fields["contact_email_source_url"] = existing["contact_email_source_url"] or ""
                fields["contact_email_verified_at"] = existing["contact_email_verified_at"]
        fields["contact_level"] = derive_contact_level(fields)
        if inserted:
            cursor = connection.execute(
                """
                INSERT INTO candidates (
                    source, external_id, username, display_name, city, bio, company,
                    profile_url, contact_email, contact_email_source_url,
                    contact_email_verified_at, contact_level, contact_url,
                    suggested_role, match_score,
                    education_status, age_status, first_seen_at, last_seen_at,
                    source_updated_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["source"], fields["external_id"], fields["username"],
                    fields["display_name"], fields["city"], fields["bio"], fields["company"],
                    fields["profile_url"], fields["contact_email"],
                    fields["contact_email_source_url"], fields["contact_email_verified_at"],
                    fields["contact_level"], fields["contact_url"],
                    fields["suggested_role"], fields["match_score"], fields["education_status"],
                    fields["age_status"], timestamp, timestamp, fields["source_updated_at"],
                    timestamp, timestamp,
                ),
            )
            candidate_id = int(cursor.lastrowid)
        else:
            candidate_id = int(existing["id"])
            connection.execute(
                """
                UPDATE candidates SET
                    username = ?, display_name = ?, city = ?, bio = ?, company = ?,
                    profile_url = ?, contact_email = ?, contact_email_source_url = ?,
                    contact_email_verified_at = ?, contact_level = ?, contact_url = ?,
                    suggested_role = ?, match_score = ?, education_status = ?, last_seen_at = ?,
                    source_updated_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    fields["username"], fields["display_name"], fields["city"], fields["bio"],
                    fields["company"], fields["profile_url"], fields["contact_email"],
                    fields["contact_email_source_url"], fields["contact_email_verified_at"],
                    fields["contact_level"], fields["contact_url"],
                    fields["suggested_role"], fields["match_score"],
                    fields["education_status"], timestamp, fields["source_updated_at"],
                    timestamp, candidate_id,
                ),
            )

        for evidence in candidate.get("evidence", []):
            evidence_url = safe_public_url(evidence.get("url"))
            if not evidence_url:
                continue
            connection.execute(
                """
                INSERT INTO evidence
                    (candidate_id, title, url, description, stars, language, is_fork, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id, url) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    stars = excluded.stars,
                    language = excluded.language,
                    is_fork = excluded.is_fork
                """,
                (
                    candidate_id,
                    evidence.get("title") or "公开项目",
                    evidence_url,
                    evidence.get("description") or "",
                    int(evidence.get("stars") or 0),
                    evidence.get("language") or "",
                    1 if evidence.get("is_fork") else 0,
                    timestamp,
                ),
            )
        if job_id is not None:
            connection.execute(
                "INSERT OR IGNORE INTO job_candidates(job_id, candidate_id) VALUES (?, ?)",
                (job_id, candidate_id),
            )
        return candidate_id, inserted


def set_public_email(candidate_id: int, email: str, source_url: str) -> bool:
    safe_email = safe_public_email(email)
    safe_source_url = safe_public_url(source_url)
    if not safe_email or not safe_source_url:
        raise ValueError("公开邮箱或来源链接无效")
    timestamp = now_iso()
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE candidates
            SET contact_email = ?, contact_email_source_url = ?,
                contact_email_verified_at = ?, contact_level = 'A', updated_at = ?
            WHERE id = ?
            """,
            (safe_email, safe_source_url, timestamp, timestamp, int(candidate_id)),
        )
        return cursor.rowcount > 0


def list_candidates(filters: Dict[str, Any]) -> Dict[str, Any]:
    clauses = []
    params: List[Any] = []
    archived = str(filters.get("archived") or "active").strip()
    if archived == "only":
        clauses.append("archived_at IS NOT NULL")
    elif archived != "all":
        clauses.append("archived_at IS NULL")
    for key, column in (
        ("status", "review_status"),
        ("source", "source"),
        ("city", "city"),
        ("role", "suggested_role"),
        ("contact_stage", "contact_stage"),
    ):
        value = str(filters.get(key) or "").strip()
        if value and value != "全部":
            clauses.append("{} = ?".format(column))
            params.append(value)
    search = str(filters.get("search") or "").strip()
    if search:
        clauses.append("(display_name LIKE ? OR username LIKE ? OR bio LIKE ? OR company LIKE ?)")
        pattern = "%{}%".format(search)
        params.extend([pattern, pattern, pattern, pattern])
    contactability = str(filters.get("contactability") or "all").strip()
    contactability_clauses = {
        "all": "",
        "全部": "",
        "email": "contact_level IN ('A', 'B')",
        "contactable": "contact_level IN ('A', 'B', 'C')",
        "profile_only": "contact_level = 'D'",
        "A": "contact_level = 'A'",
        "B": "contact_level = 'B'",
        "C": "contact_level = 'C'",
        "D": "contact_level = 'D'",
    }
    if contactability not in contactability_clauses:
        raise ValueError("无效的联系方式筛选")
    if contactability_clauses[contactability]:
        clauses.append(contactability_clauses[contactability])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    limit = max(1, min(int(filters.get("limit") or 100), 300))
    offset = max(0, int(filters.get("offset") or 0))
    with connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS count FROM candidates" + where, params
        ).fetchone()["count"]
        rows = connection.execute(
            """
            SELECT * FROM candidates{}
            ORDER BY {} LIMIT ? OFFSET ?
            """.format(where, candidate_order_sql()),
            params + [limit, offset],
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


def get_candidate(candidate_id: int) -> Optional[Dict[str, Any]]:
    with connect() as connection:
        candidate = row_to_dict(
            connection.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        )
        if not candidate:
            return None
        candidate["evidence"] = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM evidence WHERE candidate_id = ? ORDER BY is_fork, stars DESC, id",
                (candidate_id,),
            ).fetchall()
        ]
        return candidate


def review_candidate(
    candidate_id: int,
    status: str,
    note: str,
    education_verification: str = "待本人确认",
    age_status: str = "待本人确认",
    work_location_status: str = "待本人确认",
    agent_experience_status: str = "待人工核验",
    contact_stage: str = "未联系",
) -> bool:
    if status not in REVIEW_STATUSES:
        raise ValueError("无效的审核状态")
    if education_verification not in EDUCATION_VERIFICATIONS:
        raise ValueError("无效的学历核验状态")
    if age_status not in AGE_STATUSES:
        raise ValueError("无效的年龄核验状态")
    if work_location_status not in WORK_LOCATION_STATUSES:
        raise ValueError("无效的工作地点核验状态")
    if agent_experience_status not in AGENT_EXPERIENCE_STATUSES:
        raise ValueError("无效的 Agent 项目核验状态")
    if contact_stage not in CONTACT_STAGES:
        raise ValueError("无效的联系进度")
    timestamp = now_iso()
    with connect() as connection:
        current = connection.execute(
            "SELECT contact_stage, contact_updated_at FROM candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if not current:
            return False
        contact_updated_at = current["contact_updated_at"]
        if contact_stage != current["contact_stage"]:
            contact_updated_at = timestamp
        cursor = connection.execute(
            """
            UPDATE candidates SET review_status = ?, review_note = ?,
                education_verification = ?, age_status = ?, work_location_status = ?,
                agent_experience_status = ?, contact_stage = ?, contact_updated_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                note[:2000],
                education_verification,
                age_status,
                work_location_status,
                agent_experience_status,
                contact_stage,
                contact_updated_at,
                timestamp,
                candidate_id,
            ),
        )
        return cursor.rowcount > 0


def archive_candidate(candidate_id: int) -> bool:
    timestamp = now_iso()
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE candidates SET archived_at = ?, updated_at = ?
            WHERE id = ? AND archived_at IS NULL
            """,
            (timestamp, timestamp, candidate_id),
        )
        return cursor.rowcount > 0


def restore_candidate(candidate_id: int) -> bool:
    timestamp = now_iso()
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE candidates SET archived_at = NULL, updated_at = ?
            WHERE id = ? AND archived_at IS NOT NULL
            """,
            (timestamp, candidate_id),
        )
        return cursor.rowcount > 0


def archive_nonmatching_candidates() -> int:
    timestamp = now_iso()
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE candidates SET archived_at = ?, updated_at = ?
            WHERE review_status = '不符合' AND archived_at IS NULL
            """,
            (timestamp, timestamp),
        )
        return int(cursor.rowcount)


def delete_archived_candidate(candidate_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM candidates WHERE id = ? AND archived_at IS NOT NULL",
            (candidate_id,),
        )
        return cursor.rowcount > 0


def create_backup() -> Dict[str, Any]:
    source_path = db_path()
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone()
    filename = "talent-radar-{}.db".format(timestamp.strftime("%Y%m%d-%H%M%S-%f"))
    output_path = backup_dir / filename
    temporary_path = backup_dir / (filename + ".tmp")
    try:
        with connect() as source:
            target = sqlite3.connect(str(temporary_path))
            try:
                source.backup(target)
            finally:
                target.close()
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(output_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return {
        "filename": filename,
        "created_at": timestamp.isoformat(timespec="seconds"),
        "size_bytes": output_path.stat().st_size,
    }


def cleanup_job_logs(days: int = 90) -> int:
    days = max(30, min(int(days), 3650))
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    with connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM jobs
            WHERE created_at < ?
              AND status NOT IN ('等待执行', '正在采集', '正在分析', '请求取消')
            """,
            (cutoff,),
        )
        return int(cursor.rowcount)


def vacuum_database() -> int:
    path = db_path()
    before = path.stat().st_size if path.exists() else 0
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
    finally:
        connection.close()
    after = path.stat().st_size if path.exists() else 0
    return max(0, before - after)


def data_management_stats() -> Dict[str, Any]:
    path = db_path()
    with connect() as connection:
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END) AS active_candidates,
                SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) AS archived_candidates
            FROM candidates
            """
        ).fetchone()
        evidence_count = connection.execute(
            "SELECT COUNT(*) AS count FROM evidence"
        ).fetchone()["count"]
        job_count = connection.execute(
            "SELECT COUNT(*) AS count FROM jobs"
        ).fetchone()["count"]
    database_bytes = sum(
        item.stat().st_size
        for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        if item.exists()
    )
    log_bytes = sum(
        item.stat().st_size
        for item in (path.parent / "service.log", path.parent / "service-error.log")
        if item.exists()
    )
    backup_dir = path.parent / "backups"
    backups = sorted(backup_dir.glob("talent-radar-*.db"), key=lambda item: item.stat().st_mtime)
    latest_backup = None
    if backups:
        latest = backups[-1]
        latest_backup = {
            "filename": latest.name,
            "size_bytes": latest.stat().st_size,
            "created_at": datetime.fromtimestamp(
                latest.stat().st_mtime
            ).astimezone().isoformat(timespec="seconds"),
        }
    return {
        "active_candidates": int(counts["active_candidates"] or 0),
        "archived_candidates": int(counts["archived_candidates"] or 0),
        "evidence_count": int(evidence_count),
        "job_count": int(job_count),
        "database_bytes": database_bytes,
        "log_bytes": log_bytes,
        "backup_count": len(backups),
        "latest_backup": latest_backup,
    }


def overview() -> Dict[str, Any]:
    with connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) AS count FROM candidates WHERE archived_at IS NULL"
        ).fetchone()["count"]
        contactable = connection.execute(
            "SELECT COUNT(*) AS count FROM candidates WHERE archived_at IS NULL AND contact_email <> ''"
        ).fetchone()["count"]
        direct_contactable = connection.execute(
            """
            SELECT COUNT(*) AS count FROM candidates
            WHERE archived_at IS NULL AND contact_level IN ('A', 'B', 'C')
            """
        ).fetchone()["count"]
        pending = connection.execute(
            "SELECT COUNT(*) AS count FROM candidates WHERE archived_at IS NULL AND review_status = '待审核'"
        ).fetchone()["count"]
        priority = connection.execute(
            "SELECT COUNT(*) AS count FROM candidates WHERE archived_at IS NULL AND review_status = '优先联系'"
        ).fetchone()["count"]
        city_rows = connection.execute(
            "SELECT city, COUNT(*) AS count FROM candidates WHERE archived_at IS NULL GROUP BY city ORDER BY count DESC"
        ).fetchall()
        role_rows = connection.execute(
            "SELECT suggested_role, COUNT(*) AS count FROM candidates WHERE archived_at IS NULL GROUP BY suggested_role ORDER BY count DESC"
        ).fetchall()
    return {
        "total": total,
        "contactable": contactable,
        "direct_contactable": direct_contactable,
        "contact_coverage": round((direct_contactable / total) * 100) if total else 0,
        "pending": pending,
        "priority": priority,
        "cities": [dict(row) for row in city_rows],
        "roles": [dict(row) for row in role_rows],
        "recent_jobs": list_jobs(6),
    }


def get_schedule() -> Dict[str, Any]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM schedules WHERE id = 1").fetchone()
    schedule = dict(row)
    schedule["enabled"] = bool(schedule["enabled"])
    schedule["config"] = json.loads(schedule.pop("config_json"))
    schedule["config"].setdefault("prefer_contactable", True)
    return schedule


def save_schedule(schedule: Dict[str, Any], next_run_at: Optional[str]) -> Dict[str, Any]:
    with connect() as connection:
        connection.execute(
            """
            UPDATE schedules SET enabled = ?, weekday = ?, hour = ?, minute = ?,
                config_json = ?, next_run_at = ?, retry_at = NULL, retry_count = 0,
                updated_at = ? WHERE id = 1
            """,
            (
                1 if schedule.get("enabled") else 0,
                int(schedule["weekday"]),
                int(schedule["hour"]),
                int(schedule["minute"]),
                json.dumps(schedule["config"], ensure_ascii=False),
                next_run_at,
                now_iso(),
            ),
        )
    return get_schedule()


def claim_due_schedule(next_run_at: str) -> Optional[Dict[str, Any]]:
    timestamp = now_iso()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM schedules WHERE id = 1").fetchone()
        if not row or not row["enabled"]:
            return None
        retry_due = bool(row["retry_at"] and row["retry_at"] <= timestamp)
        weekly_due = bool(row["next_run_at"] and row["next_run_at"] <= timestamp)
        if not retry_due and not weekly_due:
            return None
        if retry_due:
            connection.execute(
                "UPDATE schedules SET last_run_at = ?, retry_at = NULL, updated_at = ? WHERE id = 1",
                (timestamp, timestamp),
            )
        else:
            connection.execute(
                """
                UPDATE schedules SET last_run_at = ?, next_run_at = ?, retry_at = NULL,
                    retry_count = 0, updated_at = ? WHERE id = 1
                """,
                (timestamp, next_run_at, timestamp),
            )
        config = json.loads(row["config_json"])
        config.setdefault("prefer_contactable", True)
        return config


def defer_schedule_retry(delay_minutes: int = 15, max_retries: int = 3) -> Optional[str]:
    timestamp = datetime.now().astimezone()
    retry_at = (timestamp + timedelta(minutes=delay_minutes)).isoformat(timespec="seconds")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT enabled, retry_count FROM schedules WHERE id = 1"
        ).fetchone()
        if not row or not row["enabled"] or int(row["retry_count"] or 0) >= max_retries:
            return None
        connection.execute(
            """
            UPDATE schedules SET retry_at = ?, retry_count = retry_count + 1,
                updated_at = ? WHERE id = 1
            """,
            (retry_at, now_iso()),
        )
    return retry_at


def clear_schedule_retry() -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE schedules SET retry_at = NULL, retry_count = 0, updated_at = ? WHERE id = 1",
            (now_iso(),),
        )


def report_candidates() -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM candidates
            WHERE archived_at IS NULL AND review_status <> '不符合'
            ORDER BY CASE review_status
                WHEN '优先联系' THEN 0
                WHEN '需要核验' THEN 1
                WHEN '待审核' THEN 2
                ELSE 3 END,
                {}
            """.format(candidate_order_sql())
        ).fetchall()
    return [dict(row) for row in rows]


def export_candidates() -> List[Dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM candidates
            WHERE archived_at IS NULL
            ORDER BY {}
            """.format(candidate_order_sql())
        ).fetchall()
        candidates = [dict(row) for row in rows]
        evidence_rows = connection.execute(
            """
            SELECT e.*, c.display_name AS candidate_name
            FROM evidence e
            JOIN candidates c ON c.id = e.candidate_id
            WHERE c.archived_at IS NULL
            ORDER BY e.candidate_id, e.is_fork, e.stars DESC, e.id
            """
        ).fetchall()
    evidence_by_candidate: Dict[int, List[Dict[str, Any]]] = {}
    for row in evidence_rows:
        item = dict(row)
        evidence_by_candidate.setdefault(int(item["candidate_id"]), []).append(item)
    for candidate in candidates:
        candidate["evidence"] = evidence_by_candidate.get(int(candidate["id"]), [])
    return candidates
