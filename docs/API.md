# API与Provider协议

本地FastAPI OpenAPI：`/docs` 和 `/openapi.json`。前端同源`/api`反代到Python。数据时间字段必须带时区；持久报价时间规范化为UTC。错误详情不包括供应商原始响应、密钥或认证请求头。

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
| GET /api/results/latest | 按monitorId返回最后结果；标记旧配置 |
| GET /api/monitors/{id}/history | limit≤500、可选before结果ID；按时序返回 |
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
