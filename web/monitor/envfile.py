from .models import ENV_NAME_RE

MAX_ENV_CHARS = 100_000
MAX_REVISIONS = 30


def parse_env(text):
    values = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENV_NAME_RE.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name] = value
    return values


def mask_value(value):
    if value is None:
        return "—"
    if value == "":
        return "empty"
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}…{value[-4:]}"


def side_lines(file_text, draft_text):
    """Color the file lines missing from the draft, and draft lines that are new."""
    file_lines = (file_text or "").splitlines()
    draft_lines = (draft_text or "").splitlines()
    file_set = set(file_lines)
    draft_set = set(draft_lines)
    left = [
        {"kind": "same" if line in draft_set else "removed", "text": line}
        for line in file_lines
    ]
    right = [
        {"kind": "same" if line in file_set else "added", "text": line}
        for line in draft_lines
    ]
    return left, right


def diff_env(old, new):
    rows = []
    names = sorted(set(old) | set(new))
    for name in names:
        in_old = name in old
        in_new = name in new
        old_value = old.get(name, "")
        new_value = new.get(name, "")
        if in_old and not in_new:
            kind = "removed"
        elif in_new and not in_old:
            kind = "added"
        elif old_value != new_value:
            kind = "changed"
        else:
            kind = "same"
        rows.append(
            {
                "name": name,
                "kind": kind,
                "old_value": old_value if in_old else "",
                "new_value": new_value if in_new else "",
                "old_masked": mask_value(old_value) if in_old else "—",
                "new_masked": mask_value(new_value) if in_new else "—",
            }
        )
    order = {"removed": 0, "changed": 1, "added": 2, "same": 3}
    rows.sort(key=lambda row: (order[row["kind"]], row["name"]))
    return rows
