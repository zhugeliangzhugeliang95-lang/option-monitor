# 协作交接

后端从 backend/app/models.py、engine.py、statistics.py 开始；前端位于 frontend/app/page.tsx 与 globals.css。约束见 AGENTS.md。启动和测试见 README.md。数据库和密钥均不进入源码包。

接供应商时只做 Provider/协议映射和字段归一化，先用本地 gateway.py 与 tests/test_providers.py 验证，再核实权限、feedType、事件时刻、合约规格、交易日历和单位。未核实不可宣称实时。主统计方法和公式不能在前端另做一份。


## GitHub 协作

私有仓库：https://github.com/zhugeliangzhugeliang95-lang/option-monitor

协作者收到邀请并接受后，可以克隆仓库：

```bash
git clone https://github.com/zhugeliangzhugeliang95-lang/option-monitor.git
cd option-monitor
bash scripts/dev.sh
```

新电脑安装和富途 OpenD 设置见 [迁移说明](docs/另一台电脑运行.md)。仓库只含源码，首次运行列表为空；需要登录自己的 OpenD 并配置监测标的。数据库、历史行情、回放记录和密钥不会随 Git 同步。

建议新建分支开发，提交后通过 Pull Request 审阅。后端逻辑改动运行 pytest；前端改动运行类型检查和构建。不要提交 .env、data/、日志、账号信息或访问令牌。模拟盘仅供历史研究，不提交真实交易。
