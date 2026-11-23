# 首次安装和升级

把版本化 GitHub 安装链接交给 Codex，并说明“安装此 Skill，初始化 CBN 订单工作区，跑完自检”。Codex 可用 skill-installer 安装链接中的 `cbn-daily-orders` 文件夹，随后直接读取安装目录的 `SKILL.md` 执行初始化。本 Skill 在下一轮对话可用 `$cbn-daily-orders` 调用。

`init` 会在客户指定的工作目录创建 `.cbn-venv/`、`templates/`、`config.json` 和 `order-handoffs.sqlite`。依赖为锁定版本及 SHA-256 的公开 Python 包。安装日志写到 stderr，业务命令的 stdout 是 UTF-8 JSON。安装路径与工作目录可以包含中文和空格，入口会检查依赖完整性并重装损坏的依赖。

```sh
"$PYTHON" "$SKILL/scripts/cbn.py" --workspace "$WORKSPACE" init
"$PYTHON" "$SKILL/scripts/cbn.py" --workspace "$WORKSPACE" self-test
"$PYTHON" "$SKILL/scripts/cbn.py" --workspace "$WORKSPACE" doctor
```

网络无法访问 PyPI 或缺少 Python 的 venv/pip 时，查看具体错误并修复运行环境后重试。可在 Codex 中调用工作区依赖定位工具获取 Python，或用已有 uv 安装 Python 3.12。运行入口会复用已经装好的独立环境。

升级时先保存原客户工作目录，再替换 Skill 安装目录中的程序。用相同工作目录运行 `init` 和 `self-test`，已有模板、配置、台账会被读取。台账缺失时核对原文件和备份，避免将已有订单当成首次交接。

第一次接入真实物流回传时，需要实际列名、数量口径、快递公司原文和同店上传模板认可的配送业者值。由运营员确认后写入 `config.json` 的 `feedback`、`carriers`；数量口径用 `quantityUnit` 明确为 `platform` 或 `customer`。`doctor` 会列出承运商映射缺项。已有订单出表可以继续使用。

从旧人工流程切换时，新台账还没有历史发件记录。先核对当前待发货中已交接给客户的订单；补录方法见 [交接与物流](handoff.md)。发件结果不明的订单先核对实际记录，确认范围后再交接。
