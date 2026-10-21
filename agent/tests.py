import os
import tempfile
import unittest
from pathlib import Path

from access import (
    clean_env_path,
    cpu_percent,
    format_env,
    memory_mb,
    read_env_file,
    summarize,
    visible,
    write_env_file,
)


class AccessTests(unittest.TestCase):
    def test_visible_requires_matching_label(self):
        self.assertTrue(visible({"track.project": "shop"}, "shop"))
        self.assertFalse(visible({"track.project": "shop"}, "other"))
        self.assertFalse(visible(None, "shop"))
        self.assertFalse(visible({"track.project": "shop"}, ""))

    def test_summarize_drops_zero_dates_and_slash(self):
        row = summarize(
            container_id="a" * 64,
            name="/demo",
            image="nginx:alpine",
            state={
                "Status": "running",
                "ExitCode": 0,
                "OOMKilled": False,
                "RestartCount": 2,
                "StartedAt": "2026-09-23T10:00:00.123456789Z",
                "FinishedAt": "0001-01-01T00:00:00Z",
                "Health": {"Status": "healthy"},
            },
        )
        self.assertEqual(row["name"], "demo")
        self.assertEqual(row["short_id"], "a" * 12)
        self.assertIsNone(row["finished_at"])
        self.assertEqual(row["health"], "healthy")
        self.assertEqual(row["restart_count"], 2)
        self.assertEqual(row["project"], "")

    def test_summarize_keeps_project_label(self):
        row = summarize(
            container_id="a" * 64,
            name="shop-web",
            image="shop",
            state={"Status": "running"},
            labels={"track.project": "shop"},
        )
        self.assertEqual(row["project"], "shop")

    def test_env_path_stays_inside_the_projects_folder(self):
        self.assertEqual(clean_env_path("shop/.env"), "shop/.env")
        self.assertEqual(clean_env_path("../secret.env"), "")
        self.assertEqual(clean_env_path("/etc/passwd"), "")
        with tempfile.TemporaryDirectory() as tmp:
            shop = Path(tmp) / "shop"
            shop.mkdir()
            write_env_file(tmp, "shop/.env", "TOKEN=sk-live-zzzzzz\n")
            text, missing = read_env_file(tmp, "shop/.env")
            self.assertFalse(missing)
            self.assertEqual(text, "TOKEN=sk-live-zzzzzz\n")
            with self.assertRaises(ValueError):
                read_env_file(tmp, "../.env")
            with self.assertRaises(FileNotFoundError):
                write_env_file(tmp, "missing/.env", "A=1\n")

    def test_cpu_percent(self):
        stats = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1]},
                "system_cpu_usage": 1000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100},
                "system_cpu_usage": 500,
            },
        }
        self.assertEqual(cpu_percent(stats), 40.0)

    def test_format_env_sorts_names_and_drops_blank(self):
        text = format_env(["PATH=/usr/bin", "TOKEN=sk-live-zzzzzz", "bad name=1", "", None])
        self.assertEqual(text, "PATH=/usr/bin\nTOKEN=sk-live-zzzzzz")

    def test_memory_mb(self):
        usage, limit = memory_mb({"memory_stats": {"usage": 1024 * 1024, "limit": 2 * 1024 * 1024}})
        self.assertEqual(usage, 1.0)
        self.assertEqual(limit, 2.0)


class ApiTests(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_TOKEN"] = "abc"
        from fastapi.testclient import TestClient

        import app as agent_app

        self.client = TestClient(agent_app.app)

    def test_health_is_open_and_containers_require_token(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        denied = self.client.get("/containers")
        self.assertEqual(denied.status_code, 401)

    def test_log_tail_over_2000_is_rejected(self):
        response = self.client.get(
            "/container/logs",
            params={"name": "demo", "tail": 2001},
            headers={"X-Agent-Token": "abc"},
        )
        self.assertEqual(response.status_code, 422)

    def test_invalid_name_never_reaches_docker(self):
        response = self.client.get(
            "/container",
            params={"name": "../x"},
            headers={"X-Agent-Token": "abc"},
        )
        self.assertEqual(response.status_code, 404)

    def test_project_env_requires_token_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PROJECTS_ROOT"] = tmp
            (Path(tmp) / "shop").mkdir()
            (Path(tmp) / "shop" / ".env").write_bytes(b"TOKEN=secret\n")
            denied = self.client.get("/project/env", params={"path": "shop/.env"})
            self.assertEqual(denied.status_code, 401)
            ok = self.client.get(
                "/project/env",
                params={"path": "shop/.env"},
                headers={"X-Agent-Token": "abc"},
            )
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["env"], "TOKEN=secret\n")
            self.assertFalse(ok.json()["missing"])
            escaped = self.client.get(
                "/project/env",
                params={"path": "../secret.env"},
                headers={"X-Agent-Token": "abc"},
            )
            self.assertEqual(escaped.status_code, 400)
