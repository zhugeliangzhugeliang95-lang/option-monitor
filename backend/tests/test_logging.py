import io, logging
import pytest
from app.logs import configure, redact
from app.models import Config

SECRETS=('hunter2','tok_ABC123','sk-live-999','88001234','pw0rd')

def capture():
    stream=io.StringIO(); root=configure('DEBUG',stream)
    h=[h for h in root.handlers if getattr(h,'_app_handler',False)][0]; h.setStream(stream)
    return stream

def test_redact_patterns():
    text=redact("{'password': 'hunter2', \"token\": \"tok_ABC123\", 'acc_id': 88001234} https://u:pw0rd@h/x?api_key=sk-live-999&symbol=SPY Bearer tok_ABC123")
    assert not any(s in text for s in SECRETS)
    assert 'symbol=SPY' in text and "'password': '***'" in text

def test_exception_with_secret_not_logged():
    stream=capture(); log=logging.getLogger('app.test')
    try: raise RuntimeError({'user':'x','password':'hunter2','trade_pwd':'pw0rd','acc_id':88001234,'url':'http://127.0.0.1:11111/q?token=tok_ABC123'})
    except RuntimeError: log.exception('boom')
    out=stream.getvalue()
    assert 'Traceback' in out and 'RuntimeError' in out and 'boom' in out
    assert not any(s in out for s in SECRETS), out

def test_configure_idempotent():
    configure(); configure()
    assert sum(getattr(h,'_app_handler',False) for h in logging.getLogger('app').handlers)==1

@pytest.mark.asyncio
async def test_worker_catch_all_logs_traceback_without_secret():
    from app.worker import Worker
    from app.service import create_monitor
    from app.db import Session, Monitor, RunConfig, ProviderStatus
    from sqlalchemy import select
    stream=capture(); w=Worker()
    with Session() as db:
        m=create_monitor(db,Config(provider='mock',dataMode='MOCK',nBelow=1)); rc=db.get(RunConfig,m.config_id)
    try:
        await w.collect(m.id,rc,pre_error=ValueError('bad row https://x/y?secret=sk-live-999 password=hunter2'))
        out=stream.getvalue()
        assert 'unexpected collect failure monitor='+m.id in out and 'symbol=SPY' in out and 'Traceback' in out and 'ValueError' in out
        assert not any(s in out for s in SECRETS), out
        with Session() as db:
            st=db.scalars(select(ProviderStatus).where(ProviderStatus.monitor_id==m.id)).all()[-1]
            assert st.payload['status']=='INVALID_PROVIDER_DATA' and st.payload['retryAfter']==10
        from app.providers import ProviderError
        await w.collect(m.id,rc,pre_error=ProviderError('TIMEOUT'))
        assert 'provider error monitor='+m.id in stream.getvalue() and 'status=TIMEOUT' in stream.getvalue()
    finally:
        with Session() as db: db.get(Monitor,m.id).active=False; db.commit()
        for p in w.providers.values(): await p.close()


# --- per-pattern redaction (reviewer list) ---
@pytest.mark.parametrize('text,secret',[
    ('password: hunter2','hunter2'),
    ('PASSWORD = hunter2','hunter2'),
    ("unlock_trade(password='hunter2')",'hunter2'),
    ('pwd hunter2','hunter2'),
    ('pw=hunter2','hunter2'),
    ('account 28001234','28001234'),
    ('acc 28001234','28001234'),
    ('acct: 28001234','28001234'),
    ('account_id=28001234','28001234'),
    ('account-id 28001234','28001234'),
    ('Authorization: token ghp_abcDEF1234567890','ghp_abcDEF1234567890'),
    ('Authorization: Bearer eyJhbGciOi.payload.sig','eyJhbGciOi.payload.sig'),
    ('Authorization: Basic dXNlcjpwYXNz','dXNlcjpwYXNz'),
    ('api_key=sk-live-999','sk-live-999'),
    ('apikey=sk-live-999','sk-live-999'),
    ('secret_key=sk-live-999','sk-live-999'),
    ('private-key=sk-live-999','sk-live-999'),
    ('access_key=sk-live-999','sk-live-999'),
    ('key=sk-live-999','sk-live-999'),
    ('https://u:pw0rd@h/x','pw0rd'),
])
def test_redacts_pattern(text,secret):
    out=redact(text); assert secret not in out and '***' in out, out

@pytest.mark.parametrize('text',['monkey=banana','keys=SPY','keyboard=us','symbol=SPY status=TIMEOUT operation=get_option_chain','account balance unchanged'])
def test_does_not_redact_non_secrets(text):
    assert redact(text)==text

def test_percent_args_and_exc_info_redacted():
    stream=capture(); log=logging.getLogger('app.test')
    log.warning('login %s with %s',"password='hunter2'",'account 28001234')
    try: raise ValueError('pw=pw0rd Authorization: Bearer tok_ABC123')
    except ValueError: log.error('failed %(x)s',{'x':'api_key=sk-live-999'},exc_info=True)
    out=stream.getvalue()
    assert 'Traceback' in out and not any(s in out for s in SECRETS+('28001234',)), out

def _handler_output(logger_name,msg):
    stream=io.StringIO(); lg=logging.getLogger(logger_name)
    h=logging.StreamHandler(stream); h.setFormatter(logging.Formatter('%(levelname)s %(message)s')); lg.addHandler(h)
    try:
        configure()   # wraps the pre-existing handler (as uvicorn/futu install theirs before us)
        assert getattr(h.formatter,'_redacting',False)
        lg.error(msg); return stream.getvalue()
    finally: lg.removeHandler(h)

@pytest.mark.parametrize('name',['futu','FTFileLog','FTConsoleLog','uvicorn','uvicorn.error','uvicorn.access',''])
def test_third_party_logger_handlers_redacted(name):
    out=_handler_output(name,'GET /x?token=tok_ABC123 password: hunter2 account 28001234')
    assert 'GET /x' in out and not any(s in out for s in ('tok_ABC123','hunter2','28001234')), out

def test_futu_sdk_logger_propagates_to_redacting_root():
    stream=io.StringIO(); root=logging.getLogger(); h=logging.StreamHandler(stream); root.addHandler(h)
    try:
        configure(); logging.getLogger('futu.sdk.child').error('unlock password: hunter2')
        assert 'unlock password: ***' in stream.getvalue()
    finally: root.removeHandler(h)


# --- round 2 (review at b8cf37f) ---
@pytest.mark.parametrize('text,secret',[
    ("ValueError: password='hunter2'",'hunter2'),          # blocker 1: <word>: key=value
    ('error: password="hunter2"','hunter2'),
    ("cfg: password='a b'",'a b'),
    ('PassWord: hunter2','hunter2'),                        # camelCase pass+word
    ('pass: hunter2','hunter2'),
    ('url?pass=hunter2','hunter2'),
    ('FUTU_ACC_ID=28001234','28001234'),                    # acc+id at any token position
    ('FUTU_ACCOUNT_NO=28001234','28001234'),
    ('FUTU_RSA_KEY=MIIEvQIBADANBg','MIIEvQIBADANBg'),
    ('rsa_private_key=MIIEvQIBADANBg','MIIEvQIBADANBg'),
    ('Authorization: Digest username=x, response=hunter2','hunter2'),
    ('session_id=abc123def','abc123def'),
])
def test_redacts_round2(text,secret):
    out=redact(text); assert secret not in out and '***' in out, out

def test_digest_whole_credential_masked():
    out=redact('Authorization: Digest username="bob", realm="r", nonce="n1", response="hunter2"')
    assert 'bob' not in out and 'n1' not in out and 'hunter2' not in out, out

@pytest.mark.parametrize('text',['passport=E123','compass: north','bypass=1','password_policy_note','monkey=banana','keys=SPY',
                                 'keyboard=us','account_balance=5','a: b=c','optionSessionOpen=False','ValueError: bad strike=450'])
def test_round2_not_masked(text):
    assert redact(text)==text

def test_exc_info_last_line_key_value_masked():
    stream=capture(); log=logging.getLogger('app.test')
    try: raise ValueError("password='hunter2'")
    except ValueError: log.exception('failed')
    out=stream.getvalue(); last=out.strip().splitlines()[-1]
    assert last=="ValueError: password='***'" and 'hunter2' not in out, out

def test_configure_twice_no_double_logging():
    root=logging.getLogger(); before=list(root.handlers)
    stream=io.StringIO(); h=logging.StreamHandler(stream); root.addHandler(h)
    try:
        configure(); configure()
        assert len(root.handlers)==len(before)+1   # no duplicate root handler when one exists
        logging.getLogger('other.lib').error('once password=hunter2')
        assert stream.getvalue().count('once')==1 and 'hunter2' not in stream.getvalue()
        app=logging.getLogger('app'); n=sum(getattr(x,'_app_handler',False) for x in app.handlers); assert n==1
    finally: root.removeHandler(h)
