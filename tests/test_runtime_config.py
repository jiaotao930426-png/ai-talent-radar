import os
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class ServerAddressTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_defaults_keep_native_service_on_loopback(self) -> None:
        self.assertEqual(app.server_address_from_env(), ("127.0.0.1", 8765))

    @patch.dict(
        os.environ,
        {"TALENT_RADAR_HOST": "0.0.0.0", "TALENT_RADAR_PORT": "9000"},
        clear=True,
    )
    def test_container_address_can_be_configured(self) -> None:
        self.assertEqual(app.server_address_from_env(), ("0.0.0.0", 9000))

    def test_invalid_ports_are_rejected(self) -> None:
        for value in ("", "0", "65536", "-1", "+8765", "8765.0", " 8765", "8765 "):
            with self.subTest(value=value), patch.dict(
                os.environ, {"TALENT_RADAR_PORT": value}, clear=True
            ):
                with self.assertRaisesRegex(ValueError, "TALENT_RADAR_PORT"):
                    app.server_address_from_env()

    def test_invalid_hosts_are_rejected(self) -> None:
        for value in ("", " 127.0.0.1", "127.0.0.1 ", "local host", "localhost\n"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"TALENT_RADAR_HOST": value}, clear=True
            ):
                with self.assertRaisesRegex(ValueError, "TALENT_RADAR_HOST"):
                    app.server_address_from_env()

    @patch.dict(os.environ, {"TALENT_RADAR_PORT": "invalid"}, clear=True)
    @patch("app.db.init_db")
    def test_invalid_configuration_fails_before_database_initialization(
        self, mocked_init_db
    ) -> None:
        with self.assertRaisesRegex(ValueError, "TALENT_RADAR_PORT"):
            app.run()
        mocked_init_db.assert_not_called()


class CrossPlatformCopyTests(unittest.TestCase):
    def test_posix_launchers_are_executable_in_a_fresh_clone(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        for relative_path in (
            "scripts/start.sh",
            "scripts/install-macos-launchd.sh",
            "scripts/uninstall-macos-launchd.sh",
        ):
            with self.subTest(relative_path=relative_path):
                mode = (project_dir / relative_path).stat().st_mode
                self.assertTrue(mode & 0o111, "{} must be executable".format(relative_path))

    def test_dockerignore_excludes_local_data_and_generated_artifacts(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        dockerignore = (project_dir / ".dockerignore").read_text(encoding="utf-8")
        required_patterns = (
            "logs/",
            "reports/",
            "work/",
            ".playwright-cli/",
            "*.xlsx",
            "*.xls",
            "*.csv",
            "*.tsv",
            "*.tmp",
            "*.bak",
        )
        for pattern in required_patterns:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, dockerignore)

    def test_frontend_does_not_reference_macos_application_support(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        markup = (project_dir / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Application Support", markup)
        self.assertIn("当前数据库目录的 backups 子目录", markup)

    def test_macos_launchd_template_has_no_machine_specific_path(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        template = (project_dir / "launchd" / "com.qft.ai-talent-radar.plist.template").read_text(
            encoding="utf-8"
        )
        self.assertIn("__PROJECT_DIR__", template)
        self.assertIn("__PYTHON_BIN__", template)
        self.assertNotIn("/Users/", template)
        self.assertNotIn("Application Support", template)

    def test_public_email_rescan_tool_is_portable(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        tool = (project_dir / "tools" / "import_public_profile_emails.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("TALENT_RADAR_DB", (project_dir / "db.py").read_text(encoding="utf-8"))
        self.assertIn("db.init_db()", tool)
        self.assertNotIn("Application Support", tool)


if __name__ == "__main__":
    unittest.main()
