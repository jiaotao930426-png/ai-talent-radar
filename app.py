import json
import mimetypes
import os
import re
import signal
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True

import db
from excel_export import generate_excel
from jobs import (
    AIReanalysisInProgress,
    AI_REANALYSIS_MAX_CANDIDATES,
    JobManager,
    next_weekly_run,
    normalize_config,
)
from report import generate_report
from source_health import get_source_health


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_BODY_SIZE = 64 * 1024
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
manager = JobManager()


def server_address_from_env() -> Tuple[str, int]:
    host = os.environ.get("TALENT_RADAR_HOST", DEFAULT_HOST)
    if not host or any(character.isspace() or character == "\x00" for character in host):
        raise ValueError("TALENT_RADAR_HOST 不能为空或包含空白/控制字符")

    raw_port = os.environ.get("TALENT_RADAR_PORT")
    if raw_port is None:
        port = DEFAULT_PORT
    else:
        if not re.fullmatch(r"[0-9]+", raw_port):
            raise ValueError("TALENT_RADAR_PORT 必须是 1 到 65535 之间的整数")
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError("TALENT_RADAR_PORT 必须是 1 到 65535 之间的整数")
    return host, port


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AITalentRadar/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        try:
            self.dispatch_get()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.handle_internal_error(exc)

    def dispatch_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        if path == "/api/overview":
            payload = db.overview()
            payload["schedule"] = db.get_schedule()
            self.send_json(payload)
        elif path == "/api/candidates":
            self.send_json(db.list_candidates(query))
        elif re.fullmatch(r"/api/candidates/\d+", path):
            candidate = db.get_candidate(int(path.rsplit("/", 1)[1]))
            self.send_json(candidate or {"error": "候选人不存在"}, HTTPStatus.OK if candidate else HTTPStatus.NOT_FOUND)
        elif path == "/api/jobs":
            self.send_json({"items": db.list_jobs(int(query.get("limit") or 50))})
        elif re.fullmatch(r"/api/jobs/\d+", path):
            job = db.get_job(int(path.rsplit("/", 1)[1]))
            self.send_json(job or {"error": "任务不存在"}, HTTPStatus.OK if job else HTTPStatus.NOT_FOUND)
        elif path == "/api/schedule":
            self.send_json(db.get_schedule())
        elif path == "/api/role-templates":
            self.send_json({"items": db.list_role_templates()})
        elif re.fullmatch(r"/api/role-templates/[^/]+", path):
            identifier = path.rsplit("/", 1)[1]
            template = db.get_role_template(identifier)
            self.send_json(template or {"error": "岗位模板不存在"}, HTTPStatus.OK if template else HTTPStatus.NOT_FOUND)
        elif path == "/api/data-management":
            self.send_json(db.data_management_stats())
        elif path == "/report.html":
            content = generate_report(db.report_candidates())
            self.send_bytes(content, "text/html; charset=utf-8")
        elif path == "/export/candidates.xlsx":
            try:
                content = generate_excel(db.export_candidates(query))
                self.send_bytes(
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    content_disposition="attachment; filename*=UTF-8''AI-Talent-Candidates.xlsx",
                )
            except RuntimeError:
                self.send_json({"error": "Excel 导出生成失败，请检查本机导出运行环境"}, HTTPStatus.SERVICE_UNAVAILABLE)
        elif path == "/api/health":
            self.send_json({"status": "ok", "github_token_configured": bool(os.environ.get("GITHUB_TOKEN"))})
        elif path == "/api/source-health":
            self.send_json(get_source_health(probe=query.get("probe") == "1"))
        else:
            self.serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path == "/api/candidates/reanalyze-ai":
                raw_limit = payload.get("limit", AI_REANALYSIS_MAX_CANDIDATES)
                if isinstance(raw_limit, bool):
                    raise ValueError("AI 重分析数量必须是整数")
                try:
                    limit = int(raw_limit)
                except (TypeError, ValueError) as exc:
                    raise ValueError("AI 重分析数量必须是整数") from exc
                include_archived = payload.get("include_archived", False)
                if not isinstance(include_archived, bool):
                    raise ValueError("是否包含已归档候选人必须为布尔值")
                job_id, selected_count = manager.submit_ai_reanalysis(
                    limit,
                    include_archived=include_archived,
                )
                self.send_json(
                    {
                        "job_id": job_id,
                        "selected_count": selected_count,
                        "message": (
                            "已开始 AI 重分析"
                            if selected_count
                            else "人才池中没有需要重新分析的候选人"
                        ),
                    },
                    HTTPStatus.ACCEPTED,
                )
            elif path == "/api/jobs":
                job_id = manager.submit("手动采集", payload)
                self.send_json({"job_id": job_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/role-templates":
                self.send_json(db.create_role_template(payload), HTTPStatus.CREATED)
            elif re.fullmatch(r"/api/role-templates/[^/]+/(activate|deactivate)", path):
                parts = path.split("/")
                active = parts[-1] == "activate"
                template = db.set_role_template_active(parts[-2], active)
                if not template:
                    self.send_json({"error": "岗位模板不存在"}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(template)
            elif re.fullmatch(r"/api/jobs/\d+/cancel", path):
                job_id = int(path.split("/")[3])
                changed = db.request_job_cancel(job_id)
                if changed:
                    self.send_json({"cancel_requested": True})
                else:
                    self.send_json(
                        {"error": "任务不存在或当前无法取消"},
                        HTTPStatus.CONFLICT,
                    )
            elif re.fullmatch(r"/api/candidates/\d+/archive", path):
                candidate_id = int(path.split("/")[3])
                changed = db.archive_candidate(candidate_id)
                if changed:
                    self.send_json({"archived": True})
                else:
                    self.send_json(
                        {"error": "候选人已归档或不存在"},
                        HTTPStatus.CONFLICT,
                    )
            elif re.fullmatch(r"/api/candidates/\d+/restore", path):
                candidate_id = int(path.split("/")[3])
                changed = db.restore_candidate(candidate_id)
                if changed:
                    self.send_json({"restored": True})
                else:
                    self.send_json(
                        {"error": "候选人未归档或不存在"},
                        HTTPStatus.CONFLICT,
                    )
            elif path == "/api/data-management/backup":
                self.send_json({"backup": db.create_backup()}, HTTPStatus.CREATED)
            elif path == "/api/data-management/archive-nonmatching":
                self.send_json({"archived_count": db.archive_nonmatching_candidates()})
            elif path == "/api/data-management/cleanup-jobs":
                days = int(payload.get("days") or 90)
                self.send_json({"deleted_count": db.cleanup_job_logs(days), "days": days})
            elif path == "/api/data-management/vacuum":
                self.send_json({"reclaimed_bytes": db.vacuum_database()})
            else:
                self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except AIReanalysisInProgress as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.handle_internal_error(exc)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if not re.fullmatch(r"/api/candidates/\d+", path):
                self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
                return
            if str(payload.get("confirmation") or "") != "永久删除":
                raise ValueError("请输入“永久删除”以确认")
            candidate_id = int(path.rsplit("/", 1)[1])
            candidate = db.get_candidate(candidate_id)
            if not candidate:
                self.send_json({"error": "候选人不存在"}, HTTPStatus.NOT_FOUND)
                return
            if not candidate.get("archived_at"):
                self.send_json({"error": "只有已归档候选人可以永久删除"}, HTTPStatus.CONFLICT)
                return
            backup = db.create_backup()
            deleted = db.delete_archived_candidate(candidate_id)
            if deleted:
                self.send_json({"deleted": True, "backup": backup})
            else:
                self.send_json(
                    {"error": "候选人状态已变化，未执行删除", "backup": backup},
                    HTTPStatus.CONFLICT,
                )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.handle_internal_error(exc)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if re.fullmatch(r"/api/candidates/\d+", path):
                candidate_id = int(path.rsplit("/", 1)[1])
                changed = db.review_candidate(
                    candidate_id,
                    str(payload.get("review_status") or "待审核"),
                    str(payload.get("review_note") or ""),
                    str(payload.get("education_verification") or "待本人确认"),
                    str(payload.get("age_status") or "待本人确认"),
                    str(payload.get("work_location_status") or "待本人确认"),
                    str(payload.get("agent_experience_status") or "待人工核验"),
                    str(payload.get("contact_stage") or "未联系"),
                )
                if changed:
                    self.send_json({"updated": True})
                else:
                    self.send_json({"error": "候选人不存在"}, HTTPStatus.NOT_FOUND)
            elif re.fullmatch(r"/api/role-templates/[^/]+", path):
                identifier = path.rsplit("/", 1)[1]
                template = db.update_role_template(identifier, payload)
                if template:
                    self.send_json(template)
                else:
                    self.send_json({"error": "岗位模板不存在"}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.handle_internal_error(exc)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path != "/api/schedule":
                self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
                return
            weekday = int(payload.get("weekday", 0))
            hour = int(payload.get("hour", 10))
            minute = int(payload.get("minute", 0))
            if weekday not in range(7) or hour not in range(24) or minute not in range(60):
                raise ValueError("定时配置超出有效范围")
            if not isinstance(payload.get("enabled"), bool):
                raise ValueError("启用状态必须为布尔值")
            raw_config = payload.get("config")
            if not isinstance(raw_config, dict):
                raise ValueError("定时任务配置格式无效")
            normalized_config = normalize_config(
                {
                    "mode": "search",
                    "target": raw_config.get("target") or 30,
                    "roles": raw_config.get("roles"),
                    "cities": raw_config.get("cities"),
                    "sources": raw_config.get("sources"),
                    "keywords": raw_config.get("keywords"),
                    "prefer_contactable": raw_config.get("prefer_contactable", True),
                    "use_local_ai": raw_config.get(
                        "use_local_ai", raw_config.get("enable_ai", False)
                    ),
                },
                # Keep the historical schedule payload compatibility: an old
                # saved config with a retired role falls back to the first
                # active template. New manual jobs reject unknown roles.
                strict_roles=False,
            )
            enabled = payload["enabled"]
            schedule = {
                "enabled": enabled,
                "weekday": weekday,
                "hour": hour,
                "minute": minute,
                "config": {
                    "roles": normalized_config["roles"],
                    "cities": normalized_config["cities"],
                    "sources": normalized_config["sources"],
                    "target": normalized_config["target"],
                    "keywords": normalized_config["keywords"],
                    "prefer_contactable": normalized_config["prefer_contactable"],
                    "use_local_ai": normalized_config["use_local_ai"],
                    "enable_ai": normalized_config["use_local_ai"],
                    "role_template_snapshots": normalized_config.get("role_template_snapshots", []),
                },
            }
            next_run = (
                next_weekly_run(weekday, hour, minute).isoformat(timespec="seconds") if enabled else None
            )
            self.send_json(db.save_schedule(schedule, next_run))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.handle_internal_error(exc)

    def read_json(self) -> Dict[str, Any]:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("请求必须使用 application/json")
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin:
            port = int(getattr(self.server, "server_port", 8765))
            allowed_origins = {
                "http://127.0.0.1:{}".format(port),
                "http://localhost:{}".format(port),
            }
            if origin not in allowed_origins:
                raise ValueError("请求来源无效")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length <= 0 or length > MAX_BODY_SIZE:
            raise ValueError("请求内容为空或过大")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("请求不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求格式无效")
        return payload

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        if relative.startswith("static/"):
            relative = relative[len("static/") :]
        target = (STATIC_DIR / relative).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_json({"error": "路径无效"}, HTTPStatus.BAD_REQUEST)
            return
        if not target.is_file():
            self.send_json({"error": "页面不存在"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_bytes(target.read_bytes(), content_type)

    def handle_internal_error(self, _exc: Exception) -> None:
        traceback.print_exc(file=sys.stderr)
        self.send_json(
            {"error": "本地服务发生错误，请稍后重试并查看任务日志"},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def send_bytes(
        self,
        content: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        content_disposition: Optional[str] = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_disposition:
            self.send_header("Content-Disposition", content_disposition)
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; "
                "frame-ancestors 'none'",
            )
        self.end_headers()
        self.wfile.write(content)


def run() -> None:
    host, port = server_address_from_env()
    db.init_db()
    manager.start()
    server = ThreadingHTTPServer((host, port), AppHandler)

    def shutdown(_signum: int, _frame: Any) -> None:
        manager.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print("AI Talent Radar running at http://{}:{}".format(host, port), flush=True)
    try:
        server.serve_forever()
    finally:
        manager.stop()
        server.server_close()


if __name__ == "__main__":
    run()
