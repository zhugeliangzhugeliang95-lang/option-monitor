"""One logging setup (stdout, LOG_LEVEL env) plus secret redaction for every handler we can reach.

Never log credentials, env values, full config payloads or provider request params.
Redaction is a last line of defence applied to the *final formatted line* of each handler
(message after %-args interpolation, exc_info traceback and stack_info included).

Covered loggers: root, 'app' tree, 'futu' plus the Futu SDK's own 'FTFileLog'/'FTConsoleLog'
(propagate=False, own file + console handlers), 'uvicorn', 'uvicorn.error', 'uvicorn.access'.
"""
import functools, logging, os, re, sys

FORMAT='%(asctime)s %(levelname)s %(name)s %(message)s'
COVERED_LOGGERS=('', 'app', 'futu', 'FTFileLog', 'FTConsoleLog', 'uvicorn', 'uvicorn.error', 'uvicorn.access')
MASK='***'

# --- key-name classification (token-aware; split on _ - . whitespace and camelCase) ---
# Sensitive words are matched against whole tokens or concatenations of adjacent tokens, so
# PassWord / pass_word -> "password", FUTU_ACC_ID -> "acc"+"id", but passport/compass/bypass,
# monkey, keys, keyboard are single tokens that are not in the lists and stay visible.
_ALWAYS={'password','passwd','passphrase','pass','passcode','pwd','pw','otp','auth','token','secret','authorization','cookie',
        'apikey','credential','credentials','privatekey','secretkey','accesskey'}
_ACCOUNT={'account','acc','acct'}
_ACCOUNT_SUFFIX={'id','no','num','number'}
_KEY_PREFIX={'api','secret','private','access','auth','client','signing','encryption','session',
             'rsa','dsa','ec','ssh','pem','gpg','pgp','hmac','jwt','master','app','sign'}

def _tokens(name):
    name=re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',name)
    name=re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1_\2',name)
    return [t for t in re.split(r'[_\-.\s]+',name.lower()) if t]

def _spans(t):
    for i in range(len(t)):
        for j in range(i+1,min(len(t),i+3)+1): yield i,j,''.join(t[i:j])

@functools.lru_cache(maxsize=4096)
def sensitive_name(name):
    t=_tokens(name)
    if not t: return False
    if any(w in _ALWAYS for _,_,w in _spans(t)): return True
    for i,w in enumerate(t):
        if w=='key' and (len(t)==1 or any(x in _KEY_PREFIX for x in t[:i])): return True
        if w in _ACCOUNT and (i==len(t)-1 or t[i+1] in _ACCOUNT_SUFFIX): return True
        if w in ('session','sessionid','sid') and (len(t)==1 or i+1<len(t) and t[i+1] in ('id','cookie')): return True
        if w=='user' and i+1<len(t) and t[i+1]=='id': return True
    return any(w in ('accountid','accid','acctid','userid','accno') for _,_,w in _spans(t))

# Unquoted values never contain '=' so 'ValueError: password=...' cannot swallow the next key.
# Quoted values honour backslash escapes ('hun\\'ter2') and run to end of text when unterminated
# (e.g. cut by truncation), so no tail of a quoted secret is left visible.
_VALUE=r"""(?:'(?:[^'\\]|\\.)*(?:'|\\?\Z)|"(?:[^"\\]|\\.)*(?:"|\\?\Z)|[^\s,;&)}\]'"=]{1,256})"""
_VALUE_RUN=re.compile(r"""[^\s,;&)}\]'"=]*""")   # rest of an unquoted value longer than the 256 bound
# Names may not start right after another name character (incl. '.'), so the scan is linear:
# no restart after every '.' or '-' of a long token (was quadratic on long lines).
# Names are unbounded (possessive: no backtracking) but only their LAST 64 chars are classified
# (NAME_TAIL), so 'very.long.dotted.path...PASSWORD' is still caught and the scan stays linear.
# Fast pre-check inside the regex engine: a name is only a candidate if, ignoring _ . -
# separators, it contains one of these words (a superset of what sensitive_name() can accept), so
# the thousands of harmless 'a=' / 'a:' pairs in a big record never reach Python code.
_SENS_WORDS=('pass','pw','otp','auth','token','secret','cookie','key','cred','acc','sess','sid','user')
_SENS_ANY='(?i:'+'|'.join('[_.-]*'.join(w) for w in _SENS_WORDS)+')'
_NAME=r'(?<![\w.-])(?=[\w.-]*?'+_SENS_ANY+r')[A-Za-z_][\w.-]*+'
NAME_TAIL=64
MAX_LINE=8192    # longer lines are truncated (tail dropped) BEFORE redaction
MAX_TOTAL=65536  # whole-record cap: the tail beyond it is dropped BEFORE redaction
def _maybe_sensitive(name):
    return sensitive_name(name[-NAME_TAIL:])
_AUTH_SCHEME=r"(?:(bearer|basic|token|digest|negotiate|ntlm|hmac[\w-]*)\s+)?"
# quoted repr ('Authorization': 'Digest ...'): mask up to the closing quote
_AUTH_Q=re.compile(r"""(?i)(['"](?:proxy-)?authorization['"]\s*[:=]\s*(['"]))"""+_AUTH_SCHEME+r"""(?:(?!\2).)+""")
# header style (Authorization: Digest username="x", response="y"): mask the rest of the line
_AUTH=re.compile(r"""(?i)(\b(?:proxy-)?authorization\s*[:=]\s*)"""+_AUTH_SCHEME+r"""[^\r\n]+""")
_KV=re.compile(r"""(?<![\w.-])(['"]?)("""+_NAME+r""")\1(\s*(?:=>|[:=])\s*)("""+_VALUE+')')
_EQ=re.compile(r"""("""+_NAME+r""")=([^&\s,;)'"}\]]+)""")  # also inside quoted strings (URLs)
_SPACED=re.compile(r"""(?i)\b(password|passwd|pwd|pw|token|secret)(\s+)(?!=|:)([^\s,;'"]+)""")
_ACCOUNT_NUM=re.compile(r"""(?i)\b(acc(?:ount)?|acct)((?:[_-]?(?:id|no|number))?['"]?[\s:=#'"]*)(\d{4,})""")
_SCHEME_TOKEN=re.compile(r'(?i)\b(bearer|basic)\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{6,}')
_URL_CREDS=re.compile(r'(?i)(\b[a-z][a-z0-9+.-]{0,15}://)[^/\s:@]{0,256}:[^/\s@]{1,256}@')
# Applied only to a head cut by truncation: `scheme://user:pa` with the `@` dropped must not leak the
# password prefix. Masked to the end of the head (not applied to untruncated text, so a plain
# `http://host:8080` at the end of a message stays readable).
_URL_CREDS_CUT=re.compile(r'(?i)(\b[a-z][a-z0-9+.-]{0,15}://)[^/\s:@]{0,256}:[^/\s@]*\Z')
def _cut(head):
    return _URL_CREDS_CUT.sub(lambda m: m.group(1)+MASK,head)

def _kv_scan(text):
    """Mask sensitive `name<sep>value`. If the name is not sensitive, only the name is consumed and
    scanning resumes right after it, so `ValueError: password='x'` still reaches `password`."""
    out,pos=[],0
    while True:
        m=_KV.search(text,pos)
        if not m: out.append(text[pos:]); return ''.join(out)
        q,name,sep,val=m.groups()
        if not val.strip('\'"').startswith(MASK) and _maybe_sensitive(name):
            masked=f'{val[0]}{MASK}{val[0]}' if val[:1] in '\'"' else MASK
            end=m.end() if val[:1] in '\'"' else _VALUE_RUN.match(text,m.end()).end()
            out.append(text[pos:m.start()]+f'{q}{name}{q}{sep}{masked}'); pos=end
        else:
            out.append(text[pos:m.end(2)]); pos=m.end(2)

def redact(text):
    """Mask secrets. Order is TRUNCATE THEN REDACT: a record longer than MAX_TOTAL keeps only its
    head and an over-long line (rare) keeps only its first MAX_LINE chars; the dropped tails are
    never emitted, so they cannot leak. The kept text is redacted and a visible
    '...[truncated N chars]' marker appended. A value cut at a boundary (quoted or not) is masked
    to the end of the kept head."""
    text=str(text); cut=''
    if len(text)>MAX_TOTAL:
        cut=f'...[truncated {len(text)-MAX_TOTAL} chars]'; text=_cut(text[:MAX_TOTAL])
    return _redact_lines(text)+cut

def _redact_lines(text):
    if len(text)>MAX_LINE and any(len(l)>MAX_LINE for l in text.split('\n')):
        return '\n'.join(_redact(l) if len(l)<=MAX_LINE else _redact(_cut(l[:MAX_LINE]))+f'...[truncated {len(l)-MAX_LINE} chars]'
                         for l in text.split('\n'))
    return _redact(text)

def _redact(text):
    text=_AUTH_Q.sub(lambda m: m.group(1)+(m.group(3)+' ' if m.group(3) else '')+MASK,text)
    text=_AUTH.sub(lambda m: m.group(0) if m.group(0).rstrip().endswith(MASK+"'") or m.group(0).rstrip().endswith(MASK+'"') else m.group(1)+(m.group(2)+' ' if m.group(2) else '')+MASK,text)
    text=_URL_CREDS.sub(r'\1***:***@',text)
    text=_EQ.sub(lambda m: m.group(1)+'='+MASK if _maybe_sensitive(m.group(1)) and m.group(2)!=MASK else m.group(0),text)
    text=_kv_scan(text)
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
    """Idempotent: one stdout handler on 'app'; one on root only if root has no handler at all
    (existing root handlers are kept and wrapped, never duplicated); then a redacting formatter
    on every handler of every covered logger. Calling it twice adds nothing."""
    app=logging.getLogger('app')
    level=(level or os.getenv('LOG_LEVEL','INFO')).upper()
    app.setLevel(level if level in logging._nameToLevel else 'INFO')
    if not any(getattr(h,'_app_handler',False) for h in app.handlers):
        h=logging.StreamHandler(stream or sys.stdout); h._app_handler=True
        app.addHandler(h); app.propagate=False
    root=logging.getLogger()
    if not root.handlers:   # never add a duplicate: existing root handlers are wrapped below instead
        h=logging.StreamHandler(stream or sys.stdout); h._app_handler=True; root.addHandler(h)
    try: import futu  # noqa: F401  instantiates the SDK's FTLog handlers so they can be wrapped
    except Exception: pass
    protect_loggers()
    return app
