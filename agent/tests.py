import os
import unittest

from access import cpu_percent, memory_mb, summarize, visible


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

    def test_invalid_name_never_reaches_docker(self):
        response = self.client.get(
            "/container",
            params={"name": "../x"},
            headers={"X-Agent-Token": "abc"},
        )
        self.assertEqual(response.status_code, 404)
