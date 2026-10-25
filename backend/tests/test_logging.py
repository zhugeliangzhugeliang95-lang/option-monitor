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
