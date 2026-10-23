# 计算与统计约定

## 定价

`S_base = C − P + K exp(−rT)`，每股单位。`T` 为 pricingAt 与快照 valuationAt 的UTC秒差 / (365×86400)，或仅使用人工 days/365。到期日盘中不自动设T=0。历史预览保留原 valuationAt。过期、未知时间且无明确假设返回null。

`BID = C_bid − P_bid + PV(K)`；`ASK = C_ask − P_ask + PV(K)`；MID分别取两条腿中间价；LAST分别取最新成交。CUSTOM分别指定。所需项缺失返回null，不向其他价格借值。人工报告Mid仅在MANUAL源使用，保留reportedMid和reportedMidAt，不虚构盘口。

真实合成盘口下界 `C_bid−P_ask+PV(K)`，上界 `C_ask−P_bid+PV(K)`。高侧grossGap为下界−正股ask；低侧为正股bid−上界。不是保证可实现收益，也不是置信区间。ASK反推可以小于BID。

选档先按Decimal行权价与S_last比较；低侧严格小于，降序取N；高侧严格大于，升序取N。平值排除。先定档，再检查腿，不替换坏档。无可靠Last时可展示原始计算，但不能视为严格选档或统计合格。

一个K贡献一个值；总体均值直接平均所有有效K，不将两侧均值五五混合。rawMean使用所有可计算K，validatedMean仅使用质量合格K；严格时点要求全部请求档位都齐全合格且正股对照有效、常规正股与期权时段交集开放。

配对比较标的、期限、K、币种、行权方式、结算类型、乘数、交割规格、调整标记和时间元数据。调整合约拒绝，单位不为每股拒绝。负数、缺值、交叉盘口、时间戳缺失、过期、不同事件时间差等有独立排除原因；异常原值尽量保留，不按价差大小删数据。非有限输入在Pydantic边界拒绝并返回422，不以0代替。

独立股息输出 `adjustedValue=S_base+dividendPV`，版本parity-dividend-v1；未知股息为null。当前主要统计始终使用基础版本，避免混入修正结果。

## 时间序列

每个严格合格时点：`d_t=10000×(validatedMean−同期正股对照)/同期正股对照`。每个已完成的UTC固定分钟内，6个10秒槽每槽最多取首个有效新事件；至少4槽才形成分钟均值。相同供应商快照重复轮询排除，新事件时间但同样价格保留。没有任何合格事件的分钟是缺口，不补值。

从刚结束的分钟开始，向前取连续有效段，最多60分钟，至少30分钟。缺口打断。数据按配置版本、主口径、正股基准、标的、到期日、源、feedType、历史标记和交易日隔离；固定选K规则导致K变化不会拆流，但保存组成变化。

## HAC近似推断

拟合只有截距的OLS：`d_m=mu+error_m`。明确调用：

```python
sm.OLS(y,np.ones((m,1))).fit(
    cov_type='HAC',
    cov_kwds={'maxlags':5,'kernel':'bartlett','use_correction':True},
    use_t=True,
)
t=(mu_hat-deltaBps)/SE_HAC
```

通过SciPy t分布、近似df=m−1计算greater上尾、less下尾、two-sided双尾p。greater返回单侧下界，less返回单侧上界，two-sided双侧区间。无限一侧用null表示，而不是把无穷写入JSON。样本不足、非有限、SE<1e-10时不生成p。

同时输出lag1/5/10敏感性；只比较预选alpha下的结论是否变化，不自动选显著lag。主要统计每个完成分钟统一计算，同family、同feedType、同历史标记的当前主要检验做Holm；保存familyMembers。口径预览是次要历史重算，不冒充当时主要信号，Holm p为空。配置更改开启新流，不复用旧样本。

置信界描述窗口平均价差，不是下一收盘价区间；HAC不修复错误行情、结构突变、盘口偏差。Holm不控制全天反复查看和调参数的全部错误率。30分钟仅是工程起步门槛。

## 参考与实现核对

- [statsmodels robust covariance implementation](https://www.statsmodels.org/dev/_modules/statsmodels/regression/linear_model.html)：HAC参数、Bartlett、small sample correction。
- [SciPy t distribution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html)：sf/cdf/ppf。
- 最终安装版本锁定在requirements.lock。测试直接以已安装statsmodels同参数拟合及multipletests Holm结果交叉核验，不依赖旧版本默认值。
