import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from monitor.envfile import diff_env, mask_value, parse_env

from monitor.models import EnvName, EnvRevision, NoteEnv, Project, UserContainer, clean_env_path, set_user_containers
from monitor.present import tone
from monitor.services.llm import LLMNotConfigured, explain
from monitor.services.redact import redact


CONTAINER = {
    "id": "a" * 64,
    "short_id": "a" * 12,
    "name": "shop-web",
    "image": "shop:latest",
    "status": "exited",
    "health": None,
    "restart_count": 3,
    "exit_code": 1,
    "oom_killed": False,
    "started_at": "2026-09-23T10:00:00Z",
    "finished_at": "2026-09-23T10:01:00Z",
}
OTHER = {**CONTAINER, "id": "b" * 64, "short_id": "b" * 12, "name": "other-web"}


@override_settings(
    AGENT_URL="http://127.0.0.1:9",
    AGENT_TOKEN="test",
    LLM_API_TOKEN="",
    LLM_API_BASE="https://api.openai.com/v1",
)
class MonitorPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("ada", password="secret-pass-123")
        self.other = User.objects.create_user("bea", password="secret-pass-123")
        UserContainer.objects.create(user=self.owner, container_name="shop-web")
        self.project = Project.objects.create(name="Shop", slug="shop", dockerfile="FROM python:3.12\n")
        EnvName.objects.create(project=self.project, name="DATABASE_URL", is_set=True)

    def test_env_name_has_no_value_field(self):
        self.assertNotIn("value", {field.name for field in EnvName._meta.fields})
        self.assertNotIn("value", {field.name for field in NoteEnv._meta.fields})

    def test_login_page_renders(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Log in")

    def test_login_required(self):
        response = self.client.get(reverse("monitor:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_login_lands_on_dashboard(self):
        response = self.client.post(reverse("login"), {"username": "ada", "password": "secret-pass-123"})
        self.assertRedirects(response, "/")
        page = self.client.get("/")
        self.assertContains(page, "Containers")

    def test_dashboard_when_agent_is_down(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unreachable")

    def test_bad_container_name_is_not_proxied(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:container_detail", args=["_hidden"]))
        self.assertEqual(response.status_code, 404)

    def test_other_user_cannot_open_granted_container(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("monitor:container_detail", args=["shop-web"]))
        self.assertEqual(response.status_code, 404)

    @patch("monitor.views.agent_api.list_containers")
    def test_user_sees_only_assigned_containers(self, list_containers):
        list_containers.return_value = [CONTAINER, OTHER]
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:dashboard"))
        self.assertContains(response, "shop-web")
        self.assertNotContains(response, "other-web")

    @patch("monitor.views.agent_api.list_containers")
    def test_superuser_sees_every_container(self, list_containers):
        list_containers.return_value = [CONTAINER, OTHER]
        root = User.objects.create_superuser("root", "root@example.com", "secret-pass-123")
        self.client.force_login(root)
        response = self.client.get(reverse("monitor:dashboard"))
        self.assertContains(response, "shop-web")
        self.assertContains(response, "other-web")

    @patch("monitor.views.agent_api.list_containers")
    def test_dashboard_groups_services_from_one_compose_stack(self, list_containers):
        list_containers.return_value = [
            {**CONTAINER, "name": "voip-api-1", "compose_project": "voip"},
            {**OTHER, "name": "track-demo"},
            {**CONTAINER, "id": "c" * 64, "short_id": "c" * 12, "name": "voip-web-1", "compose_project": "voip"},
        ]
        root = User.objects.create_superuser("root", "root@example.com", "secret-pass-123")
        self.client.force_login(root)
        response = self.client.get(reverse("monitor:dashboard"))
        body = response.content.decode()
        self.assertLess(body.index('class="stack-name">voip'), body.index('class="stack-name">Other'))
        self.assertLess(body.index("voip-web-1"), body.index('class="stack-name">Other'))
        self.assertLess(body.index('class="stack-name">Other'), body.index("track-demo"))

    def test_explain_without_local_model_shows_setup(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("monitor:explain_submit", args=["shop-web"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response["Content-Type"])
        body = b"".join(response.streaming_content).decode()
        self.assertIn("LLM_API_BASE", body)

    @patch("monitor.views.agent_api.get_logs", return_value="startup failed")
    @patch("monitor.views.agent_api.get_container")
    @patch("monitor.views.explain_chunks", side_effect=lambda **kwargs: iter(["Likely ", "cause."]))
    @override_settings(LLM_API_TOKEN="", LLM_API_BASE="http://192.168.0.200:8880/v1")
    def test_explain_streams_into_events(self, explain_chunks, get_container, get_logs):
        get_container.return_value = {"container": CONTAINER, "stats": {}}
        self.client.force_login(self.owner)
        response = self.client.post(reverse("monitor:explain_submit", args=["shop-web"]))
        body = b"".join(response.streaming_content).decode()
        self.assertIn("Likely ", body)
        self.assertIn("cause.", body)
        self.assertIn('"done": true', body)
        explain_chunks.assert_called_once()
        get_logs.assert_called_once()

    @patch("monitor.views.agent_api.get_logs", return_value="")
    @patch("monitor.views.agent_api.get_container")
    def test_detail_keeps_explain_on_the_page(self, get_container, get_logs):
        get_container.return_value = {
            "container": CONTAINER,
            "stats": {"cpu_percent": 0, "mem_usage_mb": 1, "mem_limit_mb": 2},
        }
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:container_detail", args=["shop-web"]))
        self.assertContains(response, 'id="explain-panel"')
        self.assertContains(response, "Explain with LLM")
        self.assertContains(response, "Last 1000")
        self.assertContains(response, "Last 2000")
        self.assertContains(response, "Download .txt")
        self.assertContains(response, 'id="log-live"')
        self.assertContains(response, 'data-download="500"')
        self.assertContains(response, "Hide health checks")
        self.assertContains(response, "no project label")
        get_logs.assert_called_once()

    @patch("monitor.views.agent_api.get_logs", return_value="line\n")
    def test_log_tail_only_allows_known_sizes(self, get_logs):
        self.client.force_login(self.owner)
        ok = self.client.get(reverse("monitor:container_logs", args=["shop-web"]) + "?tail=500")
        self.assertEqual(ok.status_code, 200)
        get_logs.assert_called_once_with("shop-web", tail=500)
        get_logs.reset_mock()
        wide = self.client.get(reverse("monitor:container_logs", args=["shop-web"]) + "?tail=2000")
        self.assertEqual(wide.status_code, 200)
        get_logs.assert_called_once_with("shop-web", tail=2000)
        get_logs.reset_mock()
        limited = self.client.get(reverse("monitor:container_logs", args=["shop-web"]) + "?tail=99999")
        self.assertEqual(limited.status_code, 200)
        get_logs.assert_called_once_with("shop-web", tail=200)

    def test_set_user_containers_replaces_names(self):
        set_user_containers(self.owner, ["shop-web", "shop-web", "api"])
        self.assertEqual(
            set(self.owner.container_grants.values_list("container_name", flat=True)),
            {"shop-web", "api"},
        )
        set_user_containers(self.owner, ["api"])
        self.assertEqual(
            list(self.owner.container_grants.values_list("container_name", flat=True)),
            ["api"],
        )


class RedactTests(TestCase):
    def test_redacts_secrets(self):
        text = redact(
            "token=super-secret-token Bearer abc.def-ghi "
            "postgres://user:s3cret@db/app "
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        self.assertNotIn("super-secret-token", text)
        self.assertNotIn("s3cret", text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", text)
        self.assertIn("[redacted]", text)
        self.assertIn("[redacted-jwt]", text)

    def test_tone(self):
        self.assertEqual(tone("running", None), "ok")
        self.assertEqual(tone("running", "unhealthy"), "warn")
        self.assertEqual(tone("exited", None), "bad")


class ExplainTests(TestCase):
    @override_settings(LLM_API_TOKEN="", LLM_API_BASE="https://api.openai.com/v1")
    def test_unconfigured(self):
        with self.assertRaises(LLMNotConfigured):
            explain(status={}, logs="", dockerfile="", env_names=[])

    @override_settings(
        LLM_API_TOKEN="",
        LLM_API_BASE="http://192.168.0.200:8880/v1",
        LLM_MODEL="qwen",
        LLM_TEMPERATURE=0.7,
        LLM_MAX_TOKENS=1200,
        LLM_EFFORT="low",
    )
    @patch("monitor.services.llm.httpx.Client")
    def test_local_qwen_payload(self, client_cls):
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = iter(
            [
                'data: {"choices":[{"delta":{"content":"The process exited."}}]}',
                "data: [DONE]",
            ]
        )
        stream_cm = Mock()
        stream_cm.__enter__ = Mock(return_value=response)
        stream_cm.__exit__ = Mock(return_value=False)
        client = Mock()
        client.stream.return_value = stream_cm
        client_cm = Mock()
        client_cm.__enter__ = Mock(return_value=client)
        client_cm.__exit__ = Mock(return_value=False)
        client_cls.return_value = client_cm
        answer = explain(
            status={"status": "exited", "exit_code": 1},
            logs="startup failed token=super-secret-token",
            dockerfile="FROM python\n",
            env_names=[{"name": "DATABASE_URL", "state": "set", "value": "should-not-leave"}],
        )
        self.assertEqual(answer, "The process exited.")
        body = client.stream.call_args.kwargs["json"]
        blob = json.dumps(body)
        self.assertNotIn("super-secret-token", blob)
        self.assertNotIn("should-not-leave", blob)
        self.assertTrue(body["stream"])
        self.assertEqual(body["model"], "qwen")
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["max_tokens"], 1200)
        self.assertEqual(body["effort"], "low")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("Nothing suspicious. The app is up.", body["messages"][0]["content"])
        self.assertNotIn("Authorization", client.stream.call_args.kwargs["headers"])


class EnvFileTests(TestCase):
    def test_parse_ignores_comments_and_quotes(self):
        parsed = parse_env('# note\nexport TOKEN="sk-live-secret"\nEMPTY=\nBAD-NAME=1\n')
        self.assertEqual(parsed, {"TOKEN": "sk-live-secret", "EMPTY": ""})

    def test_diff_marks_add_remove_and_change(self):
        rows = {row["name"]: row["kind"] for row in diff_env({"OLD": "a", "SAME": "1"}, {"NEW": "b", "SAME": "1", "OLD": "z"})}
        self.assertEqual(rows, {"OLD": "changed", "NEW": "added", "SAME": "same"})
        self.assertEqual(mask_value("sk-live-zzzzzz"), "sk-…zzzz")
        self.assertEqual(mask_value("short"), "••••")
        self.assertEqual(clean_env_path("shop/.env"), "shop/.env")
        self.assertEqual(clean_env_path(r"shop\.env"), "shop/.env")
        self.assertEqual(clean_env_path("../secret.env"), "")
        self.assertEqual(clean_env_path("/etc/passwd"), "")
        self.assertEqual(clean_env_path("Mongo 7 config/.env"), "Mongo 7 config/.env")


class EnvPageTests(MonitorPageTests):
    def setUp(self):
        super().setUp()
        self.project.env_path = "shop/.env"
        self.project.save()
        self.shop = {**CONTAINER, "project": "shop"}

    @patch("monitor.views.agent_api.read_project_env", return_value=("TOKEN=sk-live-zzzzzz\nNEW=1\n", False))
    @patch("monitor.views.agent_api.get_logs", return_value="")
    @patch("monitor.views.agent_api.get_container")
    def test_env_page_shows_project_file(self, get_container, get_logs, read_project_env):
        get_container.return_value = {
            "container": self.shop,
            "stats": {"cpu_percent": 0, "mem_usage_mb": 1, "mem_limit_mb": 2},
        }
        EnvRevision.objects.create(
            project=self.project,
            container_name="shop-web",
            body="TOKEN=sk-live-aaaaaa\nGONE=1\n",
            created_by=self.owner,
        )
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:container_detail", args=["shop-web"]))
        self.assertContains(response, "File now")
        self.assertContains(response, "shop/.env")
        self.assertContains(response, "TOKEN=sk-live-zzzzzz")
        self.assertContains(response, "env-changed")
        self.assertContains(response, "env-added")
        self.assertContains(response, "env-removed")
        self.assertContains(response, "sk-…zzzz")
        self.assertContains(response, 'data-value="sk-live-zzzzzz"')
        self.assertContains(response, "ada")
        self.assertContains(response, "shop-web")
        read_project_env.assert_called_once_with("shop/.env")
        get_logs.assert_called()

    @patch("monitor.views.agent_api.read_project_env", return_value=("TOKEN=sk-live-zzzzzz\n", False))
    @patch("monitor.views.agent_api.get_logs", return_value="")
    @patch("monitor.views.agent_api.get_container")
    def test_compose_service_opens_the_stack_env(self, get_container, get_logs, read_project_env):
        get_container.return_value = {
            "container": {**CONTAINER, "compose_project": "voip", "env_path": "VOIP/.env"},
            "stats": {"cpu_percent": 0, "mem_usage_mb": 1, "mem_limit_mb": 2},
        }
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:container_detail", args=["shop-web"]))
        self.assertContains(response, "VOIP/.env")
        self.assertContains(response, "shared by every service in voip")
        self.assertContains(response, "TOKEN=sk-live-zzzzzz")
        read_project_env.assert_called_once_with("VOIP/.env")
        get_logs.assert_called()

    @patch("monitor.views.agent_api.read_project_env", return_value=("", True))
    @patch("monitor.views.agent_api.get_logs", return_value="")
    @patch("monitor.views.agent_api.get_container")
    def test_other_project_label_does_not_open_the_file(self, get_container, get_logs, read_project_env):
        get_container.return_value = {"container": {**CONTAINER, "project": "other"}, "stats": {}}
        self.client.force_login(self.owner)
        response = self.client.get(reverse("monitor:container_detail", args=["shop-web"]))
        self.assertContains(response, "No project matches")
        read_project_env.assert_not_called()
        get_logs.assert_called()

    @patch("monitor.views.agent_api.read_project_env", return_value=("API_TOKEN=super-secret-value\n", False))
    @patch("monitor.views.agent_api.write_project_env")
    @patch("monitor.views.agent_api.get_logs", return_value="")
    @patch("monitor.views.agent_api.get_container")
    def test_save_env_copy(self, get_container, get_logs, write_project_env, read_project_env):
        get_container.return_value = {"container": self.shop, "stats": {}}
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("monitor:env_save", args=["shop-web"]),
            {"body": "API_TOKEN=super-secret-value\n"},
            follow=True,
        )
        self.assertContains(response, "Saved shop/.env.")
        saved = EnvRevision.objects.get(container_name="shop-web")
        self.assertEqual(saved.created_by, self.owner)
        self.assertEqual(saved.project, self.project)
        self.assertContains(response, "sup…alue")
        self.assertContains(response, "ada")
        self.assertNotContains(response, ">super-secret-value<")
        write_project_env.assert_called_once_with("shop/.env", "API_TOKEN=super-secret-value\n")
        get_logs.assert_called()

    @patch("monitor.views.agent_api.read_project_env", return_value=("API_TOKEN=super-secret-value\n", False))
    @patch("monitor.views.agent_api.write_project_env")
    @patch("monitor.views.agent_api.get_logs", return_value="")
    @patch("monitor.views.agent_api.get_container")
    def test_save_compose_env_records_the_service(self, get_container, get_logs, write_project_env, read_project_env):
        get_container.return_value = {
            "container": {**CONTAINER, "name": "shop-web", "compose_project": "voip", "env_path": "VOIP/.env"},
            "stats": {},
        }
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("monitor:env_save", args=["shop-web"]),
            {"body": "API_TOKEN=super-secret-value\n"},
            follow=True,
        )
        self.assertContains(response, "Saved VOIP/.env.")
        saved = EnvRevision.objects.get()
        self.assertEqual(saved.created_by, self.owner)
        self.assertEqual(saved.compose_project, "voip")
        self.assertEqual(saved.container_name, "shop-web")
        self.assertEqual(saved.env_path, "VOIP/.env")
        self.assertIsNone(saved.project)
        write_project_env.assert_called_once_with("VOIP/.env", "API_TOKEN=super-secret-value\n")
        get_logs.assert_called()

    def test_project_rejects_parent_path(self):
        self.project.env_path = "../secret.env"
        with self.assertRaises(ValidationError):
            self.project.full_clean()

    def test_other_user_cannot_save_env(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("monitor:env_save", args=["shop-web"]),
            {"body": "API_TOKEN=super-secret-value\n"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(EnvRevision.objects.count(), 0)

    @patch("monitor.views.explain_chunks", return_value=iter(["ok"]))
    @patch("monitor.views.agent_api.get_container")
    @patch("monitor.views.agent_api.get_logs", return_value="startup failed")
    @override_settings(LLM_API_TOKEN="", LLM_API_BASE="http://192.168.0.200:8880/v1")
    def test_explain_does_not_receive_saved_env_values(self, get_logs, get_container, explain_chunks):
        get_container.return_value = {"container": CONTAINER, "stats": {}}
        EnvRevision.objects.create(
            container_name="shop-web",
            body="API_TOKEN=super-secret-value\n",
            created_by=self.owner,
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse("monitor:explain_submit", args=["shop-web"]))
        body = b"".join(response.streaming_content).decode()
        self.assertNotIn("super-secret-value", body)
        self.assertNotIn("super-secret-value", json.dumps(explain_chunks.call_args.kwargs))
        get_logs.assert_called_once()
