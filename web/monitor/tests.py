import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from monitor.models import EnvName, NoteEnv, Project, UserContainer, set_user_containers
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

    def test_explain_without_local_model_shows_setup(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("monitor:explain_submit", args=["shop-web"]), follow=True)
        self.assertContains(response, "LLM_API_BASE")

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
    @patch("monitor.services.llm.httpx.post")
    def test_local_qwen_payload(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": "The process exited."}}]}
        post.return_value = response
        answer = explain(
            status={"status": "exited", "exit_code": 1},
            logs="startup failed token=super-secret-token",
            dockerfile="FROM python\n",
            env_names=[{"name": "DATABASE_URL", "state": "set", "value": "should-not-leave"}],
        )
        self.assertEqual(answer, "The process exited.")
        body = post.call_args.kwargs["json"]
        blob = json.dumps(body)
        self.assertNotIn("super-secret-token", blob)
        self.assertNotIn("should-not-leave", blob)
        self.assertEqual(body["model"], "qwen")
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["max_tokens"], 1200)
        self.assertEqual(body["effort"], "low")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
