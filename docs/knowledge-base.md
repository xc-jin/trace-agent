# 使用指南：内部知识库选型与需求变更复核

## 1. 使用者、动机与交付物

面向需要为内部知识库选择候选软件及部署方式的小型研发团队。这里的知识库是待选型的业务系统，**不是让 TraceAgent 读取公司的业务知识库正文**。

希望维护的是一份决策记录：需求是否明确、建议依据什么、在哪些条件下成立、约束变了以后哪些建议过期，以及还有什么没有解决。不追求用一次长回复覆盖全部问题。

交付物有三种：

1. `report`：当前约束、问题、结论、引用、局限及修订历史。
2. `review`：某个契约版本以来的约束变化、待复核/已修订/保留/新增条目和未解决事项；Markdown 给人阅读，JSON 便于应用集成。
3. `trace`：本地模型/工具边界事件，供排查过程；不是精确重放、审计防篡改或 checkpoint 恢复。

本项目没有证明通用 Agent 无法处理这些任务；我们做的是把需求变更后的决策维护变成默认工作流，而不是要求每个用户重新设计工具与状态管理。

## 2. 无密钥走完整条流程

在项目根目录执行：

```bash
uv sync --frozen
uv run --frozen agent4x scenario list
uv run --frozen agent4x demo --scenario knowledge-base
```

不读取 `.env`，阻断网络；使用预设模型回复。候选 AlphaKB/BetaKB 与许可证均为虚构对象。

| 阶段 | 程序/Agent 做什么 | 要检查的行为 |
| --- | --- | --- |
| 契约 v1：部署政策未知 | Agent 调用 assess_task，提出问题；程序保存后停止 | 无提前保存的推荐；1 次脚本模型调用 |
| 用户尚未答复又运行 | 程序直接返回已有问题 | 0 次模型调用，不反复消耗请求 |
| 用户明确允许托管，进入 v2 | Agent 读资料，登记部署建议、许可判断、未知项，提议经验 | 7 次脚本调用；经验 pending，不自动生效 |
| 用户要求必须内网自托管，进入 v3 | Store 标记依赖 deployment 的两条记录待复核 | 无模型调用；许可结论保留 r1 / v2 |
| 定向复核 | Agent 沿用部署及未知项原 ID 修订 | 5 次脚本调用；许可结论未改写 |
| 人工交接 | 程序导出报告、变更对照与 trace | 2 条修订、1 条保留；未知项仍显示，不冒充全部解决 |

命令输出报告目录、任务 ID、各阶段调用次数及九项机械检查。读该目录的 `01` 到 `08` 文件即可串起全过程。`07-trace.json` 可能很长，可先看 `08-summary.json`。

```bash
# 用输出中的真实任务 ID 替换 TASK_ID
uv run --frozen agent4x task show TASK_ID
uv run --frozen agent4x report TASK_ID
uv run --frozen agent4x review TASK_ID
uv run --frozen agent4x review TASK_ID --since-version 2 --format json
uv run --frozen agent4x trace TASK_ID
```

这里的 1/0/7/5 是预设脚本轨迹，不是对真实模型调用次数或成本的承诺。

## 3. 准备真实任务，而不是把演示当成产品推荐

### 3.1 确认发送边界

真实 `run` 会把任务、读取到的资料和相关确认记忆发送给外部 DeepSeek API；当前不是全本地推理方案。“为内网知识库选型”不等于“选型助手本身已经符合你的内网制度”。

只使用公开或已获准发送的脱敏资料。不要导入业务文档、客户数据、密钥、内部制度原件或保密采购资料。不能使用外部 API 时，先不要执行真实 `run`；离线演示仍可运行。详见 `SECURITY.md`。

### 3.2 配置本项目凭据

```bash
cp .env.example .env
chmod 600 .env
```

在本机编辑 `.env`，填写 DeepSeek key；不要复制旧项目整份配置，不要在聊天、截图、Git 中提供 key。默认不需要 Tavily，也不联网抓取产品资料。

### 3.3 建立任务

```bash
uv run --frozen agent4x scenario init knowledge-base --project my-team
```

记录输出的 `task_id`，后文 `TASK_ID` 都替换为它。

默认部署政策和许可接受范围都标为尚未确认，**不导入任何虚构产品**。这些文字是供模型评估的用户输入，不是程序能理解的政策语言；首步强制调用评估工具，但是否识别信息不足仍取决于模型。

用户应通过下面的命令填入真实、已确认的要求：

```bash
uv run --frozen agent4x task amend TASK_ID deployment '允许托管，也可选择内网自托管'
uv run --frozen agent4x task amend TASK_ID license '按我提供的许可接受清单匹配；未列入的需人工确认'
```

第二条要求你真的提供接受清单；不能让模型猜测公司政策。若你尚不能确认部署条件，可先运行一次，让 Agent 记录必要问题；进入等待后，实际修改约束才解除等待，相同值不会解除等待。

也可以初始化时使用多个 `--constraint key=value` 覆盖默认要求。确定的额外约束最好在首次选型前提供：新增约束会保守复核所有旧结论。

### 3.4 导入来源

把公开/脱敏的需求说明、许可接受清单和候选资料放在专用目录，例如 `public-materials/`。可参考 `examples/knowledge-base/requirements-template.md` 与 `examples/knowledge-base/source-template.md` 的字段，但不要把未填写的模板当成事实证据。

```bash
uv run --frozen agent4x source add TASK_ID requirements.md --root public-materials
uv run --frozen agent4x source add TASK_ID candidate-a.md --root public-materials
uv run --frozen agent4x source add TASK_ID candidate-b.md --root public-materials
uv run --frozen agent4x task show TASK_ID
```

路径是你准备的相对文件路径；根目录需已存在。每份 `.md`/`.txt` 不超过 65,000 字节，超出时拆分。资料中写清产品版本、采集日期、原始公开地址、逐字事实与未查明事项；程序不会替你确认其真实性或时效性。

要手动体验虚构资料，可另建测试项目，显式添加 `--example-materials`。它只初始化虚构任务，**不会自动运行或阻断随后真实 run 的网络**。不要在真实项目中开启此选项或混用真假来源。

## 4. 第一次决策与人工检查

```bash
uv run --frozen agent4x run TASK_ID
uv run --frozen agent4x report TASK_ID
uv run --frozen agent4x report TASK_ID --output reports/initial.md
```

默认每轮 8 次模型、18 次工具调用、120 秒协作式预算，实际运行产生 API 费用。超过预算会失败，已完成的写入可能仍在；先查看报告与 trace，不要盲目重跑或一味加预算。`run` 的 stopped_unverified 不是“全部正确”或人工通过。

首次报告至少人工检查：

- 部署建议是否真的符合部署要求，而不是把资料没写的能力推断出来。
- 部署与许可判断是否拆开，依赖是否分别声明为 deployment / license。
- 原文是否真正支持结论；引文存在不等于逻辑成立。
- 缺少费用、硬件、运维、检索质量或安全审查资料时，是否明确标为未知。
- 有没有把“支持自托管”写成“必然安全合规”，或把“有引用”写成“质量已经验证”。

如果缺少关键条件，工具会保存问题并返回 needs_clarification；用户按真实答案修改约束后再运行。不要为了绕过等待而填写无意义的新值。

## 5. 需求改变：先看过期条目，再复核

例如部署要求收紧：

```bash
uv run --frozen agent4x task amend TASK_ID deployment '业务文档不能离开内网，必须支持内网自托管'
uv run --frozen agent4x review TASK_ID
```

这两步都不调用模型。根据已声明依赖，部署相关记录应标为待复核，旧内容不会被当作当前建议；只依赖 license 的记录应保留。如果许可判断也依赖部署方式，它被复核同样合理，不应机械要求所有许可记录都不变。

空依赖记录会保守复核；新增约束会复核全部。模型可能漏填依赖，人工应额外检查未标记条目。

```bash
uv run --frozen agent4x run TASK_ID
uv run --frozen agent4x review TASK_ID --output reports/change-review.md
uv run --frozen agent4x review TASK_ID --format json --output reports/change-review.json
uv run --frozen agent4x report TASK_ID --output reports/revised.md
```

本轮入口已有待复核条目时，工具固定该批次，保护原本未受影响的旧条目；全部复核完成后的新运行不再沿用旧批次。模型仍可能错误新增重复记录，工具不会自动理解两个结论语义相同。

### 如何理解 review 的基线

- 默认对照当前契约版本与上一版本；也可 `--since-version N`，要求 `1 <= N < 当前版本`。
- 尚未修改过约束时，用 `report`；没有合法的旧契约供比较。
- 基线取每条结论在契约 N 及以前的最后一个修订。同一版本内的多次写入不构成独立时间点，不能用它回放任意时刻。
- 待复核、已修订、保留、新增分别统计；仍有未知项会单独计数。“保留”表示没改写，不代表本轮重新验证；“无待复核”不代表任务已获批准。
- 当前只处理用户修改约束导致的复核；新增/更新来源不会自动触发相关结论失效，没有自动定期检查产品更新。

## 6. 人工交接与经验确认

交接时同时阅读 `revised.md` 与 `change-review.md`，按这份清单确认：

- [ ] 当前约束确实是用户的要求，未拿历史条件作决策。
- [ ] 不合适的旧建议已明确撤回；原 ID、旧版本和原文依据可查。
- [ ] 未受影响记录没有伪装成本轮重新验证。
- [ ] 依赖与引文已人工抽查；没有把资料缺口填成确定能力。
- [ ] 未解决事项和下一步责任人已在团队工作流中明确。
- [ ] 需要的许可/安全审查由相应人员处理；报告不是审批结果。

经验需要单独确认，不能用确认记忆代替批准选型：

```bash
uv run --frozen agent4x memory list --project my-team
# 确认内容与适用范围后，使用实际 MEMORY_ID
uv run --frozen agent4x memory confirm MEMORY_ID
```

需要修改经验时 edit 后重新确认；delete 后停止未来使用，不承诺擦除全部历史副本。

## 7. 当前完成与未完成

已完成：CLI 模板、显式虚构资料、受限工具循环、澄清等待、约束变更标记、原 ID 修订、版本对照、未解决事项展示、人工确认经验、零网络流程演示及自动化基本验收。

未完成：自动识别全部影响、语义支持关系证明、真实产品推荐保证、Web UI/多人审批、自动部署、全本地模型运行、来源更新自动失效、训练或 benchmark。保持范围明确，避免把一个决策维护工具包装成安全合规系统。
