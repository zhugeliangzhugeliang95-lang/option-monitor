# API与Provider协议

本地FastAPI OpenAPI：`/docs` 和 `/openapi.json`。前端同源`/api`反代到Python。数据时间字段必须带时区；持久报价时间规范化为UTC。错误详情不包括供应商原始响应、密钥或认证请求头。

- `GET /api/stream`: SSE; each event has `id:` = outbox id. Reconnect with `Last-Event-ID` header (wins) or `?lastEventId=` to replay missed events; `event: resync` means refetch state (id pruned/unknown or client too slow); `event: ping` every 10s.

## 研究API

| 方法 / 路径 | 输入或作用 |
|---|---|
| GET /api/monitors | 活跃监测对象、完整配置、版本和hash |
| POST /api/monitors | Config；默认0/0、MID、sameDay、10秒 |
| PATCH /api/monitors/{id} | Config部分字段；验证完整配置后追加新版本；相同值不增版本 |
| DELETE /api/monitors/{id} | 停用；不删除历史 |
| POST /api/monitors/{id}/refresh | 与定时采集共用下一10秒槽；不突破限流退避；人工返回输入提示 |
| POST /api/monitors/{id}/preview-price-basis | snapshotId和optionPricePreset、callPriceBasis、putPriceBasis、stockReferenceBasis；不写新样本 |
| POST /api/manual-snapshots | Snapshot；provider=manual，quote.feedType=MANUAL |
| POST /api/imports/quotes | monitorId、format(csv/tsv/json)、content、metadata；标记人工历史 |
| GET /api/results/latest | 按monitorId返回最后结果；标记旧配置；`?summary=1` 返回精简字段（id、snapshotId、valuationAt、configId、configVersion、feedType、historical、duplicate、status、stockReference、composition、modes{mode:{rawMean,validatedMean,rawGapBps}}） |
| GET /api/monitors/{id}/history | limit≤500、可选before结果ID；可选after结果ID（返回之后最早的limit条，升序）；`summary=1` 返回同上精简字段；按时序返回 |
| GET /api/snapshots/{id} | 原始输入、当时配置与hash |
| GET /api/monitors/{id}/statistics | 最后主要统计；可选mode计算其他口径，标记历史重算且不入主要族 |
| GET /api/providers/status | worker心跳、各监测最近状态、耗时、延迟、退避 |
| POST /api/providers/test | provider=manual/mock/http；未配置返回NOT_CONFIGURED |
| GET /api/stream | SSE；API单广播器读取持久outbox；浏览器重连后重新读取latest/history |
| POST /api/research/frozen-observations | snapshotId，可选note；不可变观察 |
| GET /api/research/frozen-observations?monitorId=… | 已锁定观察及完整输入 |
| POST /api/research/frozen-observations/{id}/outcomes | 后续实际结果/说明追加，绝不更新锁定记录 |
| GET /api/exports/{monitorId} | 完整JSON审计，包括所有配置、快照、结果、分钟、统计、冻结和追加结果 |
| GET /api/exports/{monitorId}?format=csv | 每个快照、每个口径各一行，含口径与配置hash |

CSV/TSV列：strike, call_bid, call_ask, call_last, put_bid, put_ask, put_last，以及可选call_reportedMid/put_reportedMid。每个价格可对应`call_bidAt`等事件时间列。缺少时间戳仍可原始计算，但不通过质量。metadata应包含valuationAt、expiration、underlying；可选pricingAt、lastTradingAt、exerciseCutoffAt和人工历史时段声明。导入JSON结构见samples/manual.json。

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

单一SQLAlchemy ORM管理SQLite。WAL、busy_timeout=15000ms。RunConfig/RawSnapshot/PairResult/AggregateResult/MinuteBlock/StatisticalResult/FrozenObservation/ObservationOutcome有禁止UPDATE/DELETE触发器。Monitor、WorkerState属于可变控制状态，ProviderStatus和Outbox追加。

分钟块按统计批次保存不可变修订快照；同一个分钟可出现在多次统计批次中，其statsAsOf可区分，不能将这些审计修订当额外样本。worker在保存结果的同一事务写outbox，API负责广播。worker文件锁与数据库路径绑定，第二个worker启动会拒绝。

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
  - `summary=true` 的精简结果**不含** `chain`；原始快照（`/api/snapshots/{sid}`）也不含。

| 字段 | 类型 | 是否总有（在此形态内） |
|---|---|---|
| `chain.stale` | bool（链已过新鲜 TTL、按 SWR 出数时为 true） | 总有 |
| `chain.ageSeconds` | int（距 `fetchedAt` 的秒数） | 总有 |
| `chain.fetchedAt` | ISO 8601 string（UTC） | 总有 |
| `chain.refreshFailures` | int（后台刷新连续失败次数） | 总有 |
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
