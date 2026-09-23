def format_docker_time(value):
    if not value or str(value).startswith("0001"):
        return "—"
    text = str(value).replace("T", " ").replace("Z", "")
    return text[:19] + " UTC"


def tone(status, health):
    if health == "unhealthy" or status == "restarting":
        return "warn"
    if status == "running":
        return "ok"
    return "bad"


def present_container(row):
    item = dict(row)
    item["tone"] = tone(item.get("status"), item.get("health"))
    item["started_display"] = format_docker_time(item.get("started_at"))
    item["finished_display"] = format_docker_time(item.get("finished_at"))
    item["health_display"] = item.get("health") or "none"
    item["oom_display"] = "yes" if item.get("oom_killed") else "no"
    return item
