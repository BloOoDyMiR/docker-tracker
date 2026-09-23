# Docker Track

A small Django portal for container status and logs. The agent lists every container on the Docker host. In Admin, open a user and tick the containers that person can see. Superusers see all of them.

Django does not mount the Docker socket. The `agent` service is the only process that does, and it only answers list, inspect, stats, and logs.

## Run with Docker

```powershell
copy .env.example .env
docker compose up --build
```

Open http://localhost:8000/ and http://localhost:8000/admin/.

The first admin user comes from `DJANGO_SUPERUSER_USERNAME` and `DJANGO_SUPERUSER_PASSWORD` in `.env`.

A demo nginx container named `track-demo` is included. Log in as the superuser and it appears on the dashboard with the other containers on that Docker host.

## Run on the host

```powershell
copy .env.example .env
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -r web\requirements.txt -r agent\requirements.txt
.\.venv\Scripts\python agent\app.py
```

In another terminal, from `web`:

```powershell
..\.venv\Scripts\python manage.py migrate
..\.venv\Scripts\python manage.py createsuperuser
..\.venv\Scripts\python manage.py runserver
```

## LLM

`config/settings.py` reads these from `.env`. Explain sends status, redacted logs, and a stored Dockerfile. For the local Qwen server:

```
LLM_API_BASE=http://192.168.0.200:8880/v1
LLM_API_TOKEN=
LLM_MODEL=qwen
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=1200
LLM_EFFORT=low
```

An empty token is sent without an `Authorization` header. The request also sets `chat_template_kwargs.enable_thinking` to false.

## Admin

Create a user and tick Containers this user can see. The list is every container the agent can see. Superusers are not limited by those ticks.

Optional container notes store a Dockerfile and env names (set or missing, never the value) for the Explain action.
