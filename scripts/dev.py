#!/usr/bin/env python3
"""Own process groups; clean up all Next/uvicorn descendants on exit."""
import os, signal, socket, subprocess, sys, time
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]; load_dotenv(ROOT/'.env')
frontend_port=int(os.getenv('FRONTEND_PORT','3000')); backend_port=int(os.getenv('BACKEND_PORT','8000')); gateway_port=int(os.getenv('GATEWAY_PORT','8010'))
children=[]
def stop(*_):
    for p in children:
        try: os.killpg(p.pid,signal.SIGTERM)
        except ProcessLookupError: pass
    deadline=time.monotonic()+6
    for p in children:
        try: p.wait(timeout=max(.1,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            try: os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            p.wait(timeout=2)
    sys.exit(0)
for s in (signal.SIGINT,signal.SIGTERM): signal.signal(s,stop)
def available(port):
    with socket.socket() as probe:
        probe.settimeout(.2)
        if probe.connect_ex(('127.0.0.1',port))==0: return False
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try: sock.bind(('127.0.0.1',port)); return True
        except OSError: return False
def choose(name,default,fallback):
    explicit=os.getenv(name)
    candidates=[int(explicit)] if explicit else [default,*range(fallback,fallback+20)]
    for candidate in candidates:
        if available(candidate): os.environ[name]=str(candidate); return candidate
    raise SystemExit(f'{name}: no available port; stop the previous session or choose a free port.')
frontend_port=choose('FRONTEND_PORT',3000,3100)
backend_port=choose('BACKEND_PORT',8000,8100)
if '--gateway' in sys.argv: gateway_port=choose('GATEWAY_PORT',8010,8110)
subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT/'backend',check=True)
commands=[([sys.executable,'-m','uvicorn','app.api:app','--host','127.0.0.1','--port',str(backend_port)],ROOT/'backend'),([sys.executable,'-m','app.worker'],ROOT/'backend'),(['npm','exec','--','next','dev','--hostname','127.0.0.1','--port',str(frontend_port)],ROOT/'frontend')]
if '--gateway' in sys.argv: commands.append(([sys.executable,'-m','uvicorn','app.gateway:app','--host','127.0.0.1','--port',str(gateway_port)],ROOT/'backend'))
try:
    for cmd,cwd in commands: children.append(subprocess.Popen(cmd,cwd=cwd,start_new_session=True))
    print(f'\n期权反推价格监测台: http://127.0.0.1:{frontend_port}\nAPI: http://127.0.0.1:{backend_port}/docs\nCtrl+C 关闭所有子进程\n',flush=True)
    while all(p.poll() is None for p in children): time.sleep(.5)
finally: stop()
