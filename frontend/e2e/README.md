# 前端 E2E（Playwright）

浏览器端回归脚本，跑在本地 dev 栈上（provider 用 MOCK，不连接真实富途）。

| 脚本 | 覆盖 |
|---|---|
| `fb.py` | 新建监测表单：校验、trim、提交（**会通过 API 真的创建测试监测 ZZFB**） |
| `fb4.py` | 代码输入与保存快照（POST 被拦截并返回 500，所以控制台的 3 条 500 是预期的） |
| `fb5.py` | 代码输入、导入 CSV（`min-import.csv`）、快照（POST 被拦截） |
| `fb6.py` | Futu v5 状态文案、chain 徽标、失败配色（mock `/api/providers/status`、`/api/results/latest`） |

## 依赖

```bash
python3 -m venv .venv-e2e && .venv-e2e/bin/pip install playwright && .venv-e2e/bin/playwright install chromium
```

## 起栈（在仓库根目录执行，用独立端口，不要占用别人的栈）

```bash
FRONTEND_PORT=3200 BACKEND_PORT=8200 GATEWAY_PORT=8210 ./scripts/dev.sh --gateway
```

等 `curl -sf localhost:3200/api/monitors` 返回后再跑。前端端口不是 3200 时设置 `E2E_BASE_URL=http://localhost:<端口>`。

## 运行全部

```bash
for t in fb fb4 fb5 fb6; do python frontend/e2e/$t.py; done   # 每个脚本最后都会打印 SUMMARY n / m
```

截图写在 `frontend/e2e/shots/`（已 gitignore）。脚本本身不写 `data/`，但 `fb.py` 通过后端 API 创建了监测，所以会改到数据库。

## 恢复数据库

跑之前先备份，跑完、**停栈之后**恢复：

```bash
cp data/monitor.sqlite3 ../monitor.sqlite3.bak                       # 跑之前
rm -f data/monitor.sqlite3-wal data/monitor.sqlite3-shm && cp ../monitor.sqlite3.bak data/monitor.sqlite3 && cmp data/monitor.sqlite3 ../monitor.sqlite3.bak   # 跑完之后
```

另一种做法：设置 `DATABASE_PATH` 给栈单独的数据目录。

停栈时按 PID 停掉本栈的进程（`ss -ltnp | grep -E ':(3200|8200|8210) '`），不要 pkill。
