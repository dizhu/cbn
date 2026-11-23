# CBN Skills 验证记录

## cbn-daily-orders 1.0.2

从已验收版本 1.0.2 迁移到 `.agents/skills/cbn-daily-orders`，21 个 Skill 文件与原版本逐字节一致。项目目录与安装链接按多 Skill 仓库组织。

本 Skill 的 41 项自检涵盖四种出表、长编号与电话、跨日交接状态、并发登记、附件在预留后被修改、包裹数量、平台与客户数量单位及整单同包证据。CI 在 Windows、macOS、Ubuntu 24.04 与 Python 3.9、3.12、3.14 共九种组合运行。

[订单 Skill CI](https://github.com/dizhu/cbn/actions/workflows/cbn-daily-orders.yml)

真实历史订单复验结果保存在本地。正式物流对接和早晨任务仍需合作方回传、当前店铺模板及团队确认的东京时刻。
