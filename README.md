# Parity Lab · 期权反推价格监测台

可运行的本地金融研究工作台。Next.js / React / TypeScript / Tailwind / shadcn 风格 Button + FastAPI / Pydantic / SQLAlchemy / SQLite + 独立 Python worker。所有定价、均值、价差与统计由 Python 计算。没有交易接口。

## 启动

换电脑运行请先阅读 [另一台电脑运行说明](docs/另一台电脑运行.md)，包含安装、富途登录、空数据库及 Windows/WSL 注意事项。

需要 Node.js 20.9+、Python 3.12+（本次验证为 macOS arm64、Node 24、Python 3.14）。首次运行需联网安装依赖。

```bash
cd /path/to/option-monitor
./scripts/dev.sh
```

也可双击 `启动监测台.command`。打开终端打印的地址，默认 http://127.0.0.1:3000 。API 文档默认 http://127.0.0.1:8000/docs 。脚本自动建立虚拟环境、安装缺少的依赖、执行数据库迁移、启动前端/API/单个 worker。Ctrl+C 清理所有所属子进程。默认端口占用时自动尝试3100/8100等空闲端口，不停止其他应用。也可明确指定：

```bash
FRONTEND_PORT=3100 BACKEND_PORT=8100 GATEWAY_PORT=8110 ./scripts/dev.sh --gateway
```

`--gateway` 额外启动本地 MOCK 参考网关。本次开发预览使用上面这组端口。API 和 worker 不支持热更新；修改 Python 后 Ctrl+C 重启。

可复制 `.env.example` 为 `.env`，设置上述端口。密钥仅使用 `QUOTE_GATEWAY_KEY` 后端环境变量，前端不读取它。

## 开始使用

1. 点击「新增标的」，选择富途 OpenD / 人工 / HTTP。初始低侧、高侧均为 **0**，需要明确设置数量。可以只选一侧。
2. 人工模式进入「监测配置」，选择时间规则：合约时间戳，或 Excel 剩余天数。人工页输入带时区的事件时刻、正股 Last/Bid/Ask、合约到期日和逐档报价，点击「计算并保存快照」。编辑草稿不存历史。
3. 原始公式是 `Call − Put + K × exp(−rT)`，权利金为每股单位。默认5%是人工示例假设，内部保存0.05。利率、期限、报价口径均可修改。
4. 报价按钮是**同一历史快照重算预览**，不增加统计样本。点击「设为主监测口径」或保存配置才开启新版本。高级设置可以独立选择 Call/Put；正股对照口径独立，选档永远使用正股 Last。
5. MOCK 流由后台10秒目标调度采集。多个浏览器不会产生更多 worker。每轮按 Last 重新选档，近档缺腿不替换成远档。
6. 「历史与追溯」可以查看原始输入、导出完整 JSON 审计或所有模式 CSV。「锁定当前研究观察」固定原始报价、配置和结果；后续结果使用独立追加接口。
7. 不想继续采集时，在配置中选择「暂停采集」。停用监测对象保留所有历史。

## 可选演示数据

在空数据库执行迁移后（也可以先运行启动脚本），另开终端：

```bash
.venv/bin/python scripts/seed_demo.py --history
```

只在没有任何监测对象时生效，避免重复。创建 SPY/QQQ MOCK、EXCEL 人工样例、未配置 HTTP，以及独立的 `SIM-HISTORY` 模拟历史流。演示明确设置了非零档数，普通新建仍默认为0。

`SIM-HISTORY` 用固定种子生成35个完整分钟、每分钟4个槽，关闭自动采集，标记 historical + MOCK。它只用于检验统计链路，不是35分钟实盘或前向验证。窗口以当前时间向后查连续分钟，历史数据停止后出现缺口，当前统计样本会自然归零；旧统计仍保存在审计记录中。演示数据库不包含在源码压缩包中。

## 接富途 OpenD

本机打开富途 OpenD 并登录，默认 `127.0.0.1:11111`。页面点击「连接富途」，新建独立监测，配置页点击「测试行情连接」。需要 OpenAPI 美股正股及 OPRA 期权权限；程序不保管登录密码。详细步骤与时间戳限制见 [富途接入说明](docs/FUTU.md)。

## 接本地参考网关

配置 `.env`：

```dotenv
QUOTE_GATEWAY_URL=http://127.0.0.1:8010
QUOTE_GATEWAY_KEY=
```

然后 `./scripts/dev.sh --gateway`。若改用8110，网关URL也改为8110。新建 HTTP 监测并设置数量，连接测试返回 MOCK，永远不会标成 REALTIME。

这是本项目定义的 HTTP 协议，不声称任意券商原生兼容。真实供应商应实现协议映射或替换 Python Provider，计算内核无需修改。未设置 URL 显示 `NOT_CONFIGURED`。不需要购买 API 就能运行人工和 MOCK 模式。

## 验证

```bash
cd backend
../.venv/bin/python -m pytest -q
cd ../frontend
npm ci
npm run typecheck
npm run build
npm audit
```

后端测试使用临时数据库，不读写演示数据库。具体已运行结果见 `docs/VALIDATION.md`。

## 结构

- `backend/app/engine.py`：纯取价、Decimal 选档、配对、期限、质量、汇总。
- `backend/app/statistics.py`：完成分钟、4/6槽覆盖、连续窗口、HAC、Holm。
- `backend/app/models.py`：统一 Pydantic 合约、报价与配置模型。
- `backend/app/db.py` / `migrations/`：SQLite WAL、15秒 busy timeout、不可变审计表触发器。
- `backend/app/providers.py` / `gateway.py`：异步 Provider、MOCK、HTTP 分页/批量和参考网关。
- `backend/app/worker.py`：单进程锁、单监测防重入、并发限制、10秒目标节拍、心跳、错误隔离和退避。
- `backend/app/api.py` / `service.py`：版本、快照、历史、导入导出、冻结观察、数据库 outbox 与 API 统一 SSE。
- `frontend/`：中文研究界面。Next.js 仅转发同源 `/api`；不访问供应商。
- `samples/`：CSV、TSV、JSON 与模拟历史生成器说明。
- `scripts/dev.py`：macOS 进程组管理器。

## 使用边界

- 已接入富途 OpenD 快照 Provider（21个美股 / ETF，列表见 docs/FUTU.md）；正股连接和权限已实测。期权权限与最新连通状态见 `docs/FUTU.md`，不能以购买成功代替 API 行情核验。
- 真实交易所日历由 exchange_calendars 提供；正股常规时段还必须与 Provider 的期权可交易时段取交集。缺少元数据不能证明可交易。MOCK 使用明确的合成交易时段。
- 无当天到期合约不自动滚到下一期限。第一版拒绝调整合约及 PER_CONTRACT 单位；供应商应先正确映射到每股标准合约。
- 时间戳规则要求合约的 pricingAt，或明确的配置假设；不把所有合约统一设置成纽约16:00。人工网格的定价/最后交易时刻是用户明确输入，真实供应商需要分别提供完整元数据。
- 原始可计算值保留；时效/同步/来源不合格的值不得进入 validatedMean 或严格统计。统计只使用所有请求档位齐全的时点。延迟/未知/示意行情不进入严格统计。
- 未配置自动复权、未知股息补齐、真实利率、实际交易费、滑点或提前行权模型；美式合约显示简化平价近似。股息现值作为独立修正输出，不代替基础输出。
- SQLite 本地单 worker / 单 API 实例部署；仅绑定回环地址。此版本没有公网账户鉴权，不应直接暴露到公网。
- 原始数据追加保留，长时间运行数据库会增长；备份时使用 SQLite backup API，不能只复制运行中的主文件忽略 WAL。

## 内置模拟盘

左侧「模拟盘」支持 $200,000 总本金、正股/期权各 $100,000 的独立历史回放。包含四分之一凯利、止盈止损、资金与盘口数量限制、严格核验/研究假设两个结果及逐笔审计。它不提交券商订单，也没有启动前瞻自动交易；详见 [模拟盘规则与复现](docs/PAPER_TRADING.md)。
