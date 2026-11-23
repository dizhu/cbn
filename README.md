# CBN 日本站订单处理 Skill

把下面这句话和安装链接一起发给客户，客户可直接交给 Codex。

> 请安装这个 CBN Skill，初始化订单工作目录并跑完自检。之后帮我处理日本站待发货订单。

[安装 v1.0.2](https://github.com/dizhu/cbn-order-skill/tree/v1.0.2/cbn-daily-orders)

安装包包含三份空白模板、四类出表配置、跨日交接记录和物流包裹核对。小顔与青パパイヤ共用模板。水果茶样品表按团队流程 PDF 第 19 页的版式制作。

首次使用由 Codex 找到 Python 3.9+，自动安装锁定的公开依赖，建立客户工作目录并运行自检。订单、生成文件、交接台账存放在客户工作目录，后续升级继续使用该目录。

客户可给 Codex 原始 Excel，也可让 Codex 在已登录的 Chrome 中导出「日本空港ギフト館」待发货订单。工作日期采用东京时间。四类结果为小顔订单、青パパイヤ订单、DozoFreesh 正式订单和零元样品。

物流回填首次需核对合作方回传的列名和快递公司原文，并配置同店当前上传模板认可的配送业者名称。物流数量支持平台销售单位或客户商品件数；按每个 SKU 的换算核对后，上传恢复原平台数量。样例会使用合成物流资料测试匹配、冲突和整单同包。邮件发送、后台回填按用户授权执行。定时任务采用团队指定的东京早晨时刻。

[Skill 正文](cbn-daily-orders/SKILL.md) · [安装与升级](cbn-daily-orders/references/install.md) · [工作簿处理](cbn-daily-orders/references/workbooks.md) · [交接与物流](cbn-daily-orders/references/handoff.md)
