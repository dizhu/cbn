# 工作簿处理

命令统一使用 `scripts/cbn.py --workspace 客户工作目录` 入口。它自动使用该目录的 `config.json`。配置中的相对文件路径以配置文件目录为基准。`pathBase` 可指定相对基准，旧项目配置用 `..` 兼容原来的项目目录。

## 四份客户表

| 文件 | 筛选与字段 |
| --- | --- |
| 小顔订单 | 商品名匹配小顔或小颜，与青パパイヤ共用模板。按原字段复制，编号、电话、邮编保存为文本。 |
| 青パパイヤ订单 | 商品名匹配青パパ。`統合リスト` 接受源表同名字段或 `まとめて出品`。`伝票番号` 留给客户。 |
| DozoFreesh 订单 | 商品名匹配 DOZ，原单价大于零。订单号取 `注文ID`，客户编码取 `セラーSKU`，单价取 `SKUの元の価格`。全数字编码的数量乘 6。电话转国内形式，五段地址拼成一列。U、V 列附 SKU ID 和商品名。 |
| DozoFreesh 样品 | 同品牌原单价恰好为零。按流程 PDF 第 19 页版式，内容填客户编码，数量同样按数字编码乘 6。備考填订单号，K、L 列填 SKU ID 和商品名，依頼日用业务日期。出荷日和追跡番号留给客户。 |

默认发件店铺及客户下单承运商代码沿用团队模板的 `dozofreesh` 和 `yamato`，运营员可在配置中调整。客户下单代码和 TikTok 回填认可名称分别维护。

出表前校验收件人、完整地址、日本电话和 7 位邮编。电话支持国内 10/11 位、`(+81)`、`+81`、全角数字及常见连字符。长编号数值化、缺必填、重复订单加 SKU、名称同时命中两个客户、遮蔽地址等情况会报具体行号。中途失败会撤回临时结果。

## 出表命令

```sh
"$PYTHON" "$SKILL/scripts/cbn.py" --workspace "$WORKSPACE" prepare \
  --orders "$SOURCE" --source-scope to-ship --mode live \
  --date "$TOKYO_DATE" --out "$WORKSPACE/runs/$TOKYO_DATE/customer"
```

只有源表没有订单状态列时才需要 `--source-scope to-ship`，先通过后台筛选或团队记录确认范围。`--date` 可省略并自动采用东京当天。历史复验使用 `--mode replay`，可指定旧业务日期并使用新的输出目录。

`--groups smallFace,papaya,doz,sample` 可选处理类别，默认处理全部。某类为零时无该类 Excel，汇总计数为零。源表 114 行中若只有 74 行命中三品牌，其余 40 行计入 `otherRows`。

`summary.json` 保存各类实际出表数、范围外行数及交接状态。`batch.json` 保存该批编号、原数量和换算，供交接和后续回填。正式批次带附件哈希及跨日台账身份。保留原件，重跑使用新的输出目录。

## 字段映射

`orders` 可指定 `sheet`、`headerRow`、`firstDataRow` 和表头别名 `headers`。默认首行为表头，第二行为 TikTok 说明，第三行起读数据。优先读订单子状态，兼容主状态。

日本站日文与英文表头自动识别，例如 `注文ID / Order ID`、`商品名 / Product Name`、`電話番号 / Phone #`。英文导出的 `Prefecture`、`County`、`City/Ward` 分别对应都道府県、市区町村、町名；`Combined Listing` 对应統合リスト。客户输出仍保留原模板的日文/中文列名及原始文本。相同字段的日英文列同时出现时会报表头重复，需核对来源。状态值仍须属于配置的待发货范围，表头语言和来源声明都不会放宽状态校验。

DOZ 地址遇到已识别的罗马字都道府县时，用随包附带的 47 项官方名称对应转为日文，例如 `TOKYO TO` 转为 `東京都`。转换过的地址以空格连接各段，保留市区、街道和楼宇原文；未知的英文地区名称继续进入错误核对。对应数据来自 [日本郵便罗马字邮编资料](https://www.post.japanpost.jp/service/search/zipcode/download/roman-zip.html)，来源与下载校验值保存在 `assets/japan-prefectures.json`；运行时不向外部查询买家地址。

`templates` 的 `smallFace` 可用 `reuse: "papaya"` 共用列映射。每项的 `file`、`sheet`、`headerRow`、`firstDataRow` 指定模板和行。每个 `columns` 键为列字母，`header` 校验准确表头，取值为 `source`、`field` 或 `value` 之一。`source` 可填优先级别名数组，`text` 保留编号文本，`required` 校验必填，`optionalSource` 允许缺少可选列。需要追加表头时指定 `newHeader` 和空的原 `header`。

## 快递回填

```sh
"$PYTHON" "$SKILL/scripts/cbn.py" --workspace "$WORKSPACE" tracking \
  --batch "$BATCH" --current "$CURRENT_EXPORT" --source-scope to-ship \
  --feedback "doz=$FEEDBACK" --upload-template "$UPLOAD_TEMPLATE" \
  --out "$WORKSPACE/runs/$TOKYO_DATE/tracking"
```

可多次传 `--feedback`，客户名可用 `smallFace`、`papaya`、`doz`。`feedback` 按客户设置表头行、首数据行、`orderId`、`tracking`、`carrier`、`skuId`、`quantity`。`quantityUnit` 默认 `platform`，表示平台销售单位。物流方按客户商品件数回传时明确配置为 `customer`，同时指定 `quantity` 数量列，程序按该批每个 SKU 的实际换算核对。已核实整单同包时，用 `wholeOrder.header`、`wholeOrder.value` 指定该列和确认值，该记录按订单填写。

例如数字编码商品平台数量为 2、客户数量为 12，配置 `customer` 后回传 12 才通过，上传仍为 2。与不乘 6 的商品同包时分别相加，客户总量为 12+1=13，平台上传数量分别为 2、1。部分件数、非整倍数或错单位不会四舍五入。

`carriers` 是快递公司原文到平台认可值的精确映射。例如测试资料使用测试名称，正式值从该店当前上传模板或后台取证。仅确认整份反馈均用同一业者时设置 `defaultCarrier`。

上传模板默认第 2 行表头，第 3 行 Required/Optional，第 4 行起写。`upload` 可覆盖行、表名和字段名。保留模板的其他工作表、说明、数据验证和打印设置。匹配使用订单加 SKU，上传数量恢复原平台单位。数量大于 1 的商品需要已发平台数量或明确的整单同包确认。单号冲突、部分数量、多 SKU 缺证据、跨履约方等在 `issues.json` 列出。
