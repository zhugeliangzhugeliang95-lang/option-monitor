"""One logging setup (stdout, LOG_LEVEL env) plus secret redaction for every handler we can reach.

Never log credentials, env values, full config payloads or provider request params.
Redaction is a last line of defence applied to the *final formatted line* of each handler
(message after %-args interpolation, exc_info traceback and stack_info included).

Covered loggers: root, 'app' tree, 'futu' plus the Futu SDK's own 'FTFileLog'/'FTConsoleLog'
(propagate=False, own file + console handlers), 'uvicorn', 'uvicorn.error', 'uvicorn.access'.
"""
import logging, os, re, sys

FORMAT='%(asctime)s %(levelname)s %(name)s %(message)s'
COVERED_LOGGERS=('', 'app', 'futu', 'FTFileLog', 'FTConsoleLog', 'uvicorn', 'uvicorn.error', 'uvicorn.access')
MASK='***'

# --- key-name classification (token-aware; split on _ - . and camelCase) ---
_ALWAYS={'password','passwd','passphrase','pwd','pw','token','secret','authorization','cookie','session','apikey','credential','credentials'}
_ACCOUNT={'account','acc','acct'}
_KEY_PREFIX={'api','secret','private','access','auth','client','signing','encryption','session'}

def _tokens(name):
    name=re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',name)
    return [t for t in re.split(r'[_\-.\s]+',name.lower()) if t]

def sensitive_name(name):
    t=_tokens(name)
    if not t: return False
    joined=''.join(t)
    if any(x in _ALWAYS for x in t) or joined in ('apikey','accountid','accid','acctid','userid','privatekey','secretkey','accesskey'): return True
    if 'key' in t:   # 'key' only as a whole token: key=, api_key, secret-key; never monkey/keys/keyboard
        i=t.index('key'); return len(t)==1 or i==len(t)-1 and (t[i-1] in _KEY_PREFIX or len(t)==1) or i>0 and t[i-1] in _KEY_PREFIX
    if t[0] in _ACCOUNT and (len(t)==1 or t[1:]==['id'] or t[1:]==['no'] or t[1:]==['number']): return True
    if t==['user','id']: return True
    return False

_VALUE=r'''(?:'[^']*'|"[^"]*"|[^\s,;&)}\]'"]+)'''
_AUTH=re.compile(r'''(?i)(\b(?:proxy-)?authorization['"]?\s*[:=]\s*['"]?)(?:(bearer|basic|token|digest|negotiate)\s+)?[^\s'",;}]+''')
_KV=re.compile(r'''(?<![\w-])(['"]?)([A-Za-z_][\w.-]*)\1(\s*[:=]\s*)('''+_VALUE+')')
_EQ=re.compile(r'''(?<![\w-])([A-Za-z_][\w.-]*)=([^&\s,;)'"}\]]+)''')  # also inside quoted strings (URLs)
_SPACED=re.compile(r'''(?i)\b(password|passwd|pwd|pw|token|secret)(\s+)(?!=|:)([^\s,;'"]+)''')
_ACCOUNT_NUM=re.compile(r'''(?i)\b(acc(?:ount)?|acct)((?:[_-]?(?:id|no|number))?['"]?[\s:=#'"]*)(\d{4,})''')
_SCHEME_TOKEN=re.compile(r'(?i)\b(bearer|basic)\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{6,}')
_URL_CREDS=re.compile(r'(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@')

def _kv(m):
    q,name,sep,val=m.groups()
    if not sensitive_name(name): return m.group(0)
    if val[:1] in '\'"': return f'{q}{name}{q}{sep}{val[0]}{MASK}{val[0]}'
    return f'{q}{name}{q}{sep}{MASK}'

def redact(text):
    text=str(text)
    text=_AUTH.sub(lambda m: m.group(1)+(m.group(2)+' ' if m.group(2) else '')+MASK,text)
    text=_URL_CREDS.sub(r'\1***:***@',text)
    text=_EQ.sub(lambda m: m.group(1)+'='+MASK if sensitive_name(m.group(1)) and m.group(2)!=MASK else m.group(0),text)
    text=_KV.sub(_kv,text)
    text=_SPACED.sub(lambda m: m.group(0) if m.group(3).startswith(MASK) else m.group(1)+m.group(2)+MASK,text)
    text=_ACCOUNT_NUM.sub(r'\1\2'+MASK,text)
    text=_SCHEME_TOKEN.sub(r'\1 '+MASK,text)
    return text

class RedactingFormatter(logging.Formatter):
    """Wraps an inner formatter (keeps uvicorn/futu layouts) and redacts its full output."""
    _redacting=True
    def __init__(self,fmt=FORMAT,inner=None,**kw):
        super().__init__(fmt,**kw); self.inner=inner
    def format(self,record):
        out=self.inner.format(record) if self.inner is not None else super().format(record)
        if record.exc_text: record.exc_text=redact(record.exc_text)   # cached traceback reused by other handlers
        return redact(out)

def protect_handler(h):
    f=h.formatter
    if not getattr(f,'_redacting',False): h.setFormatter(RedactingFormatter(inner=f or logging.Formatter(FORMAT)))
    return h

def protect_loggers(names=COVERED_LOGGERS):
    for n in names:
        for h in logging.getLogger(n or None).handlers: protect_handler(h)

def configure(level=None,stream=None):
    """Idempotent: one stdout handler on 'app' and on root (if root has none), then a redacting
    formatter on every handler of every covered logger."""
    app=logging.getLogger('app')
    level=(level or os.getenv('LOG_LEVEL','INFO')).upper()
    app.setLevel(level if level in logging._nameToLevel else 'INFO')
    if not any(getattr(h,'_app_handler',False) for h in app.handlers):
        h=logging.StreamHandler(stream or sys.stdout); h._app_handler=True
        app.addHandler(h); app.propagate=False
    root=logging.getLogger()
    if not any(getattr(h,'_app_handler',False) for h in root.handlers):
        h=logging.StreamHandler(stream or sys.stdout); h._app_handler=True; root.addHandler(h)
    try: import futu  # noqa: F401  instantiates the SDK's FTLog handlers so they can be wrapped
    except Exception: pass
    protect_loggers()
    return app
