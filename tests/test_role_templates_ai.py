import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
import collectors
import jobs
import ollama_matcher
from scoring import score_candidate, template_match_breakdown


class RoleTemplateDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db = os.environ.get("TALENT_RADAR_DB")
        os.environ["TALENT_RADAR_DB"] = str(Path(self.temp_dir.name) / "test.db")
        db.init_db()

    def tearDown(self) -> None:
        if self.previous_db is None:
            os.environ.pop("TALENT_RADAR_DB", None)
        else:
            os.environ["TALENT_RADAR_DB"] = self.previous_db
        self.temp_dir.cleanup()

    def test_builtin_templates_are_idempotent_and_custom_slug_is_generated(self) -> None:
        self.assertEqual(len(db.list_role_templates()), 3)
        template = db.create_role_template(
            {
                "name": "AI 研究工程师",
                "description": "研究型岗位",
                "required_terms": ["pytorch"],
                "search_keywords": ["research agent"],
            }
        )
        self.assertTrue(template["slug"].startswith("role-"))
        self.assertFalse(template["is_builtin"])
        updated = db.update_role_template(template["id"], {"preferred_terms": ["transformer"]})
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["preferred_terms"], ["transformer"])
        self.assertFalse(db.set_role_template_active(template["id"], False)["is_active"])
        self.assertIsNone(db.get_role_template("AI 研究工程师", require_active=True))

        renamed = db.update_role_template(template["id"], {"slug": "ai-research-platform"})
        self.assertEqual(renamed["slug"], "ai-research-platform")

    def test_builtin_slug_is_stable_after_rename_and_restart(self) -> None:
        template = db.get_role_template("ai-agent-engineer")
        self.assertIsNotNone(template)

        updated = db.update_role_template(template["id"], {"name": "智能体平台工程师"})
        self.assertEqual(updated["slug"], "ai-agent-engineer")
        with self.assertRaisesRegex(ValueError, "内置岗位模板的标识不可修改"):
            db.update_role_template(template["id"], {"slug": "renamed-agent-role"})

        db.init_db()
        templates = db.list_role_templates()
        self.assertEqual(len(templates), 3)
        self.assertEqual(
            [item["slug"] for item in templates].count("ai-agent-engineer"),
            1,
        )
        self.assertEqual(
            db.get_role_template("ai-agent-engineer")["name"],
            "智能体平台工程师",
        )

    def test_scheduled_config_keeps_snapshot_after_template_changes(self) -> None:
        template = db.create_role_template(
            {
                "name": "合成数据工程师",
                "required_terms": ["synthetic-data"],
                "search_keywords": ["synthetic one", "synthetic two"],
            }
        )
        config = jobs.normalize_config(
            {
                "roles": [template["name"]],
                "cities": ["北京"],
                "sources": ["github"],
            },
            strict_roles=True,
        )
        db.update_role_template(template["id"], {"name": "已改名岗位"})
        db.set_role_template_active(template["id"], False)

        preserved = jobs.normalize_config(
            config,
            strict_roles=True,
            allow_role_snapshots=True,
        )
        selected = jobs._template_for_role("合成数据工程师", preserved)
        self.assertEqual(preserved["roles"], ["合成数据工程师"])
        self.assertEqual(selected["version"], 1)
        self.assertEqual(selected["required_terms"], ["synthetic-data"])

    def test_no_active_templates_is_rejected_instead_of_using_legacy_roles(self) -> None:
        for template in db.list_role_templates(active_only=True):
            db.set_role_template_active(template["id"], False)
        with self.assertRaisesRegex(ValueError, "没有启用的岗位模板"):
            jobs.normalize_config(
                {"roles": ["AI Agent 工程师"]},
                strict_roles=True,
            )

    def test_invalid_due_schedule_creates_visible_failed_job(self) -> None:
        config = {
            "mode": "search",
            "target": 1,
            "roles": ["不存在的岗位"],
            "cities": ["北京"],
            "sources": ["github"],
            "keywords": "",
            "prefer_contactable": True,
            "use_local_ai": False,
            "role_template_snapshots": [],
        }
        job_id = jobs.JobManager()._submit_due_schedule(config)
        failed = db.get_job(job_id)
        self.assertEqual(failed["status"], "执行失败")
        self.assertIn("岗位模板", failed["error"])

    def test_all_template_search_keywords_are_collected(self) -> None:
        template = db.create_role_template(
            {
                "name": "多搜索词岗位",
                "required_terms": ["search-term"],
                "search_keywords": ["first term", "second term"],
            }
        )
        config = jobs.normalize_config(
            {
                "target": 1,
                "roles": [template["name"]],
                "cities": ["北京"],
                "sources": ["github"],
                "prefer_contactable": False,
            },
            strict_roles=True,
        )
        calls = []

        def fake_collector(keyword, city, role, limit):
            calls.append((keyword, city, role, limit))
            return []

        job_id = db.create_job("测试", config)
        with patch.dict(jobs.SEARCH_COLLECTORS, {"github": fake_collector}):
            jobs.JobManager()._run_search_job(job_id, config)
        self.assertEqual([call[0] for call in calls], ["first term", "second term"])

    def test_collector_keeps_full_evidence_until_custom_template_scoring(self) -> None:
        template = db.create_role_template(
            {
                "name": "量子工具工程师",
                "required_terms": ["quantumwidget"],
                "search_keywords": ["quantumwidget"],
            }
        )
        profile = {
            "id": 42,
            "login": "synthetic-user",
            "name": "Synthetic User",
            "location": "Beijing",
            "html_url": "https://example.test/synthetic-user",
        }
        repos = [
            {
                "name": "agent-project-{}".format(index),
                "html_url": "https://example.test/agent-{}".format(index),
                "description": "agent workflow",
                "stargazers_count": 50,
                "fork": False,
            }
            for index in range(7)
        ]
        repos.append(
            {
                "name": "quantumwidget-runtime",
                "html_url": "https://example.test/quantumwidget",
                "description": "quantumwidget compiler",
                "stargazers_count": 0,
                "fork": False,
            }
        )
        with patch("collectors.fetch_json", side_effect=[profile, repos]):
            candidate = collectors.github_user(
                "synthetic-user", "AI Agent 工程师", "北京"
            )
        self.assertEqual(len(candidate["evidence"]), 8)
        prepared = jobs._prepare_candidate(
            candidate,
            template["name"],
            "北京",
            False,
            template,
        )
        self.assertEqual(prepared["evidence"][0]["title"], "quantumwidget-runtime")


class RoleTemplateScoringTests(unittest.TestCase):
    def test_custom_template_exposes_hits_and_missing_required_terms(self) -> None:
        template = {
            "name": "AI 研究工程师",
            "required_terms": ["pytorch"],
            "preferred_terms": ["transformer"],
            "evidence_terms": ["benchmark"],
            "exclude_terms": ["实习生"],
            "weights": {"required": 14, "preferred": 8, "evidence": 7, "exclude": 18},
        }
        profile = {"display_name": "Candidate", "bio": "pytorch transformer", "city": "北京"}
        evidence = [{"title": "benchmark", "description": "model benchmark", "url": "https://example.test/p"}]
        score, role, _ = score_candidate(profile, evidence, "AI 研究工程师", "北京", template)
        self.assertGreater(score, 50)
        self.assertEqual(role, "AI 研究工程师")
        breakdown = template_match_breakdown(profile, evidence, template)
        self.assertEqual(breakdown["missing_required_terms"], [])


class OllamaMatcherTests(unittest.TestCase):
    def test_public_payload_does_not_include_contact_details(self) -> None:
        payload = ollama_matcher._public_payload(
            {
                "display_name": "Candidate",
                "username": "candidate-user",
                "bio": "agent",
                "company": "Example Company",
                "city": "北京",
                "contact_email": "candidate@example.test",
                "phone": "13800000000",
                "evidence": [],
            },
            {"name": "AI Agent 工程师"},
        )
        self.assertNotIn("Candidate", str(payload))
        self.assertNotIn("candidate-user", str(payload))
        self.assertNotIn("Example Company", str(payload))
        self.assertNotIn("北京", str(payload))
        self.assertNotIn("contact_email", str(payload))
        self.assertNotIn("13800000000", str(payload))

    def test_public_payload_redacts_contacts_embedded_in_public_text(self) -> None:
        payload = ollama_matcher._public_payload(
            {
                "bio": "agent contact candidate@example.test 13800138000",
                "evidence": [
                    {
                        "title": "agent project",
                        "description": "maintainer: owner@example.org / 13912345678",
                        "url": "https://example.test/project",
                    }
                ],
            },
            {"name": "AI Agent 工程师", "description": "owner@example.net"},
        )
        payload_text = str(payload)
        for value in (
            "candidate@example.test",
            "owner@example.org",
            "owner@example.net",
            "13800138000",
            "13912345678",
        ):
            self.assertNotIn(value, payload_text)
        self.assertIn("公开邮箱已隐藏", payload_text)
        self.assertIn("公开电话已隐藏", payload_text)

    def test_model_text_output_is_redacted_before_persistence(self) -> None:
        parsed = ollama_matcher._parse_json(
            '{"match_score": 70, "confidence": 0.5, '
            '"matched_skills": ["agent@example.test"], '
            '"evidence": [{"title": "owner@example.org", "url": "https://example.test/p", '
            '"reason": "call 13800138000"}], '
            '"gaps": ["13912345678"], "summary": "email owner@example.net"}'
        )
        parsed_text = str(parsed)
        for value in (
            "agent@example.test",
            "owner@example.org",
            "owner@example.net",
            "13800138000",
            "13912345678",
        ):
            self.assertNotIn(value, parsed_text)
        self.assertIn("公开邮箱已隐藏", parsed_text)
        self.assertIn("公开电话已隐藏", parsed_text)

    def test_json_parser_and_score_combination_are_bounded(self) -> None:
        result = ollama_matcher._parse_json(
            '```json\n{"match_score": 120, "confidence": 2, "matched_skills": ["agent"], '
            '"evidence": [], "gaps": [], "summary": "ok"}\n```'
        )
        self.assertEqual(result["match_score"], 100)
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(ollama_matcher._parse_json('{"match_score": 0.85, "confidence": 0.5}')['match_score'], 85)
        self.assertEqual(
            ollama_matcher._parse_json(
                '{"score": 0.72, "confidence": 0.8, "summary": "compatible"}'
            )["match_score"],
            72,
        )
        self.assertEqual(ollama_matcher.combine_scores(20, 100, 1), 52)
        self.assertEqual(ollama_matcher.combine_scores(90, 0, 0), 90)
        self.assertEqual(ollama_matcher.combine_scores(90, 0, 0.5), 72)

    def test_ollama_address_is_limited_to_local_or_docker_host(self) -> None:
        with patch.dict(
            os.environ,
            {"OLLAMA_BASE_URL": "http://host.docker.internal:11434"},
        ):
            self.assertEqual(
                ollama_matcher._base_url(),
                "http://host.docker.internal:11434",
            )
        with patch.dict(
            os.environ,
            {"OLLAMA_BASE_URL": "https://external.example.test"},
        ):
            with self.assertRaisesRegex(ValueError, "本机"):
                ollama_matcher._base_url()

    def test_native_ollama_is_started_once_and_model_is_checked(self) -> None:
        key = "http://127.0.0.1:11434|qwen3:4b"
        ollama_matcher._HEALTHY_CACHE.clear()
        ollama_matcher._START_ATTEMPTS.clear()
        ollama_matcher._START_ERRORS.clear()
        with patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:4b",
                "OLLAMA_AUTOSTART": "true",
            },
            clear=True,
        ), patch.object(
            ollama_matcher,
            "_tags",
            side_effect=[ollama_matcher.OllamaUnavailable("offline"), ["qwen3:4b"]],
        ) as tags, patch.object(
            ollama_matcher, "_ollama_binary", return_value="/usr/local/bin/ollama"
        ), patch.object(ollama_matcher, "_start_ollama") as start:
            ollama_matcher.ensure_ollama_available(timeout=1)
            ollama_matcher.ensure_ollama_available(timeout=1)
        start.assert_called_once_with("/usr/local/bin/ollama")
        self.assertEqual(tags.call_count, 2)
        self.assertIn(key, ollama_matcher._HEALTHY_CACHE)

    def test_ollama_missing_model_is_reported_without_pull(self) -> None:
        ollama_matcher._HEALTHY_CACHE.clear()
        with patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:4b",
                "OLLAMA_AUTOSTART": "true",
            },
            clear=True,
        ), patch.object(ollama_matcher, "_tags", return_value=["llama3:8b"]), patch.object(
            ollama_matcher, "_start_ollama"
        ) as start:
            with self.assertRaisesRegex(ollama_matcher.OllamaUnavailable, "未找到模型"):
                ollama_matcher.ensure_ollama_available(timeout=1)
        start.assert_not_called()

    def test_matcher_uses_hardware_tolerant_default_timeout(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                result = {
                    "match_score": 70,
                    "confidence": 0.5,
                    "matched_skills": ["agent"],
                    "evidence": [],
                    "gaps": [],
                    "summary": "ok",
                }
                return json.dumps({"response": json.dumps(result)}).encode("utf-8")

        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "ollama_matcher.urllib.request.urlopen", return_value=Response()
            ) as mocked_urlopen, patch(
                "ollama_matcher.ensure_ollama_available"
            ):
                ollama_matcher.match_candidate(
                    {"bio": "agent", "evidence": []}, {"name": "Agent"}
                )
        self.assertEqual(
            mocked_urlopen.call_args.kwargs["timeout"],
            ollama_matcher.DEFAULT_TIMEOUT_SECONDS,
        )
        self.assertEqual(ollama_matcher.DEFAULT_TIMEOUT_SECONDS, 240)

    def test_compose_passes_docker_host_ollama_configuration(self) -> None:
        compose = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("OLLAMA_DOCKER_BASE_URL", compose)
        self.assertIn("host.docker.internal:host-gateway", compose)

    def test_json_parser_rejects_schema_default_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "空的匹配结果"):
            ollama_matcher._parse_json(
                '{"match_score": 0, "confidence": 0, "matched_skills": [], '
                '"evidence": [], "gaps": [], "summary": ""}'
            )

    def test_json_parser_rejects_qwen_input_echo(self) -> None:
        with self.assertRaisesRegex(ValueError, "回显了输入数据"):
            ollama_matcher._parse_json(
                '{"role_template": {"name": "Agent"}, "candidate": {"bio": "agent"}}'
            )

    def test_matcher_drops_model_citations_not_in_public_evidence(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                result = {
                    "match_score": 70,
                    "confidence": 0.5,
                    "matched_skills": [],
                    "evidence": [
                        {"title": "made-up", "url": "https://example.test/nope", "reason": "x"}
                    ],
                    "gaps": [],
                    "summary": "ok",
                }
                return json.dumps({"response": json.dumps(result)}).encode("utf-8")

        candidate = {
            "display_name": "Candidate",
            "bio": "agent",
            "evidence": [{"title": "real", "url": "https://example.test/real"}],
        }
        with patch("ollama_matcher.urllib.request.urlopen", return_value=Response()), patch(
            "ollama_matcher.ensure_ollama_available"
        ):
            result = ollama_matcher.match_candidate(candidate, {"name": "Agent"})
        self.assertEqual(result["evidence"], [])

    def test_matcher_accepts_qwen3_thinking_field_when_response_is_empty(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                result = {
                    "match_score": 64,
                    "confidence": 0.6,
                    "matched_skills": ["agent"],
                    "evidence": [],
                    "gaps": [],
                    "summary": "thinking field",
                }
                return json.dumps({"response": "", "thinking": json.dumps(result)}).encode("utf-8")

        with patch("ollama_matcher.urllib.request.urlopen", return_value=Response()), patch(
            "ollama_matcher.ensure_ollama_available"
        ):
            result = ollama_matcher.match_candidate(
                {"display_name": "Candidate", "evidence": []}, {"name": "Agent"}
            )
        self.assertEqual(result["match_score"], 64)

    def test_prepare_candidate_falls_back_when_ollama_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_db = os.environ.get("TALENT_RADAR_DB")
            os.environ["TALENT_RADAR_DB"] = str(Path(temp_dir) / "test.db")
            try:
                db.init_db()
                candidate = {
                    "source": "github",
                    "external_id": "candidate",
                    "username": "candidate",
                    "display_name": "Candidate",
                    "profile_url": "https://github.com/candidate",
                    "bio": "agent engineer",
                    "city": "北京",
                    "evidence": [],
                }
                with patch("jobs.match_candidate", side_effect=ollama_matcher.OllamaUnavailable("offline")):
                    prepared = jobs._prepare_candidate(candidate, "AI Agent 工程师", "北京", True)
                self.assertEqual(prepared["ai_match_status"], "不可用，已回退规则")
                self.assertIsNone(prepared["ai_match_score"])
                self.assertEqual(prepared["match_score"], prepared["rule_match_score"])
            finally:
                if previous_db is None:
                    os.environ.pop("TALENT_RADAR_DB", None)
                else:
                    os.environ["TALENT_RADAR_DB"] = previous_db


class StrictRoleConfigTests(unittest.TestCase):
    def test_unknown_role_is_rejected_for_new_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_db = os.environ.get("TALENT_RADAR_DB")
            os.environ["TALENT_RADAR_DB"] = str(Path(temp_dir) / "test.db")
            try:
                db.init_db()
                with self.assertRaises(ValueError):
                    jobs.normalize_config({"roles": ["不存在的岗位"]}, strict_roles=True)
            finally:
                if previous_db is None:
                    os.environ.pop("TALENT_RADAR_DB", None)
                else:
                    os.environ["TALENT_RADAR_DB"] = previous_db


class RoleScoreMigrationTests(unittest.TestCase):
    def test_existing_match_score_backfills_new_rule_score_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_db = os.environ.get("TALENT_RADAR_DB")
            path = Path(temp_dir) / "legacy.db"
            os.environ["TALENT_RADAR_DB"] = str(path)
            try:
                connection = sqlite3.connect(str(path))
                connection.executescript(
                    """
                    CREATE TABLE candidates (
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
                        contact_url TEXT NOT NULL DEFAULT '',
                        suggested_role TEXT NOT NULL DEFAULT 'AI Agent 工程师',
                        match_score INTEGER NOT NULL DEFAULT 0,
                        education_status TEXT NOT NULL DEFAULT '待核验',
                        age_status TEXT NOT NULL DEFAULT '待本人确认',
                        review_status TEXT NOT NULL DEFAULT '待审核',
                        review_note TEXT NOT NULL DEFAULT '',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        source_updated_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(source, external_id)
                    );
                    INSERT INTO candidates (
                        source, external_id, username, display_name, profile_url,
                        match_score, first_seen_at, last_seen_at, created_at, updated_at
                    ) VALUES (
                        'github', 'legacy-1', 'legacy', 'Legacy',
                        'https://example.test/legacy', 87,
                        '2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00',
                        '2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00'
                    );
                    """
                )
                connection.commit()
                connection.close()

                db.init_db()
                with db.connect() as migrated:
                    row = migrated.execute(
                        "SELECT match_score, rule_match_score FROM candidates WHERE external_id = ?",
                        ("legacy-1",),
                    ).fetchone()
                self.assertEqual(row["match_score"], 87)
                self.assertEqual(row["rule_match_score"], 87)
            finally:
                if previous_db is None:
                    os.environ.pop("TALENT_RADAR_DB", None)
                else:
                    os.environ["TALENT_RADAR_DB"] = previous_db


if __name__ == "__main__":
    unittest.main()
