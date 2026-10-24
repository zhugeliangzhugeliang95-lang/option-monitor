# 实际验证记录

验证环境：macOS arm64，Python 3.14，Node 24.15，Next 16.4.2。完成于2026-10-19（北京时间）。初版验证未接真实券商；后续富途接入验证见下文及 FUTU.md。没有交易测试。

## 已执行

| 命令 / 检查 | 实际结果 |
|---|---|
| `cd backend && ../.venv/bin/python -m pytest -q` | **58 passed**；1条Starlette关于httpx测试客户端未来迁移的弃用提示，不影响当前测试 |
| `cd frontend && npm run typecheck` | 通过 |
| `cd frontend && npm run build` | 通过，Next 16.4.2 |
| `cd frontend && npm audit` | **0 vulnerabilities** |
| `alembic upgrade head` | 启动时和隔离迁移测试均通过；SQLite WAL检查通过，不可变表UPDATE/DELETE被触发器拒绝 |
| `scripts/smoke_runtime.py http://127.0.0.1:8100` | 2个SSE客户端、12次刷新，22秒内仅2个新快照且属于2个不同10秒槽；第二worker被锁拒绝 |
| `scripts/browser_smoke.js`（Playwright CLI） | 人工保存、精确样例、报价预览不增样本、刷新保留历史、冻结观察、CSV导出通过；pageErrors=[] |
| 浏览器console检查 | 最后验证会话Errors=0、Warnings=0 |
| MOCK历史端到端统计 | 固定种子合成35分钟历史入库，n=35、p=1.0、Holm p=1.0，保存历史MOCK标记；不是实盘积累 |
| 双击启动脚本 | `启动监测台.command` 实测成功，检测已有3000/8000服务后自动使用3100/8100；同源API代理正常 |
| 进程清理 | SIGINT关闭启动管理器，原前端/API/参考网关端口无监听；未停止本机原先占用3000端口的其他程序 |

详细调度观测见 runtime-smoke.json。浏览器回归源文件位于 scripts/browser_smoke.js，使用Playwright CLI `run-code --filename=…`，需要先装载演示数据、打开工作台页面。它只操作EXCEL/MOCK演示对象。

## 核心覆盖

- Excel样例经完整Python内核得到 **157.19822490095981**。
- BID=603.00，ASK=602.70，MID=602.85，syntheticLower=602.50，syntheticUpper=603.20；CUSTOM多组合正确。
- Decimal最近档、ATM排除、双0/单侧0、缺腿不替补、规格/到期日不混配、100/102/110总体均值104。
- 缺盘口不借Last，人工报告Mid不造盘口，负数/非有限/单位错误、陈旧与缺失时间、过期、盘中T>0。
- 原始均价和质量均价分离；重复事件不进统计；新时间但相同价格保留。
- 固定分钟槽去重、覆盖不足、缺口打断、未结束分钟排除；样本不足、零SE、非有限、不同尾部和置信界。
- HAC与实际安装statsmodels显式参数逐项比较；Holm与multipletests对照。
- 价格预览不落新历史；配置变更不混样本；冻结内容不受后续快照改写；停用保留历史。
- HTTP分页、本地ASGI参考网关、MOCK确定性、401/403类权限、409未订阅、429 Retry-After、503错误；源隔离、密钥不进入导出。
- 同时监测MOCK和未配置HTTP时，MOCK继续；单对象锁防重入。交易所日历验证了夏令时、感恩节休市和次日半日市。

## 没有验证的范围

- 富途期权实时报价、盘中数据时效、完整合约截止时刻仍待验证；OpenD 登录、正股权限和正股快照已核验。
- 30分钟以上真实市场前向验证、经济可实现性、盈利或收盘预测能力。
- 真实供应商线上限流/断网与电脑睡眠的硬件故障注入；本地错误模拟、心跳过期机制与缺口规则已覆盖，但不等于每类系统故障均已实测。
- 多机部署或公网安全验收；这是本地单worker、单API研究工具。

截图是本机真实浏览器渲染的MOCK界面：`output/playwright/overview-final.png`。源码包不包含运行数据库；如需重现，使用seed_demo.py。

## 富途接入更新

2026-10-19：新增官方 SDK 快照 Provider、独立 FUTU 流、权限诊断和源标签；完整 pytest 58 passed，typecheck / build 通过。实测期权权限仍为 NO，未声明已取得期权真实报价。详情见 FUTU.md。

富途浏览器验收：通过「连接富途」创建独立 SPY FUTU（默认0档）；点击连接测试准确显示正股 LV3 / 期权 NO，未产生模拟报价。后台 worker RUNNING，并持久保存权限错误与退避状态。截图：output/playwright/futu-permission-check.png。

## 2026-10-19 13:50 富途权限生效

最新只读核验：正股LV3、期权LV1；通过实际 Provider 取得SPY当日302合约及763 Call/Put买卖报价，标准合约映射通过。市场休市，期权价格更新时间仍为上个交易日，未通过实时新鲜度验收。规范化证据保存在本机data/futu-quote-verification.json，源码包排除data。


## 2026-10-19 晚间：20个真实标的

- 下架活动列表的3个MOCK、人工EXCEL示例、未配置HTTP以及2个重复SPY；保留历史，不清库。
- 20个标的均逐一通过实际Futu正股、到期日、标准期权链和12个期权买卖报价核验。完整列表见 FUTU.md。配置显式记录到期日、上下各3档和纽约16:00的研究定价假设。
- 改为批量报价；权限统一检查并缓存60秒，避免20标的重复权限查询触发 get_user_info 限频。错误诊断仅保留方法名与错误代码，不返回账号数据。
- 后端 **64 passed**；前端 typecheck 和 production build 通过。新增覆盖批量采集、单标的错误隔离、静态链限流退避、Worker写入、权限仅查询一次。
- 无订单提交，无交易上下文；缺买卖盘事件时间仍不进入严格统计。

21:50（北京时间）实际验收：原20个标的均有当前配置下6对期权的原始反推均价、12个期权报价和正股价格；与独立逐腿公式复算一致（误差<1e-8），后续一轮全部继续更新。随后用户追加SOXX，单标的12个期权报价预检通过。21:53整批复核通过：21/21个活动监测均为FUTU真实流，252个期权报价、126对反推计算完整，全部采集快照距检查时刻不到20秒，无限流退避；公式逐腿独立复算误差<1e-8。SOXX页面已显示原始反推价并连续更新。注意采集年龄不等于每个买卖报价的事件年龄，严格统计仍未通过。截图：output/playwright/soxx-live.png。

## 2026-10-20 独立模拟盘验收

- Backend: 85 tests passed (1 existing TestClient deprecation warning); covers next-snapshot fills, negative Kelly, stop gaps, premium loss cap, aggregate exposure, daily stop, stale/future quotes, missing held contracts, point-in-time invariance, persistence and append-only migration.
- Frontend: TypeScript and production build passed; browser verified simulator navigation, running and saving a replay, mode switch and persisted account summaries.
- Replayed 8,346 Futu snapshots from New York 2026-10-19 09:30:00 to 12:03:30. No afternoon/close coverage. Strict: 8,316 missing quote-event-time rows, 28 missing gaps and 2 duplicates; no fills. Exploratory: 3,676 eligible rows, 4,638 stale/future-last exclusions, 2 invalid books, 28 missing gaps and 2 duplicates. All eligible gaps imply zero Kelly under a 25bp stop and p=0.5 (maximum eligible absolute gap 11.268085bp). Both independent $50,000 accounts remain $50,000; neither direction variant nor cost/delay stress creates a fill. This is not a full-day profitability result.

### 本金更正

按用户更正，默认总本金改为 $200,000，正股与期权各 $100,000。重新运行两个方向方案，8,346 条相同盘中快照，仍零成交、每户净值 $100,000；旧结果保留。86 tests passed，TypeScript/build passed，浏览器已核对两户本金。
