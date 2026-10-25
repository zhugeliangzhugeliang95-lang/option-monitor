"""One logging setup for the worker (stdout, LOG_LEVEL env) plus secret redaction.

Never log credentials, env values, full config payloads or provider request params.
The formatter redacts the *final* line, including tracebacks, as a last line of defence.
"""
import logging, os, re, sys

FORMAT='%(asctime)s %(levelname)s %(name)s %(message)s'
SENSITIVE=r'(?:password|passwd|pwd|token|secret|api[_-]?key|key|account(?:[_-]?id)?|acc[_-]?id|user[_-]?id|authorization|cookie|session)'
_PATTERNS=[
    # 'key': 'value' / "key": 123  (dict / JSON reprs)
    (re.compile(r'''(?i)(['"][\w-]*'''+SENSITIVE+r'''[\w-]*['"]\s*:\s*)(?:'[^']*'|"[^"]*"|[^,}\s]+)'''),r"\1'***'"),
    # key=value (query strings, kwargs reprs, env-style)
    (re.compile(r'(?i)\b([\w-]*'+SENSITIVE+r'[\w-]*)=([^&\s,;)\'"]+)'),r'\1=***'),
    # Authorization: Bearer xxx
    (re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+'),r'\1 ***'),
    # scheme://user:pass@host
    (re.compile(r'(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@'),r'\1***:***@'),
]

def redact(text):
    text=str(text)
    for pattern,repl in _PATTERNS: text=pattern.sub(repl,text)
    return text

class RedactingFormatter(logging.Formatter):
    def format(self,record): return redact(super().format(record))

def configure(level=None,stream=None):
    """Idempotent: installs one stdout handler on the 'app' logger tree."""
    root=logging.getLogger('app')
    level=(level or os.getenv('LOG_LEVEL','INFO')).upper()
    root.setLevel(level if level in logging._nameToLevel else 'INFO')
    if not any(getattr(h,'_app_handler',False) for h in root.handlers):
        h=logging.StreamHandler(stream or sys.stdout); h.setFormatter(RedactingFormatter(FORMAT)); h._app_handler=True
        root.addHandler(h); root.propagate=False
    return root
