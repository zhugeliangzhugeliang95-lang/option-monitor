# 前端 E2E（Playwright）

浏览器端回归脚本，跑在本地 dev 栈上（provider 用 MOCK，不连接真实富途）。

| 脚本 | 覆盖 |
|---|---|
| `fb.py` | 新建监测表单：校验、trim、提交。创建 ZZFB 这一步是写操作，**默认跳过**；只有显式设置 `E2E_BASE_URL` 或 `E2E_ALLOW_WRITE=1` 时才执行，结束时用 `DELETE /api/monitors/{id}` 停用本次新建的监测（后端只停用、保留历史） |
| `fb4.py` | 代码输入与保存快照（POST 被拦截并返回 500，所以控制台的 3 条 500 是预期的） |
| `fb5.py` | 代码输入、导入 CSV（`min-import.csv`）、快照（POST 被拦截） |
| `fb6.py` | Futu v5 状态文案、chain 徽标、失败配色（mock `/api/providers/status`、`/api/results/latest`） |
| `fb7.py` | 「过旧快照」阈值（固定页面时钟，mock 读接口） |

**数据依赖**：fb6、fb7 按 symbol 查找监测 id，需要后端里有一个启用中的 `SPY` 监测（MOCK 默认数据里就有）。

## 依赖

```bash
python3 -m venv .venv-e2e   # 已在 .gitignore 中忽略
 && .venv-e2e/bin/pip install playwright && .venv-e2e/bin/playwright install chromium
```

## 起栈（唯一推荐做法：临时数据库 + 独立端口）

后端（API 和 worker）从环境变量 `DATABASE_PATH` 读取数据库路径（`backend/app/db.py`，默认是 `data/monitor.sqlite3`）。测试栈一律指向临时目录，**不要**在开发或生产用的 `data/monitor.sqlite3` 上跑：

```bash
E2E_DB=$(mktemp -d)/monitor.sqlite3
# 可选：用 SQLite 在线备份 API 从现有库只读地拷一份种子数据（fb6、fb7 需要 SPY 监测）
python3 -c "import sqlite3,sys;s=sqlite3.connect('file:data/monitor.sqlite3?mode=ro',uri=True);d=sqlite3.connect(sys.argv[1]);s.backup(d)" "$E2E_DB"
DATABASE_PATH="$E2E_DB" FRONTEND_PORT=3200 BACKEND_PORT=8200 GATEWAY_PORT=8210 ./scripts/dev.sh --gateway
```

等 `curl -sf localhost:3200/api/monitors` 有返回后再跑脚本。跑完删掉临时目录即可，不需要再恢复任何文件。

注意：
- 前端的 `/api` 代理目标在 `next.config.ts` 里由 `BACKEND_PORT` 决定，构建时就写进 routes-manifest。用 `next build` / `next start` 时，**构建前先设置 `BACKEND_PORT`**；不设置会写死成 `127.0.0.1:8000`，e2e 可能打到别人的后端。`scripts/dev.sh` 会自动传入端口。
- 前端不在 3200 时，设置 `E2E_BASE_URL=http://localhost:<端口>`。设置 `E2E_BASE_URL` 同时意味着允许 fb.py 执行写操作，所以只能指向自己的临时栈。

## 运行全部

```bash
for t in fb fb4 fb5 fb6 fb7; do .venv-e2e/bin/python frontend/e2e/$t.py; done   # 每个脚本最后打印 SUMMARY n / m
E2E_ALLOW_WRITE=1 .venv-e2e/bin/python frontend/e2e/fb.py                       # 含创建 ZZFB 的完整 fb
```

截图写在 `frontend/e2e/shots/`（已 gitignore）。脚本本身不写 `data/`。

停栈时按 PID 停本栈的进程（`ss -ltnp | grep -E ':(3200|8200|8210) '`），不要 pkill。
