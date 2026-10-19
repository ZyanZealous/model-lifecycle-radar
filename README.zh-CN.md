# Model Lifecycle Radar 中文快速开始

Model Lifecycle Radar 用 Python 3.11+ 和标准库读取六家厂商的公开模型目录及生命周期/更新文档，生成供模型负责人审阅的 JSON 和 Markdown 报告。

它**不会调用推理 API**、不会登录、不发送消息，也不会自动选择或替换模型。公开文档不代表你的账号已开通该模型。目录缺少模型不等于模型已退役。文档或 OpenAPI 结构变化只提示人工复核，不直接判定为 API 破坏性变更。

## 安装与运行

```powershell
python -m pip install --no-deps -e .
# 编辑 watchlist.json：填写精确模型 ID、负责人和服务名称
python -m model_lifecycle_radar collect --state .radar/state.json --watchlist watchlist.json --out reports
python -m model_lifecycle_radar check --state .radar/state.json --watchlist watchlist.json --out reports
python -m model_lifecycle_radar check --state .radar/state.json --watchlist watchlist.json --fail-on warning
python -m model_lifecycle_radar smoke --vendor all
python -m model_lifecycle_radar demo --out .radar/demo-report
```

`collect` 在线读取公开官方页面并更新状态；`check` 离线使用已保存证据重新计算当前风险。两者都会生成 `report.json`、`report.md` 和 `events.json`。`smoke` 在线检查来源和解析结果，不修改状态。`demo` 使用合成数据离线生成示例报告。

退出码：`0` 表示运行正常且未超过指定严重级别，`1` 表示超过 `--fail-on`，`2` 表示采集、配置、锁或状态错误。部分来源失败时仍会写报告，并保留该来源上次有效数据。

## 判定规则

- 首次成功采集建立基线，不把所有模型和文档都当作新事件；但已有官方退役日期仍会立即检查。
- 新模型只表示“本次在公开目录中首次观察到”，不代表今天发布。
- 监控模型缺失时显示“在配置的范围内未核实”。只有两个可比较的成功目录快照都缺失，才生成消失复核提醒；不会仅凭缺失判定退役。
- 只有结构化官方证据中的精确日期才触发 30/14/7 天阈值；“暂定”或“不得早于”日期不会作为截止日期。
- 每次离线 `check` 都按当前 watchlist、负责人、服务和 `--retirement-days` 重新计算。
- 当前发现与新增去重事件分开输出。历史最多保留 1000 条；旧事件清理后，同一信号可能再次出现。

支持来源和覆盖范围见英文 [README](README.md) 与 [验证记录](docs/verification.md)。自定义官方 JSON/OpenAPI JSON 指纹示例在 `examples/custom-sources.json`；自定义 schema 变化只要求复核，不作通用破坏性判断。

DeepSeek 的可调用 ID 只取自 `MODEL` 行；`MODEL VERSION` 仅作显示名称。Mistral 会从当前模型表发现有效模型卡，精确 API 名称只取自标注的 API 字段或官方详情页；两处都未发布 ID 的卡片不会编造 ID 加入目录，历史退役记录单独解析。DeepSeek 更新来源使用带尾斜杠的 [Change Log](https://api-docs.deepseek.com/updates/)。

当前版本只监控公开文档，不收集账号可见目录。`OPENAI_API_KEY` 不会被读取，也不需要提供。GitHub Actions 示例每六小时运行一次；若首次运行没有状态文件，或缓存缺失/过期，会建立新基线，无法补回错过的历史。
