# CBN 客户 Skills

CBN 客户的 Codex Skills 统一在这个项目维护，每个 Skill 有独立目录、版本和安装链接。

## 可安装的 Skills

| Skill | 用途 | 已发布版本 | 安装链接 |
| --- | --- | --- | --- |
| `cbn-daily-orders` | 日本 TikTok Shop 每日订单、样品表、跨日交接与物流核对 | 1.0.2 | [安装订单处理 Skill](https://github.com/dizhu/cbn/tree/cbn-daily-orders-v1.0.2/.agents/skills/cbn-daily-orders) |

将安装链接交给 Codex，并说：

> 请安装这个 Skill，初始化 CBN 订单工作目录并跑完自检。之后帮我处理日本站待发货订单。

## 项目目录

```text
.agents/skills/
  cbn-daily-orders/
    SKILL.md
    scripts/
    assets/
    references/
    tests/
```

后续 Skill 放入 `.agents/skills/<skill-name>/`，并加入上面的目录表。每个 Skill 的发布标签采用 `<skill-name>-v<版本>`，客户安装指定标签下的对应目录。

客户订单原件、生成结果、配置和交接台账保存在客户工作目录。订单 Skill 随包附带三份空白模板及四类出表配置；首次物流对接需要核对实际回传与该店 TikTok 认可的配送业者值。早晨排程使用团队指定的东京时刻。

[订单 Skill 正文](.agents/skills/cbn-daily-orders/SKILL.md) · [安装与升级](.agents/skills/cbn-daily-orders/references/install.md) · [验证记录](VERIFICATION.md)
