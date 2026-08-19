import os
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import db
import app
import jobs
import ollama_matcher
from http.server import ThreadingHTTPServer


class AIReanalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db = os.environ.get("TALENT_RADAR_DB")
        os.environ["TALENT_RADAR_DB"] = str(Path(self.temp_dir.name) / "reanalysis.db")
        db.init_db()

    def tearDown(self) -> None:
        if self.previous_db is None:
            os.environ.pop("TALENT_RADAR_DB", None)
        else:
            os.environ["TALENT_RADAR_DB"] = self.previous_db
        self.temp_dir.cleanup()

    @staticmethod
    def candidate(external_id: str, status: str = "未启用"):
        return {
            "source": "github",
            "external_id": external_id,
            "username": external_id,
            "display_name": "Synthetic {}".format(external_id),
            "city": "北京",
            "bio": "AI agent engineer",
            "company": "Synthetic Company",
            "profile_url": "https://github.com/{}".format(external_id),
            "contact_url": "https://github.com/{}".format(external_id),
            "contact_email": "{}@example.test".format(external_id),
            "suggested_role": "AI Agent 工程师",
            "match_score": 70,
            "ai_match_status": status,
            "evidence": [
                {
                    "title": "agent-project",
                    "url": "https://github.com/{}/agent-project".format(external_id),
                    "description": "MCP agent tools",
                    "language": "Python",
                }
            ],
        }

    def test_selection_excludes_completed_and_archived_by_default(self) -> None:
        pending_id, _ = db.upsert_candidate(self.candidate("pending"))
        completed_id, _ = db.upsert_candidate(
            self.candidate("completed", status="已完成")
        )
        archived_id, _ = db.upsert_candidate(self.candidate("archived"))
        db.archive_candidate(archived_id)

        selected = db.list_candidates_needing_ai()
        self.assertEqual([item["id"] for item in selected], [pending_id])
        selected_with_archived = db.list_candidates_needing_ai(include_archived=True)
        self.assertEqual(
            {item["id"] for item in selected_with_archived},
            {pending_id, archived_id},
        )
        self.assertNotIn(completed_id, {item["id"] for item in selected_with_archived})

    def test_worker_updates_ai_fields_and_continues_after_one_failure(self) -> None:
        first_id, _ = db.upsert_candidate(self.candidate("first"))
        second_id, _ = db.upsert_candidate(self.candidate("second"))
        db.review_candidate(
            second_id,
            "优先联系",
            "人工保留",
            "本科及以上",
            "30岁以下",
            "接受北京",
            "原创 Agent 项目",
            "已联系",
        )
        job_id = db.create_job(
            jobs.AI_REANALYSIS_KIND,
            {"mode": "ai_reanalysis", "target": 2, "use_local_ai": True},
        )
        calls = []

        def fake_match(candidate, template):
            # The worker passes a reduced public shape; contact and identity
            # fields must never cross the model boundary.
            calls.append(candidate)
            self.assertNotIn("contact_email", candidate)
            self.assertNotIn("display_name", candidate)
            self.assertNotIn("company", candidate)
            self.assertNotIn("city", candidate)
            if candidate["evidence"][0]["url"].endswith("second/agent-project"):
                raise ollama_matcher.OllamaUnavailable("offline")
            return {
                "match_score": 90,
                "confidence": 0.8,
                "summary": "项目证据充分",
                "evidence": [
                    {
                        "title": "agent-project",
                        "url": candidate["evidence"][0]["url"],
                        "reason": "agent tools",
                    }
                ],
                "model": "qwen3:4b",
                "matched_at": "2026-08-17T10:00:00+08:00",
            }

        with patch("jobs.match_candidate", side_effect=fake_match):
            jobs.JobManager()._run_ai_reanalysis(job_id, [first_id, second_id])

        first = db.get_candidate(first_id)
        second = db.get_candidate(second_id)
        self.assertEqual(first["ai_match_status"], "已完成")
        self.assertEqual(first["ai_match_score"], 90)
        self.assertEqual(first["match_score"], 76)
        self.assertEqual(first["ai_match_model"], "qwen3:4b")
        self.assertEqual(first["contact_email"], "first@example.test")
        self.assertEqual(second["ai_match_status"], "不可用，已回退规则")
        self.assertIsNone(second["ai_match_score"])
        self.assertEqual(second["review_status"], "优先联系")
        self.assertEqual(second["contact_stage"], "已联系")
        self.assertEqual(len(calls), 2)
        task = db.get_job(job_id)
        self.assertEqual(task["status"], "部分完成")
        self.assertEqual(task["result_count"], 2)
        self.assertEqual(task["ai_completed_count"], 1)
        self.assertEqual(task["ai_fallback_count"], 1)

    def test_submit_without_pending_candidates_creates_visible_completed_job(self) -> None:
        db.upsert_candidate(self.candidate("done", status="已完成"))
        manager = jobs.JobManager()
        job_id, selected_count = manager.submit_ai_reanalysis()
        self.assertEqual(selected_count, 0)
        task = db.get_job(job_id)
        self.assertEqual(task["kind"], jobs.AI_REANALYSIS_KIND)
        self.assertEqual(task["status"], "已完成")
        self.assertIn("没有需要", task["message"])

    def test_worker_infrastructure_error_is_terminal(self) -> None:
        job_id = db.create_job(
            jobs.AI_REANALYSIS_KIND,
            {"mode": "ai_reanalysis", "target": 1, "use_local_ai": True},
        )
        manager = jobs.JobManager()
        with patch.object(
            manager,
            "_run_ai_reanalysis_impl",
            side_effect=RuntimeError("synthetic infrastructure failure"),
        ):
            manager._run_ai_reanalysis(job_id, [999])
        task = db.get_job(job_id)
        self.assertEqual(task["status"], "执行失败")
        self.assertEqual(task["progress"], 100)
        self.assertIn("AI 重分析任务异常", task["error"])

    def test_http_endpoint_returns_background_job_contract(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        with patch.object(
            app.manager,
            "submit_ai_reanalysis",
            return_value=(41, 3),
        ) as submit:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            body = json.dumps(
                {"limit": 20, "include_archived": False}, ensure_ascii=False
            ).encode("utf-8")
            connection.request(
                "POST",
                "/api/candidates/reanalyze-ai",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertEqual(response.status, 202)
        self.assertEqual(payload["job_id"], 41)
        self.assertEqual(payload["selected_count"], 3)
        submit.assert_called_once_with(20, include_archived=False)

    def test_http_endpoint_reports_conflict_for_duplicate_batch(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        with patch.object(
            app.manager,
            "submit_ai_reanalysis",
            side_effect=jobs.AIReanalysisInProgress("已有 AI 重分析任务正在进行（任务 8）"),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            body = b"{}"
            connection.request(
                "POST",
                "/api/candidates/reanalyze-ai",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        self.assertEqual(response.status, 409)
        self.assertIn("正在进行", payload["error"])


if __name__ == "__main__":
    unittest.main()
