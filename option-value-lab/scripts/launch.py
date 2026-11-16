"""Cross-platform local launcher; isolated dependencies and an idempotent server."""
import argparse, hashlib, json, os, signal, socket, subprocess, sys, time, urllib.request, urllib.error, webbrowser
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
PORT = int(os.environ.get('STRIKE_LAB_PORT', '3200'))
URL = f'http://127.0.0.1:{PORT}'
PROJECT_ID = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

def status():
    try:
        with urllib.request.urlopen(URL + '/api/health', timeout=2) as r:
            data = json.load(r)
        return data if data.get('application') == 'option-value-lab' else None
    except (OSError, ValueError):
        return None

def setup():
    if sys.version_info < (3,12):
        raise RuntimeError('需要 Python 3.12 或更新版本。')
    if not PYTHON.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    digest = hashlib.sha256(b''.join((ROOT / p).read_bytes() for p in ('requirements.txt','requirements-futu.txt'))).hexdigest()
    marker = ROOT / '.venv' / '.strike-dependencies'
    if not marker.exists() or marker.read_text() != digest:
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-futu.txt')], check=True)
        marker.write_text(digest)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    current = status()
    if args.status:
        print(json.dumps(current or {'status':'STOPPED'}, ensure_ascii=False))
        return 0 if current else 1
    if current and current.get('projectId') not in (None, PROJECT_ID):
        raise RuntimeError('该端口运行的是另一目录的研究室，请使用它的启动脚本，或修改本程序端口。')
    if args.stop:
        if not current:
            print('研究室未运行。')
            return 0
        pidfile=RUNTIME / 'server.pid'
        if current.get('projectId') != PROJECT_ID or not pidfile.exists() or int(pidfile.read_text()) != current.get('processId'):
            raise RuntimeError('无法确认进程由本目录启动，已取消停止操作。')
        os.kill(current['processId'], signal.SIGTERM)
        for _ in range(40):
            if not status():
                print('研究室已停止。')
                return 0
            time.sleep(.25)
        raise RuntimeError('服务尚未退出，请查看运行日志。')
    if not current:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', PORT)) == 0:
                raise RuntimeError(f'{PORT} 端口被其他程序占用。请设置 STRIKE_LAB_PORT 后重试。')
        setup()
        RUNTIME.mkdir(exist_ok=True)
        options = {'cwd':str(ROOT), 'stdin':subprocess.DEVNULL}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            options['start_new_session'] = True
        with (RUNTIME / 'server.log').open('ab') as log:
            proc = subprocess.Popen([str(PYTHON), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(PORT)], stdout=log, stderr=log, **options)
        (RUNTIME / 'server.pid').write_text(str(proc.pid))
        for _ in range(80):
            if status(): break
            if proc.poll() is not None: raise RuntimeError('启动失败，请查看 runtime/server.log。')
            time.sleep(.25)
        else: raise RuntimeError('启动超时，请查看 runtime/server.log。')
    print(f'执行价研究室已运行：{URL}\n可关闭本启动窗口。行情需要保持 OpenD 登录。')
    if not args.no_browser:
        webbrowser.open(URL)
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        print(f'启动未完成：{error}', file=sys.stderr)
        sys.exit(1)
