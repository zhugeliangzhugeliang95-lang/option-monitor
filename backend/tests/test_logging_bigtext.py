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

# Round 5: URL credentials cut by truncation before the '@' (64KB record cap and 8192 line cap).
URL_FORMS=['https://u:{s}@h/x','http://user:{s}@host:8080/p?q=1','postgresql://admin:{s}@db.local/x',
           'redis://:{s}@r:6379/0','mysql+pymysql://u.name:{s}@h','ftp://u-1:{s}@h',
           "url='https://u:{s}@h/x'",'dsn="amqp://guest:{s}@mq//"','see <https://u:{s}@h>','proxy=socks5://u:{s}@p:1080']

def _no_leak(out):
    for k in range(4,len(SECRET)+1):
        assert SECRET[:k] not in out, (k,out[-120:])

@pytest.mark.parametrize('form',URL_FORMS)
def test_url_creds_no_leak_at_total_cap_multiline(form):
    pre=form.index('{s}')
    for off in range(65500,65541):          # secret start offset, cut lands before/inside/after it
        n=off-pre; pad='a=b\n'*(n//4)+' '*(n%4)   # real multi-line padding: only the 64KB cap applies
        s=pad+form.format(s=SECRET)+'\ntail=1\n'+'a=b\n'*20
        assert len(s)>MAX_TOTAL and s.index(SECRET)==off
        _no_leak(redact(s))

@pytest.mark.parametrize('form',URL_FORMS)
def test_url_creds_no_leak_at_line_cap_multiline(form):
    pre=form.index('{s}')
    for off in range(8170,8201):            # secret start offset within one over-long line
        n=off-pre; line='q '*(n//2)+' '*(n%2)+form.format(s=SECRET)+' more'*10
        s='a=b\n'*50+line+'\nnext=1\n'+'a=b\n'*50
        assert line.index(SECRET)==off and len(line)>logs.MAX_LINE
        out=redact(s); _no_leak(out); assert 'next=1' in out

def test_url_cut_marker_and_untruncated_unchanged():
    s='a=b\n'*16380+'https://u:hunter2SECRETzz@h/x'
    out=redact(s)
    assert 'https://'+MASK in out and '[truncated' in out
    assert redact('connect http://127.0.0.1:11111')=='connect http://127.0.0.1:11111'
    assert redact('redis://:hunter2SECRETzz@r:6379')=='redis://***:***@r:6379'

@pytest.mark.parametrize('form',URL_FORMS)
def test_url_boundary_fast(form):
    s='a=b\n'*16376+form.format(s=SECRET)+'\n'+'a=b\n'*20000
    assert best_ms(s)<50
    s=('q '*4090+form.format(s=SECRET)+'\n')*13
    assert best_ms(s)<50
