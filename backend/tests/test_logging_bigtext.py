"""Round 4: multi-line big records stay fast, total-length cap, long-name tail, no leaks at boundaries."""
import time
import pytest
from app import logs
from app.logs import redact, MAX_TOTAL, MASK

SECRET='hunter2SECRETzz'

def best_ms(s):
    best=1e9
    for _ in range(3):
        t=time.perf_counter(); redact(s); best=min(best,time.perf_counter()-t)
    return best*1000

BIG=[
    ('a='*4000+'\n')*13, ('a:'*4000+'\n')*13, ('x.y='*2000+'\n')*13,          # reviewer cases (~104KB)
    ('a='*4000+'\n')*130,                                                    # ~1MB
    ('a:b '*2000+'\n')*13, ('a.'*4000+'\n')*13, ('a=b&'*2000+'\n')*13,
    ('pass=x '*1100+'\n')*13, ('password='*900+'\n')*120, ('pw:'*2600+'\n')*40,
    ("password='"*800+'\n')*13, ('acc_id=1234 '*700+'\n')*13, ('key:'*2000+'\n')*13,
    ''.join(f'pass_{i}=x ' for i in range(15000)), ''.join(f'user_id_{i}:x\n' for i in range(9000)),
    ('x'*8000+'\n')*130, ('"token": "abc", '*500+'\n')*20,
]

@pytest.mark.parametrize('s',BIG,ids=[f'big{i}' for i in range(len(BIG))])
def test_big_multiline_fast(s):
    assert len(s)>=100_000
    assert best_ms(s)<50

def test_total_cap_marker():
    s=('a=b\n')*30000
    out=redact(s)
    assert out.endswith(f'...[truncated {len(s)-MAX_TOTAL} chars]')
    assert len(out)<=MAX_TOTAL+64

FORMS=['password={s}','password="{s}"',"password='{s}'",'"password": "{s}"','token: {s}',
       'Authorization: Bearer {s}','https://u:{s}@h/x','api_key => {s}','secret {s}']

@pytest.mark.parametrize('form',FORMS)
@pytest.mark.parametrize('back',range(1,40,3))
def test_no_leak_at_total_cap(form,back):
    line=form.format(s=SECRET)
    pad=('a=b ' * (MAX_TOTAL//4+10))[:MAX_TOTAL-back]
    out=redact(pad.replace(' ','\n',3)+line+' tail=1')   # a few line breaks: line splitting path too
    for k in range(4,len(SECRET)+1):
        assert SECRET[:k] not in out

@pytest.mark.parametrize('form',['password="{a}\n{b}"',"password='{a}\n{b}'",'{{"token": "{a}\n{b}"}}'])
def test_no_leak_across_line_break(form):
    out=redact('x=1\n'*5+form.format(a='hun',b='ter2abc')+'\nnext=1')
    assert 'hun' not in out and 'ter2abc' not in out and 'next=1' in out

@pytest.mark.parametrize('n',[64,65,70,100,300])
def test_long_name_tail(n):
    name=('x'*n)[:n-len('_PASSWORD')]+'_PASSWORD'
    assert len(name)==n
    out=redact(f'{name}={SECRET} {name}: "{SECRET}"')
    assert SECRET not in out
    dotted=('a.'*n)+'password'
    assert SECRET not in redact(f'{dotted}={SECRET}')

def test_long_name_only_tail_examined():
    # sensitive word only in the head (beyond the last 64 chars) is not treated as sensitive
    name='password_'+'x'*80
    assert redact(f'{name}=visible')==f'{name}=visible'

def test_long_names_linear():
    s=('x'*5000+'_PASSWORD=a ')*12
    assert best_ms(s)<50

def test_truncate_then_redact_documented():
    assert 'TRUNCATE THEN REDACT' in logs.redact.__doc__
