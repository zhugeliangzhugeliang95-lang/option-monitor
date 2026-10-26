# API与Provider协议

本地FastAPI OpenAPI：`/docs` 和 `/openapi.json`。前端同源`/api`反代到Python。数据时间字段必须带时区；持久报价时间规范化为UTC。错误详情不包括供应商原始响应、密钥或认证请求头。

- `GET /api/stream`: SSE; each event has `id:` = outbox id. Reconnect with `Last-Event-ID` header (wins) or `?lastEventId=` to replay missed events; `event: resync` means refetch state (id pruned/unknown or client too slow); `event: ping` every 10s.

## 研究API

| 方法 / 路径 | 输入或作用 |
|---|---|
| GET /api/monitors | 活跃监测对象、完整配置、版本和hash |
| POST /api/monitors | NewConfig；直接请求默认0/0、MID、nearest、expiryClose、10秒；界面默认发送5/5 |
| PATCH /api/monitors/{id} | Config部分字段；验证完整配置后追加新版本；相同值不增版本 |
| DELETE /api/monitors/{id} | 停用；不删除历史 |
| POST /api/monitors/{id}/refresh | 与定时采集共用下一10秒槽；不突破限流退避；人工返回输入提示 |
| POST /api/monitors/{id}/preview-price-basis | snapshotId和optionPricePreset、callPriceBasis、putPriceBasis、stockReferenceBasis；不写新样本 |
| POST /api/manual-snapshots | Snapshot；provider=manual，quote.feedType=MANUAL |
| POST /api/imports/quotes | monitorId、format(csv/tsv/json)、content、metadata；标记人工历史 |
| GET /api/results/latest | 按monitorId返回最后结果；标记旧配置；`?summary=1` 返回精简字段（id、snapshotId、valuationAt、configId、configVersion、feedType、historical、duplicate、status、stockReference、composition、modes{mode:{rawMean,validatedMean,rawGapBps}}） |
| GET /api/monitors/{id}/history | limit≤500、可选before结果ID；可选after结果ID（返回之后最早的limit条，升序）；`summary=1` 返回同上精简字段；按时序返回 |
| GET /api/providers/status | worker心跳、各监测最近状态、耗时、延迟、退避 |
| POST /api/providers/test | provider=manual/mock/http；未配置返回NOT_CONFIGURED |
| GET /api/stream | SSE；API单广播器读取持久outbox；浏览器重连后重新读取latest/history |

### 新鲜度字段 / Freshness fields（只读，纯加法）

| JSON 路径 | 类型 | 是否总是存在 | 含义 |
|---|---|---|---|
| `GET /api/providers/status` → `cycleSeconds` | 整数 | 总是存在（即使没有 worker 心跳，`worker` 为 `null` 时也返回） | worker 目标采集周期（秒）。与心跳 `worker.targetSeconds` 同一来源（`app/models.py` 的 `CYCLE_SECONDS`，当前 10），不读取心跳；无心跳时即该默认值 |
| `GET /api/results/latest` → `<monitorId>.staleAfterSeconds` | 整数 | 有结果的监测总是存在；`?summary=1` 响应中不出现 | `ceil(max(maxAgeSeconds, 2 × cycleSeconds))`；`maxAgeSeconds` 取产生该结果的配置版本（旧配置结果用旧配置）。默认 maxAgeSeconds=5 时为 20 |

仅供前端判断「历史 / 过旧快照」的显示阈值；不改变 `maxAgeSeconds` 本身、报价质量判定、config hash/configId 或统计窗口。`/api/monitors/{id}/history`、`summary=1` 不变。旧前端可忽略；新前端缺字段时回退 `max(maxAgeSeconds, 20)`。

真实运行示例（临时库、MOCK、`python -m app.worker` 实跑；SPY 监测 maxAgeSeconds=5，节选字段）：

```json
// GET /api/providers/status（节选）
{"cycleSeconds": 10, "worker": {"heartbeatAt": "2026-10-26T12:15:07.645730+00:00", "pid": 280497, "targetSeconds": 10}, "workerStatus": "RUNNING", "monitors": {...}}

// GET /api/results/latest（节选）
{"9263556b78f1423d99402abf85f44069": {"valuationAt": "2026-10-26T12:15:00.041056+00:00", "status": "OK", "anchorStrictEligible": true, "staleAfterSeconds": 20, ...}}
```

无 worker 心跳时：`{"cycleSeconds": 10, "worker": null, "workerStatus": "INTERRUPTED", ...}`；maxAgeSeconds=30 的监测 `staleAfterSeconds` 为 30。

## 自定义HTTP行情协议v1

这是供应商映射层的接口，不是交易接口。`QUOTE_GATEWAY_URL`、`QUOTE_GATEWAY_KEY`只由Python进程读取。后端以`Authorization: Bearer …`发送密钥。

| GET端点 | 参数 | 响应 |
|---|---|---|
| /v1/capabilities | 无 | provider、configured、batchLimit、feedType、protocol |
| /v1/market-state | symbol, calendar | marketOpen、optionSessionOpen、reason |
| /v1/underlyings | ids=ID1,ID2 | {items:{ID:Quote}} |
| /v1/expirations | symbol, timezone, cursor? | {items:[YYYY-MM-DD],nextCursor:null或字符串} |
| /v1/contracts | symbol, expiration, timezone, cursor? | {items:[Contract],nextCursor:…} |
| /v1/quotes | ids=CONTRACT1,CONTRACT2 | {items:{contractId:Quote}} |

Quote、Contract、Capabilities、MarketState均定义于`app/models.py`。Quote保存bid/ask/last及各自事件时间、size、source、feedType、receivedAt、ingestedAt、sequence、unit。Contract需要完整配对规格，pricingAt、lastTradingAt和exerciseCutoffAt互不替代。标准单位PER_SHARE。无法归一化的调整合约不支持。

协议端点状态：401/403权限不足，409未订阅，429限流（支持秒数或HTTP日期Retry-After），其他HTTP错误独立编码；错误正文不透传。HTTP超时7秒，单监测计算任务9秒预算；批量正股请求有独立8秒预算，超时时显示延迟/退避而非承诺10秒。分页上限100页，检测重复cursor。分批使用供应商batchLimit。

## 存储

单一SQLAlchemy ORM管理SQLite。WAL、busy_timeout=15000ms。RunConfig/RawSnapshot/PairResult/AggregateResult有禁止UPDATE/DELETE触发器。Monitor、WorkerState属于可变控制状态，ProviderStatus和Outbox追加。

所有连接（`db.pragmas` 连接监听器，以及 `retention.run_on_path` 自己的 sqlite3 连接）都设置 `PRAGMA journal_size_limit=67108864`（64 MB）。原因：WAL 文件在 checkpoint 后不会自动缩小，其大小由历史上最大的一次事务决定；实测一次保留清理批次（删除 2000 行）就把 WAL 从约 4.2 MB（默认 1000 页自动 checkpoint）撑到 18.4 MB 并一直保持。设置上限后，每次 checkpoint 完成、WAL 重置时会被截断到上限以内。64 MB 远高于正常约 4 MB 的水位，不影响日常写入，但能防止一次异常大事务后磁盘被长期占用。如需立即回收，仍按 `retention.py` 文档在停机后手动执行 `PRAGMA wal_checkpoint(TRUNCATE)`。

worker在保存结果的同一事务写outbox，API负责广播。worker文件锁与数据库路径绑定，第二个worker启动会拒绝。

## Futu 用户可见状态（前端契约）

富途 worker 的状态从三个地方读出：

- **provider_status**：每个监测每轮写一行 `ProviderStatus`。`GET /api/providers/status` 返回它，设置页和历史页显示它。
- **字段**：`status`、`operation`、`durationSeconds`、`retryAfter`、`delayed`、`at`，以及下表中的可选附加字段。
- **聚合结果**：使用缓存（过期）期权链出数时，结果多一个 `chain` 键（见下）。
- **原始快照**：不带这些状态字段，schema 不变。

**"计入告警"的含义**：只是指状态显示为红色（失败），并在 worker 日志里写 WARNING/ERROR 行。本项目**没有任何通知渠道**，不会发邮件、推送或 webhook。

| 状态 | 出现位置 | 类别 | 附加字段 | 前端文案 key | 前端文案（建议） |
|---|---|---|---|---|---|
| `OPEND_OFFLINE` | provider_status | 失败，指数退避 | `operation`（可能为空） | `OPEND_OFFLINE` | OpenD 未启动或无法连接 |
| `OPEND_UNRESPONSIVE` | provider_status | 失败，固定 10 秒重试，不退避，不累计失败次数 | `operation`、`pendingSeconds`、`busyCycles` | `OPEND_UNRESPONSIVE` | OpenD 无响应：请求（{operation}）已卡住 {pendingSeconds} 秒，未重建连接，请检查 OpenD |
| `TIMEOUT` | provider_status | 失败，指数退避 | `operation`（如 `get_market_snapshot`） | `TIMEOUT` | 富途请求超时（{operation}），稍后自动重试 |
| `FUTU_BUSY` | provider_status | 非失败，10 秒重试 | `operation`=`busy`（上一请求未完成，带 `pendingSeconds`）或 `maintenance`（数据维护中） | `FUTU_BUSY` | 富途通道忙：上一请求尚未完成，本轮跳过 |
| `CHAIN_WARMING` | provider_status | 非失败，10 秒重试 | `operation`=`get_option_chain`；`chain`={`state`:`warming`, `reason`:`queued`/`retrying`/`offHours`, `waitingSeconds`, `attempts`, `lastError`:{`code`, `ret`?, `at`}, `droppedReason`?:`MAX_AGE`} | `CHAIN_WARMING`；`reason=offHours` 用 `CHAIN_WARMING_OFF_HOURS` | 期权链加载中（已等待 {waitingSeconds} 秒），稍后出数；offHours：盘外，等待开盘前预热（约 09:00 美东） |
| （正常出数，链为缓存） | provider_status 和聚合结果 | 非失败 | `chain`={`stale`, `ageSeconds`, `fetchedAt`, `refreshFailures`, `expirationsStale`}；链和到期日都新鲜时**不出现** | `CHAIN_STALE` | 使用 {ageSeconds} 秒前的期权链（后台刷新中）；`expirationsStale`：到期日列表为前一交易日 |
| `CHAIN_UNAVAILABLE` | provider_status | 失败，固定 10 秒重试，不退避，不累计失败次数；按标的的拉链退避最长 300 秒 | 同 `CHAIN_WARMING`，`chain.state`=`unavailable` | `CHAIN_UNAVAILABLE` | 期权链长时间无法获取（错误码 {code}），请检查权限或 OpenD |

### JSON 路径与类型

（按 #18 代码核对；示例 JSON 来自 fake OpenD 的真实运行输出，时间均为 UTC ISO 8601 字符串，带 `+00:00`。）

`chain` 有**两种互斥形态**，按 `status` 区分（也可看是否有 `chain.state`）：

**A. 预热/不可用形态**：只在 `status` 为 `CHAIN_WARMING` 或 `CHAIN_UNAVAILABLE` 时出现，只在 provider_status。

- 路径：`GET /api/providers/status` → `monitors.<monitorId>.chain`（`monitors` 是以监测 id 为键的对象，不是数组）。
- 聚合结果里**没有**这一形态（此时没有出数）。

| 字段 | 类型 | 是否总有 |
|---|---|---|
| `chain.state` | string：`warming` / `unavailable` | 总有 |
| `chain.reason` | string：`queued` / `retrying` / `offHours` | 总有 |
| `chain.waitingSeconds` | int（秒；`offHours` 时恒为 0） | 总有 |
| `chain.attempts` | int | 总有 |
| `chain.lastError` | object 或 `null`：{`code`: string, `at`: ISO string, `ret`?: int} | 总有（可为 null；`ret` 可选） |
| `chain.droppedReason` | string：目前只有 `MAX_AGE` | 可选，仅缓存因超龄被丢弃后出现 |

**`offHours` 是 `chain.reason` 的一个取值**，属于 `CHAIN_WARMING` 状态的一个变体（前端文案 key `CHAIN_WARMING_OFF_HOURS`）。它**不是布尔字段，也不是独立的状态码**；`status` 仍是 `CHAIN_WARMING`。

```json
{"status": "CHAIN_WARMING", "operation": "get_option_chain", "retryAfter": 10, "...": "...",
 "chain": {"state": "warming", "reason": "offHours", "waitingSeconds": 0, "attempts": 0, "lastError": null}}
```
```json
"chain": {"state": "warming", "reason": "retrying", "waitingSeconds": 31, "attempts": 2,
          "lastError": {"code": "FUTU_REQUEST_FAILED", "at": "2026-10-23T15:00:31+00:00", "ret": -1}}
```
```json
"chain": {"state": "warming", "reason": "retrying", "waitingSeconds": 0, "attempts": 1,
          "lastError": {"code": "FUTU_REQUEST_FAILED", "at": "2026-10-23T16:32:00+00:00", "ret": -1},
          "droppedReason": "MAX_AGE"}
```

**B. 缓存元数据形态**：正常出数（有快照）但链过了新鲜期，或到期日列表来自前一交易日时出现；链和到期日都新鲜时整个 `chain` 键**不存在**。

- 路径（同一对象，两处）：
  - `GET /api/providers/status` → `monitors.<monitorId>.chain`（`status` 为引擎状态，如 `OK`）；
  - 聚合结果的 `chain` 键：`GET /api/results/latest` → `<monitorId>.chain`；`GET /api/monitors/{mid}/history` → `[].chain`。
  - `summary=true` 的精简结果**不含** `chain`；底层原始快照也不含。

| 字段 | 类型 | 是否总有（在此形态内） |
|---|---|---|
| `chain.stale` | bool（链已过新鲜 TTL、按 SWR 出数时为 true） | 总有 |
| `chain.ageSeconds` | int（距 `fetchedAt` 的秒数） | 总有 |
| `chain.fetchedAt` | ISO 8601 string（UTC） | 总有 |
| `chain.refreshFailures` | int（后台刷新连续失败次数，含本批次的刷新结果：本批刷新失败则已 +1，本批刷新成功则为 0） | 总有 |
| `chain.expirationsStale` | bool（到期日列表为前一交易日） | 总有 |

注意：可能出现 `stale=false` 且 `expirationsStale=true`（链新鲜、到期日列表旧）。

```json
{"status": "OK", "operation": null, "retryAfter": 0, "...": "...",
 "chain": {"stale": true, "ageSeconds": 1853, "fetchedAt": "2026-10-23T15:00:00+00:00",
           "refreshFailures": 0, "expirationsStale": false}}
```

**前端规则：**

- 按 `status` 选文案。
- `{operation}`、`{pendingSeconds}`、`{waitingSeconds}`、`{code}`（取 `chain.lastError.code`）从字段填入。字段缺失就省略对应括号。
- 文案中不写死秒数或端口：阈值来自环境变量，见 `docs/FUTU.md`。
- 未知状态继续回退为显示原文。

**说明：**

- `CHAIN_WARMING` 状态下，`chain.lastError` 只有错误码（如 `RATE_LIMITED`、`PERMISSION_DENIED`），以及富途返回的数字 `ret`（如有）和时间。不包含供应商原文。
- `pendingSeconds` 是当前卡住的请求已持续的秒数（单进程、单调时钟）。
- `busyCycles` 是连续遇到通道忙的批次数。
- `chain.reason=offHours`：预热窗口（交易日 `FUTU_PREWARM_START`～收盘后 15 分钟）之外的当天到期冷链。不计入 `CHAIN_UNAVAILABLE` 的等待上限，`waitingSeconds` 保持 0。
- `chain.droppedReason=MAX_AGE`：缓存链超过最大年龄（当天到期 `FUTU_CHAIN_MAX_AGE` 默认 5400 秒，远期 4 天）被丢弃，按最高优先级重拉。
- `chain.stale` 等元数据只在 provider_status 和聚合结果的 `chain` 键里，不进入原始快照。

## 历史与追溯功能移除（2026-10-27）

追溯专用的 `/api/snapshots/{id}`、`/api/exports/{monitorId}`、`/api/research/frozen-observations` 及后续结果接口已移除（404）。迁移 0005 删除锁定观察及后续结果专用表；不删除原始行情、配置版本、价格序列或统计数据。`/api/monitors/{id}/history` 继续为监测总览的走势图提供数据。

## 统计检验移除（2026-10-27）

统计接口返回 404；latest 不再附带 statistics，worker 不再生成统计批次。迁移 0006 删除 minute_blocks 和 statistical_results。已有不可变配置保留原始内容和 hash；读取时忽略已退役统计字段，新配置不再包含这些字段。报价及质量字段继续保留。
