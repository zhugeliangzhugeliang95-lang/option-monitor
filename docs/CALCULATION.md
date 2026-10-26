# 计算与统计约定

## 定价

`S_base = C − P + K exp(−rT)`，每股单位。`T` 为 pricingAt 与快照 valuationAt 的UTC秒差 / (365×86400)，或仅使用人工 days/365。到期日盘中不自动设T=0。历史预览保留原 valuationAt。过期、未知时间且无明确假设返回null。

`BID = C_bid − P_bid + PV(K)`；`ASK = C_ask − P_ask + PV(K)`；MID分别取两条腿中间价；LAST分别取最新成交。CUSTOM分别指定。所需项缺失返回null，不向其他价格借值。人工报告Mid仅在MANUAL源使用，保留reportedMid和reportedMidAt，不虚构盘口。

真实合成盘口下界 `C_bid−P_ask+PV(K)`，上界 `C_ask−P_bid+PV(K)`。高侧grossGap为下界−正股ask；低侧为正股bid−上界。不是保证可实现收益，也不是置信区间。ASK反推可以小于BID。

选档先按Decimal行权价与S_last比较；低侧严格小于，降序取nBelow；高侧严格大于，升序取nAbove。先定档，再检查腿，不替换坏档。

平值档（ATM）：仅当 S_last 与某个行权价**完全相等**（按 Decimal 比较，450 = 450.0 = 450.00）时，该档以 side=`at` 额外纳入，位于低侧与高侧之间：nBelow=nAbove=5 时共 11 档（5 低 + 1 平值 + 5 高），requestedCount = nBelow+nAbove+1。S_last=450.01 等不相等的情况不算平值，仍为 10 档（5 低、5 高）。平值档计入 rawMean/validatedMean/std/median/min/max，但不计入 belowMean/aboveMean，单独输出 atValue。
- 11 档需要 nBelow 与 nAbove 都设为 5。后端默认值仍为 0/0；0/0 时不选任何档（也不选平值档），状态为 PLEASE_SET_COUNTS。
- 已有监测对象的配置不会被自动修改；新建监测对象默认 5/5 由前端 PR #8 提供。无可靠Last时可展示原始计算，但不能视为严格选档或统计合格。

一个K贡献一个值；总体均值直接平均所有有效K，不将两侧均值五五混合。rawMean使用所有可计算K，validatedMean仅使用质量合格K；严格时点要求全部请求档位都齐全合格且正股对照有效、常规正股与期权时段交集开放。

配对比较标的、期限、K、币种、行权方式、结算类型、乘数、交割规格、调整标记和时间元数据。调整合约拒绝，单位不为每股拒绝。负数、缺值、交叉盘口、时间戳缺失、过期、不同事件时间差等有独立排除原因；异常原值尽量保留，不按价差大小删数据。非有限输入在Pydantic边界拒绝并返回422，不以0代替。

独立股息输出 `adjustedValue=S_base+dividendPV`，版本parity-dividend-v1；未知股息为null。基础反推值与股息修正结果分别展示。

## 统计检验已移除

2026-10-27 起不再计算分钟样本、HAC t 检验、p 值、置信界或 Holm 调整。仍保留反推均价、价差、逐档分布和报价质量判断；走势图来自已保存行情。
