# 富途 OpenD 接入

2026-10-19，北京时间。负责人：用户；实现与验证：Codex。只读行情，无交易接口。

## 使用

1. 打开 `/Applications/Futu_OpenD.app`，使用购买行情卡的同一账号登录，完成问卷和协议。
2. 确认 OpenD 美股正股与美股期权权限。购买后仍无权限时，检查行情卡激活状态、适用终端和账号，并重启 OpenD。
3. 监测台「连接富途」→ 输入支持列表中的美股 / ETF 代码 → 创建 → 设置档数（默认两侧为0）→「测试行情连接」。FUTU 独立于现有 MOCK 历史。
4. 默认自动选择最近有效到期日，到期后顺延。仍可显式选择仅当天或手动到期；最近一期缺链或缺腿不会改用更远一期。详见 [最近到期日规则](NEAREST_EXPIRY.md)。
5. 富途期权链不提供本项目需要的完整定价/最后交易时刻。默认以所选到期日交易所常规收盘时间（含半日市）作为明确的研究定价假设，不作为已核实的期权截止时刻；也可手动填写带时区的参考时刻和假设，或选择 days/365。
6. 后台目标每10秒采集；链和到期日分别短时缓存，报价批量读取，限流或连接故障退避。电脑睡眠不能持续采集。

## 真实边界

- 数据通过本机官方 SDK `futu-api==10.11.7108` 读取；仅使用 OpenQuoteContext。
- 支持 21 个美元美股 / ETF 及其标准期权：SPY、QQQ、IWM、DIA、AAPL、MSFT、NVDA、AMZN、META、GOOGL、TSLA、AMD、AVGO、NFLX、PLTR、COIN、JPM、BAC、XOM、GLD、SOXX。可使用 US. 前缀；未知标的拒绝接入。
- 快照 bid/ask/last 保留供应商数值。`update_time` 是当前价更新时间，按美东时区转 UTC；不复制为 bidAt/askAt，也不用采集时刻冒充事件时间。
- 快照缺少买卖盘独立时间、链缺少期权截止时刻：原始价格可展示，严格同步的 MID 质量/统计不可用。权限就绪也不等于报价足够新鲜。
- REALTIME 表示读取时已核实正股和期权行情权限，仍须单独看报价年龄。休市可能返回上一交易时段值。
- 本程序计算的合成区间是单腿报价形成的研究区间，不是富途组合订单的可成交报价。
- 不请求账号资产、订单、交易解锁或下单；权限诊断只返回登录状态、行情权限、市场状态，不返回账号标识。

## 历史实测（13:50）

- 本机 GUI OpenD 10.11.7108 已启动且登录；SDK 10.11.7108 已装入本项目隔离环境。
- 2026-10-19 13:50（北京时间）最新诊断：正股 LV3，期权 LV1。已取得 SPY 2026-10-19 到期的302个合约和763行权价 Call/Put 买卖报价。
- 早先期权 NO 的授权阻塞已解除。当前市场 AFTER_HOURS_END；核验的期权当前价更新时间为2026-10-16美东16:14:58/59，因此仅证明真实数据读取成功，不证明盘中新鲜/同步报价。
- 后端 58 项测试通过；TypeScript 检查及生产构建通过。新增测试覆盖无权限禁止采集、时区、缺时间不伪造、合约映射、缓存、超时不重入。

## 官方资料

- [权限与额度](https://openapi.futunn.com/futu-api-doc/intro/authority.html)
- [购买后不生效与盘口档数](https://openapi.futunn.com/futu-api-doc/qa/quote.html)
- [快照字段](https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html)
- [期权链字段](https://openapi.futunn.com/futu-api-doc/quote/get-option-chain.html)

## 21 标的批量采集（2026-10-19）

- 本地活动监测仅保留已验证的富途真实流；模拟、人工示例、未配置 HTTP 与重复 SPY 已停用，审计历史保留。
- 每标的现价上下各3档，6对 Call/Put。当时到期日从 API 核验后显式固定，未自动换期；2026-10-26 起已改为 [最近有效到期日自动顺延](NEAREST_EXPIRY.md)。
- 各到期日纽约16:00仅为明确记录的研究定价假设，不是合约截止时刻证据。5%利率仍为人工假设，未知股息未当作已核实零股息。
- 每批统一核验权限，权限结果缓存60秒；报价请求仍由富途校验。静态链/到期日缓存30分钟；受限请求退避。预热完成后，每轮21只正股一次快照、最多252个选定期权一次快照，市场状态亦批量请求。
- 新增测试覆盖20标的批量映射、单标的合约错误隔离、静态链限流时保留其他标的采集、批量结果写入与不支持标的拒绝。
- 快照缺买卖盘独立事件时间，原始参考价可以显示，质量合格均价和严格统计仍保持不可用。

限频依据：[期权链](https://openapi.futunn.com/futu-api-doc/quote/get-option-chain.html)、[市场状态](https://openapi.futunn.com/futu-api-doc/quote/get-market-state.html)、[市场快照](https://openapi.futunn.com/futu-api-doc/en/quote/get-market-snapshot.html)。

用户追加 SOXX 后核验到最近到期日为2026-10-23，12个期权买卖报价完整；与其他标的一样使用显式研究定价假设。

## 超时、预热与状态（item 1，第一部分）

一批富途采集在后台任务里运行，不阻塞 mock/http 监测。

### 预算

- **SDK 线程内的协作截止时间**：`FUTU_HOT_BUDGET`，默认 7 秒。
  - 每次真实调用前，检查"已用时间 + 该方法余量"是否超过预算；超过就不发起这次调用。
  - 余量默认值：
    - `get_user_info` 2.0
    - `get_global_state` 0.5
    - `get_option_expiration_date` 1.0
    - `get_option_chain` 1.0
    - `get_market_state` 0.5
    - `get_market_snapshot` 0.75
  - 每项可用 `FUTU_HEADROOM_*` 覆盖。
- **整批超时**：`FUTU_BATCH_TIMEOUT`，默认 9 秒，必须大于 HOT_BUDGET。
- 已经在执行的 SDK 调用不会被打断：同一时刻只有一个 OpenD 调用。

### 先报价，后预热

- 每批先给"链已缓存"的监测报价：市场状态 1 次，快照 2 次。
- 剩余预算最多拉 `FUTU_CHAIN_WARM_PER_CYCLE` 条期权链：
  - 默认 1。
  - 上限由预算和每窗 8 次限频推出，当前为 2。超过上限时截断，并记 WARNING。
  - 若期权链窗口已有 8 次调用，本批停止拉链。
- 拉链顺序：
  1. 今天到期的冷链；
  2. 已过 TTL、正在按过期缓存出数的链（每个键只排一次刷新）；
  3. 其他冷链。
  - 同一类按等待时间先后排，再按代码排。
- 之后为后面最多 3 个排队标的预取到期日。
- 新拉好的链在下一批出数。
- 链的 TTL 为 1800 秒，加按代码确定的 0–599 秒错开量。
- 过 TTL 后按 stale-while-revalidate 继续出数，见下节。

### 过期缓存、跨日与预热（item 1，第二部分）

- **SWR**（`FUTU_CHAIN_SWR=1`，默认）：链过 TTL 后继续用缓存出数，provider_status 和聚合结果带 `chain.stale=true`、`ageSeconds`；同时排一次刷新（第 2 类，仅在预热窗口内）。
  - 刷新失败退避 10、20、40… 秒，最长 300 秒，记 INFO；`RATE_LIMITED` 和批次时限不算失败。
  - 最大年龄：当天到期 `FUTU_CHAIN_MAX_AGE`（默认 5400，必须大于 2400），远期 `FUTU_CHAIN_MAX_AGE_FUTURE`（默认 345600 = 4 天）。超过后丢弃并记 WARNING，状态 `CHAIN_WARMING`（`droppedReason=MAX_AGE`），按第 1 类重拉。
  - `FUTU_CHAIN_SWR=0`：过 TTL 即 `CHAIN_WARMING`，不提前刷新。
- **跨日（美东日期）**：
  - 当天到期的链只在当天拉取时可用，过期缓存不跨日；远期链跨日保留，可按 SWR 出数。
  - 到期日列表只有当天拉取的算新鲜；前一天的列表仅在重拉失败时经过期过滤后使用，并标 `expirationsStale=true`；更早的不用。
  - 新一天第一批清理已过期的链、到期日及其刷新/退避状态，记 INFO。
- **预热窗口**：交易日 `FUTU_PREWARM_START`（默认 09:00，范围 08:15–09:25 美东）到收盘后 15 分钟（提前收盘日按交易所日历，例如 13:15）。
  - 窗口外不拉当天到期的链：状态 `CHAIN_WARMING`（`reason=offHours`），不计入 E.2 上限；到期日仍会拉取，以便报告无当日到期/已到期。
  - 窗口外远期冷链每批最多拉 1 条；窗口外不做 SWR 刷新。非交易日没有窗口。
  - 窗口开启即预热（普通队列，每批 1 条）。开启后 25 分钟（默认 09:25）检查一次：全部就绪记 INFO `prewarm complete n/N`，否则 WARNING 列出未就绪代码。维护期间不预热。
- **OpenD 离线**：每批都会尝试连接；WARNING 每 60 秒最多一次（只记状态码），恢复连接时记 INFO。

### 卡死升级（E.1，`FUTU_WATCHDOG=0`，不重建连接）

上一请求仍在执行时，新批次得到 `FUTU_BUSY`。满足以下任一条件后，改为 `OPEND_UNRESPONSIVE`：

- 该请求已超过 `FUTU_HANG_SECONDS`（默认 60，范围 20–600，必须大于 BATCH_TIMEOUT）；
- 连续 `FUTU_BUSY_ALERT_CYCLES` 批（默认 6，范围 2–60）都遇到通道忙。

按 10 秒节奏，只有当 `FUTU_HANG_SECONDS > FUTU_BUSY_ALERT_CYCLES×10+9` 时，连续批次条件才会先于时长条件触发。默认值（60, 6）下两者在约 60 秒同时触发。

日志：

- 进入该状态时记 WARNING 一次；
- 卡住达到 5×HANG 秒时记 ERROR，之后每 5×HANG 秒再记一次；
- 请求返回后记 INFO，带卡住时长。

提示：卡住后的第一批返回 `TIMEOUT`，worker 的失败次数记 1，直到下一次成功才清零（下一次失败的退避从这里起算）。这是预期行为。

日志不含参数和账号信息。不发送任何通知。

### 期权链长期失败（E.2）

- 拉链失败时，状态仍为 `CHAIN_WARMING`（`reason=retrying`），并带 `lastError.code`。
- 同一标的的重试退避为 10、20、40… 秒，最长 300 秒。
- 从开始等待算起超过 `FUTU_CHAIN_WARM_MAX_SECONDS`（默认 600，范围 120–3600）且至少失败一次时，状态变为 `CHAIN_UNAVAILABLE`，并记 WARNING 一次。
- 上限的计时起点是该链**进入预热队列**的时刻，不是第一次失败的时刻。例如排队 590 秒后第一次失败，再过约 10 秒就会变为 `CHAIN_UNAVAILABLE`。
- 盘外：预热窗口外的当天链显示 `CHAIN_WARMING`（`reason=offHours`），等待计时每批归零，不计入该上限；因此当天链的上限实际从窗口开启（或之后入队）起算。（#16 单独运行时夜间/周末会变红的过渡行为由此解决。）
- 恢复后记 INFO。

### 数据保留与停止

- **每日保留清理**：期间不启动新的富途批次，到期监测显示 `FUTU_BUSY`（`operation=maintenance`）。清理会先等待正在运行的批次（最多 BATCH_TIMEOUT），超过 `FUTU_MAINT_ALERT_SECONDS`（默认 300）记 WARNING。
- **停止顺序**：
  1. 取消并等待富途批次；
  2. 停心跳；
  3. 等待在途请求，最多 `FUTU_CLOSE_WAIT`（默认 10）秒，然后在 SDK 线程里关闭上下文。仍卡住时记 ERROR、不关闭上下文，停止流程照常完成；
  4. 关闭其他 provider。

非法环境变量回退默认值，并记 WARNING。生效配置在启动时以 INFO 记录一次。状态表见 `docs/API.md` 的"Futu 用户可见状态（前端契约）"。
