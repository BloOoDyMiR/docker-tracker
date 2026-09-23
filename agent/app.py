"""Read-only Docker API for the Django portal.

Django calls this over the private network with AGENT_TOKEN. The browser never
talks to this process, and this process never runs docker exec.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi.responses import JSONResponse

from access import CONTAINER_NAME_RE, cpu_percent, memory_mb, summarize

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def require_token(x_agent_token: str = Header(default="")):
    expected = os.environ.get("AGENT_TOKEN", "")
    if not expected or not x_agent_token or not secrets.compare_digest(x_agent_token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def container_by_name(name: str):
    if not CONTAINER_NAME_RE.fullmatch(name or ""):
        raise HTTPException(status_code=404, detail="Not found")
    client = docker_client()
    try:
        found = client.containers.list(all=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Docker is not available") from exc
    matches = [item for item in found if item.name == name]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="Not found")
    return matches[0]


def docker_client():
    import docker

    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Docker is not available") from exc
    return client


def image_of(container):
    try:
        tags = container.image.tags
    except Exception:
        tags = []
    if tags:
        return tags[0]
    return (container.attrs.get("Config") or {}).get("Image") or ""


def row_for(container):
    return summarize(
        container_id=container.id,
        name=container.name,
        image=image_of(container),
        state=container.attrs.get("State") or {},
    )


def read_stats(container):
    empty = {"cpu_percent": None, "mem_usage_mb": None, "mem_limit_mb": None}
    try:
        raw = container.stats(stream=False)
    except Exception:
        return empty
    usage, limit = memory_mb(raw)
    return {
        "cpu_percent": cpu_percent(raw),
        "mem_usage_mb": usage,
        "mem_limit_mb": limit,
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/containers")
def list_containers(_: None = Depends(require_token)):
    client = docker_client()
    try:
        found = client.containers.list(all=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Docker is not available") from exc
    rows = [row_for(container) for container in found]
    rows.sort(key=lambda row: (row["status"] != "running", row["name"]))
    return {"containers": rows}


@app.get("/container")
def container_detail(
    name: str = Query(min_length=1, max_length=200),
    _: None = Depends(require_token),
):
    container = container_by_name(name)
    return {"container": row_for(container), "stats": read_stats(container)}


@app.get("/container/logs")
def container_logs(
    name: str = Query(min_length=1, max_length=200),
    tail: int = Query(default=200, ge=1, le=500),
    _: None = Depends(require_token),
):
    container = container_by_name(name)
    try:
        raw = container.logs(stdout=True, stderr=True, tail=tail, timestamps=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Docker is not available") from exc
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return JSONResponse({"logs": text})


if __name__ == "__main__":
    if not os.environ.get("AGENT_TOKEN"):
        raise SystemExit("AGENT_TOKEN is required")
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8001")),
    )
