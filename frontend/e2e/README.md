# 前端 E2E（Playwright）

浏览器端回归脚本，跑在本地 dev 栈上（provider 用 MOCK，不连接真实富途）。

| 脚本 | 覆盖 |
|---|---|
| `fb.py` | 新建监测表单：校验、trim、提交。futu `ZZFUTU` 的 422 校验和创建 ZZFB 都是真实 `POST /api/monitors`，**默认跳过**；只有 `E2E_ALLOW_WRITE=1` 时才执行。创建之后的流程包在 `try/finally` 里，无论成功还是中途失败，都会用 `DELETE /api/monitors/{id}` 停用本次新建的监测（运行前已有的监测不动；后端只停用、保留历史）。`E2E_FAULT=manual` 可让「人工报价」一步故意超时，用来验证清理 |
| `fb4.py` | 代码输入与保存快照（POST 在浏览器里被 route 拦截并返回 422 `{"detail":"intercepted"}`，不会到后端；Chromium 对每个拦截请求打一条 `Failed to load resource ... 422`，脚本按来源 URL 识别并单独计数，其余控制台 error 必须为 0） |
| `fb5.py` | 代码输入、导入 CSV（`min-import.csv`）、快照（POST 被拦截） |
| `fb6.py` | Futu v5 状态文案、chain 徽标、失败配色（mock `/api/providers/status`、`/api/results/latest`） |
| `fb7.py` | 「过旧快照」阈值（固定页面时钟，mock 读接口） |

**数据依赖**：fb6、fb7 按 symbol 查找监测 id，需要一个启用中的 `SPY` 监测；fb4 需要 `EXCEL`（人工）监测。空库不会自动有这些数据，需先用 `scripts/seed_demo.py` 写入演示数据（SPY、QQQ 为 MOCK，EXCEL 为人工示例，HTTP；库非空时脚本会拒绝执行）。

## 依赖

```bash
python3 -m venv .venv-e2e   # 已在 .gitignore 中忽略
.venv-e2e/bin/pip install playwright
.venv-e2e/bin/playwright install chromium
```

## 起栈（唯一推荐做法：临时数据库 + 独立端口）

后端（API 和 worker）从环境变量 `DATABASE_PATH` 读取数据库路径（`backend/app/db.py`，默认是 `data/monitor.sqlite3`）。测试栈一律指向临时目录，**不要**在开发或生产用的 `data/monitor.sqlite3` 上跑：

```bash
E2E_DIR=$(mktemp -d -p "$PWD/..")            # 临时目录；用完整个删掉
export DATABASE_PATH="$E2E_DIR/monitor.sqlite3"
(cd backend && ../.venv/bin/python -m alembic upgrade head)   # 建表（dev.sh 启动时也会再跑一次，幂等）
.venv/bin/python scripts/seed_demo.py         # 写入 SPY/QQQ/EXCEL/HTTP 演示监测（fb4/fb6/fb7 需要）
FRONTEND_PORT=3290 BACKEND_PORT=8290 GATEWAY_PORT=8291 ./scripts/dev.sh --gateway
```

端口请选不常用的（示例 3290/8290/8291），避免撞到别人的栈。等 `curl -sf localhost:3290/api/monitors` 有返回后再跑脚本。跑完删掉临时目录即可，不需要再恢复任何文件。

注意：
- 前端的 `/api` 代理目标在 `next.config.ts` 里由 `BACKEND_PORT` 决定，构建时就写进 routes-manifest。用 `next build` / `next start` 时，**构建前先设置 `BACKEND_PORT`**；不设置会写死成 `127.0.0.1:8000`，e2e 可能打到别人的后端。`scripts/dev.sh` 会自动传入端口。
- `E2E_BASE_URL` **必填**，没有默认值；不设置时脚本打印用法并以非 0 退出，不发任何请求。只应指向自己用临时 `DATABASE_PATH` 起的栈。
- `E2E_ALLOW_WRITE=1` 只控制写操作（目前只有 fb.py 的真实 `POST /api/monitors`），与地址无关；不设置时所有脚本都只读（fb4/fb5 的 POST 在浏览器里被拦截，不到后端）。

## 运行全部

```bash
export E2E_BASE_URL=http://localhost:3290
for t in fb fb4 fb5 fb6 fb7; do .venv-e2e/bin/python frontend/e2e/$t.py; done   # 每个脚本最后打印 SUMMARY n / m
E2E_ALLOW_WRITE=1 .venv-e2e/bin/python frontend/e2e/fb.py                       # 含创建 ZZFB 的完整 fb
```

截图写在 `frontend/e2e/shots/`（已 gitignore）。脚本本身不写 `data/`。

停栈时按 PID 停本栈的进程（`ss -ltnp | grep -E ':(3290|8290|8291) '`，`kill <pid>`），不要 pkill/killall；然后 `rm -rf "$E2E_DIR"`。
