import re

_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")
_ASSIGN = re.compile(
    r"(?i)([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"authorization|database_url|redis_url|dsn)[A-Za-z0-9_.-]*)(\s*[=:]\s*)(\S+)"
)
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@")


def redact(text):
    if not text:
        return ""
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _JWT.sub("[redacted-jwt]", text)
    text = _ASSIGN.sub(r"\1\2[redacted]", text)
    text = _URL_USERINFO.sub(r"\1[redacted]@", text)
    return text


def redact_obj(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    return value
