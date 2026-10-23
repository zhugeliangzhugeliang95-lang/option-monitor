# 样例

先创建 monitor-config.json 中的配置，再导入 manual.json（导入API会用选中的monitorId替换占位符）。固定历史时刻为2026-10-19 15:00 UTC。预期MID反推157.19822490096。

CSV/TSV使用同样数据；导入需提供metadata估值时间、到期日与正股报价。缺少某项报价留空，不写0。

可运行 `scripts/seed_demo.py --history` 创建固定种子MOCK报价、35分钟模拟历史、人工Excel样例；明确标记，不能视为实盘。生成器源文件就是模拟数据的可复现定义。
