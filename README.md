# TraceAgent

**结论有依据，条件变化可复核，修订过程可追溯。** 基于 DeepAgents 的可追溯、可修订 Agent，不限定于某一个业务场景。

当前为 **CLI MVP**，不是聊天 Web 产品，也不是强化学习训练平台。本项目没有模型训练、学术对照或效果领先声明；只做功能与安全边界的基本验收。

## 为什么开发它

多轮任务中的目标条件可能变化。例如，讨论技术方案时，第一轮允许托管，之后可能改为必须内网自托管。我们希望交付的不只是新答案，还包括：

- 哪条建议是在什么条件下形成的，引用了哪段资料？
- 条件改变后，哪些旧建议暂时不能再用，哪些记录保留原样？
- 为什么从候选 A 改为候选 B，是否还有未知项未解决？

TraceAgent 把这些问题落实为任务契约、独立结论、依赖标记与修订记录。它不是声称其他 Agent 无法实现，而是把这套**决策维护流程**做成可以直接使用的应用，而不只依赖模型在聊天中自觉遵守。

适合：需要阅读资料、保存依据，并在约束变化后持续复核结论的任务。不适合：简单的一次性问答、自动部署、法律意见、安全认证，或未经脱敏的保密材料处理。

## 核心闭环

```text
用户建立任务契约 → 导入资料 → Agent 评估是否需要澄清 → 登记有引用的结论
    → 用户修改关键约束 → 程序标记受影响结论
    → Agent 定向修订 → 查看原结论、更新结论与引用
```

- **任务契约**：用户掌握目标和约束；首步强制评估是否需要澄清，进入等待状态后由程序结束本轮，不能私自改需求。
- **证据账本**：精确引用已保存来源，记录字符位置和内容 SHA-256；事实、推断、未知分开。
- **定向复核**：依赖只能选择当前任务约束键；空依赖必须说明理由，约束变化时仍保守复核。新增约束时保守复核全部记录。
- **受控经验**：项目范围内的记忆先提议、再由用户确认；可修改和删除，修改后重新待确认。
- **执行边界**：不提供 shell 或任意文件读取；模型/工具次数限制、客户端超时、本地事件记录。

引用存在不代表结论已获逻辑验证，依赖标记可能遗漏；系统不承诺自动发现所有影响。`current` 是账本维护状态，不是事实认证。

## 完整示例：内部知识库选型

这是解释机制的可运行示例，不是项目唯一的使用场景。

```text
部署政策未明确 → 记录追问并等待用户
    ↓ 用户确认允许托管
读取候选资料 → 分别登记部署建议、许可适配与未知项
    ↓ 用户要求业务文档不能出内网
先标记旧建议待复核 → 沿用原 ID 修订 → 输出新旧决策对照
```

### 一条命令体验完整流程

安装后运行，无需 key，不产生 API 费用：

```bash
uv run --frozen agent4x demo --scenario knowledge-base
```

演示使用**虚构 AlphaKB/BetaKB、预设模型回复和真实 DeepAgents 图**，阻断网络；不代表真实产品选型或模型质量已通过验证。它覆盖首次澄清、等待重跑零模型调用、初始决策、需求变更、定向复核和待确认经验。

输出 `reports` 目录包含：

| 文件 | 应关注什么 |
| --- | --- |
| `01-clarification.md` | 部署政策未明确时先追问，没有提前登记推荐 |
| `02-initial.md` | 部署、许可与未解决事项分别记录 |
| `03-needs-review.md` / `04-review-before.md` | 不调用模型就先标记过期建议；许可结论保留 |
| `05-revised.md` / `06-review-after.md` | 原 ID 修订、旧建议撤回；2 条修订、1 条保留，仍有未知项 |
| `07-trace.json` / `08-summary.json` | 本地调用事件及机械检查结果 |

查看变更对照，不会额外调用模型：

```bash
# TASK_ID 替换为演示输出的真实值
uv run --frozen agent4x review TASK_ID
uv run --frozen agent4x review TASK_ID --format json
```

真实项目默认不导入虚构资料：`uv run --frozen agent4x scenario init knowledge-base --project my-team`。从准备公开资料到人工交接的完整操作见 `docs/knowledge-base.md`。

## 环境与安装

Python `3.12.13`，`deepagents==0.6.12`。独立 uv 环境与锁文件；不导入旁边学习项目的代码或环境。

项目展示名为 TraceAgent；当前 Python 包名仍为 `agent4x-app`，命令仍为 `agent4x`，以下示例可直接使用。

```bash
uv sync --frozen
uv run --frozen agent4x --help
```

## 更小的机制演示

```bash
uv run --frozen agent4x demo
```

演示使用真实 DeepAgents 图和预设模型回复，阻断外部网络，不加载 `.env`：

1. 从虚构资料登记部署与许可证两条结论。
2. 把“允许托管”改为“必须内网自托管”。
3. 只标记部署结论需复核，保留许可证结论。
4. 修订原部署结论并保留历史；记忆仍待用户确认。

输出中给出任务 ID 和三份报告的路径：初始、待复核、修订后。数据默认位于被 Git 忽略的 `.agent4x/`。

这是控制流演示，不是模型推理能力或实际框架选型的证明。示例中的 Alpha/Beta 为虚构对象。

## 用一个任务看懂新增环节

**任务：为一个内部项目选择部署方案；第一轮允许托管，之后用户要求必须内网自托管。**

下面展开的就是 `agent4x demo` 已实现的流程，不是未来功能设想。普通的“问题 → 检索 → 回答”流程也能给出建议；这里额外管理的是：**建议依据什么、依赖哪些条件、条件变化后哪些建议过期，以及修改前后发生了什么。**

### 第一步：用户建立契约，程序固定执行范围

演示首先由程序模拟用户建任务和导入资料，不让模型自行决定需求：

```text
目标：根据资料选择部署方案，并在要求变化后修订。
项目：每次演示创建一个独立项目
契约 v1：
  deployment = 允许托管部署
  license    = 接受示例宽松许可证

资料快照：
  Alpha仅支持托管部署。
  Beta支持内网自托管部署。
  Alpha与Beta均使用示例宽松许可证。
```

**多出来的环节：**目标和约束进入 SQLite，资料保存为有 ID 和内容 SHA-256 的快照；本次运行绑定契约 v1。不是只把所有信息塞进聊天记录。

**作用：**后续保存结论、比较需求变化和定位原文都有明确的数据对象；模型不能通过工具自行修改用户约束。

### 第二步：Agent 读任务和资料，把建议登记为结构化结论

第一轮实际工具顺序如下。最终文字回复不是工具调用：

```text
assess_task(assessment=ready)
    ↓ 根据入口任务快照确认契约 v1 的要求足够明确
read_source(source_id)
    ↓ 阅读当前任务的资料快照
save_claim(draft=部署结论)
    ↓ 保存结论 D，依赖 deployment
save_claim(draft=许可证结论)
    ↓ 保存结论 L，依赖 license
propose_memory(text="本演示项目选择方案时需要明确部署边界。")
    ↓ 仅提出经验，状态为 pending
最终回复：已登记两条结论，经验待确认
```

`D`、`L` 是下文的阅读标签，不是固定 ID；真实 ID 每次生成，演示输出中的 `affected_claim`、`unchanged_claim` 分别对应它们。

部署结论提交给 `save_claim` 的业务数据形状如下，来源 ID 为占位示意：

```json
{
  "text": "允许托管时，可以考虑Alpha；Beta是可自托管的备选。",
  "kind": "inference",
  "depends_on": ["deployment"],
  "evidence": [
    {
      "source_id": "替换为当前任务的真实来源ID",
      "quote": "Alpha仅支持托管部署。Beta支持内网自托管部署。"
    }
  ],
  "limitations": "仅依据虚构资料，未比较实际成本与性能。"
}
```

**多出来的环节：**`save_claim` 经 Pydantic 校验后，由 Store 检查契约版本、约束键、来源归属和引文是否真实存在于快照中，再记录引用位置及结论版本。工具 schema 枚举当前合法约束键，不能用结论 ID 代替；空依赖还必须提供 `independence_reason`。许可证结论单独保存，依赖 `license`，不与部署建议混成一整段文章。

**作用：**结论有独立身份和依据，程序才能单独标记和修订。引文不存在时拒绝写入并返回可修正的工具反馈；运行版本过期则硬停止。但引用存在仍不等于推理正确。

### 第三步：用户改变需求，程序先标记过期结论

用户现在要求：**数据不能离开内网，必须支持自托管。** 演示通过 `Store.amend()` 模拟用户动作；真实使用对应：

```bash
uv run --frozen agent4x task amend TASK_ID deployment '数据不能离开内网，必须支持自托管'
```

此时不调用模型。程序递增契约版本，并根据 `depends_on` 标记受影响条目：

| 结论 | 依赖 | 契约 v1 下 | 改为契约 v2 后 |
| --- | --- | --- | --- |
| D：允许托管时，可以考虑 Alpha | `deployment` | `current`，r1 | `needs_review`，仍为 r1 |
| L：两者符合示例许可证要求 | `license` | `current`，r1 | 保留 `current`，仍为 r1 |

**多出来的环节：**在生成新答案之前，旧建议已经被显式标记为待复核。此时生成的报告会把 D 标注为“历史结论，不是当前推荐”。

**作用：**不会在等待重新运行期间，继续把旧建议当作当前推荐；也没有把未变化的许可证结论伪装成已经重新验证。新增约束或未声明依赖时，程序采用更保守的复核规则。

### 第四步：Agent 按原结论 ID 修订，而不是覆盖整份报告

第二轮实际工具顺序：

```text
assess_task(assessment=ready)
    ↓ 根据入口快照确认契约 v2，并看到 D 的 needs_review 状态
read_source(source_id)
    ↓ 再次读取部署证据
save_claim(draft=更新后的部署结论, claim_id=原来的D的ID)
    ↓ 原 ID 新增 r2，保留 r1
最终回复：已修订部署建议，许可证结论未改写
```

更新后的 D 是：“内网约束下撤回对 Alpha 的推荐，改为建议支持自托管的 Beta。”

```text
结论 D：同一个 ID
  r1 / 契约 v1：允许托管时可以考虑 Alpha
  r2 / 契约 v2：撤回 Alpha，建议可自托管的 Beta

结论 L：仍为 r1 / 契约 v1，不冒充本轮重新核验
```

**多出来的环节：**结论修订保留旧版本和对应的契约版本，未受影响结论不重写。

**作用：**使用者既能看到最新建议，也能回答“为什么以前推荐 A，现在推荐 B”。本例展示定向修订流程，不代表已经证明它能降低真实任务的成本或提高质量。

### 第五步：程序出报告，用户决定是否保留经验

报告由 `render_report()` 读取账本生成，不再额外调用模型。演示输出的 `reports` 目录中有三个时点的报告：

| 文件 | 应重点观察的内容 |
| --- | --- |
| `01-initial.md` | 两条结论均为 r1，有来源、原文、引用位置和哈希 |
| `02-needs-review.md` | 只有部署结论待复核，并明确标为历史建议 |
| `03-revised.md` | 部署结论为 r2，保留 r1 历史；许可证结论仍为 r1 |

第一轮提出的经验仍是 `pending`，没有自动进入第二轮上下文。需要用户显式确认，才能供该项目后续任务参考：

```bash
# 替换为 demo 输出的 project、pending_memories 中的真实值
uv run --frozen agent4x memory list --project PROJECT_NAME
uv run --frozen agent4x memory confirm MEMORY_ID
```

**多出来的环节：**正式报告以已保存数据为准，经验是否启用由用户决定，而不是模型自己宣布“已经学会”。手动确认不属于离线演示自动执行的步骤。

### 哪些属于工具，哪些属于程序保障？

| 机制 | 由谁执行 | 本任务中的作用 |
| --- | --- | --- |
| `assess_task` + `TaskWorkflow` | Agent 评估，程序控制流程 | 首步仅开放评估工具；需澄清时保存问题并结束运行 |
| `get_task`、`read_source` | Agent 调用工具 | 获取需求版本和证据 |
| `save_claim` | Agent 提交，Store 校验写入 | 保存可引用、有依赖、有版本的结论 |
| 约束修改与失效标记 | 用户操作，Store 执行 | 无须模型判断即可把受影响条目标为待复核 |
| `propose_memory` / `memory confirm` | Agent 提议 / 用户确认 | 防止模型自行固化未经确认的经验 |
| `CallBudget`、工具白名单 | 执行中间件 | 每次运行检查调用预算和工具权限 |
| 可恢复工具反馈 | Schema / Store / 工具适配层 | 返回错误代码、原因和合法约束键，允许在原预算内纠正参数 |
| `AuditTrace` | 回调自动记录 | 留下模型、工具调用和错误的事件线索 |
| `render_report` | 普通程序函数 | 从账本导出报告，不重新自由编写事实和版本 |

可以执行 `uv run --frozen agent4x trace TASK_ID` 查看本次任务的事件。离线示例两轮分别调用模型 6 次、4 次，均为预设回复，不产生真实模型或搜索 API 费用。

本例没有触发澄清、资料搜索或联网搜索：`request_clarification` 用于信息不足时登记问题；`search_sources` 用于已有资料中查找片段；`search_web` 只在用户启用后注册。它们不是为了展示而强行加入每一次运行。

**边界：**真实模型是否完整登记结论、是否正确声明依赖、是否按原 ID 修订，仍需检查；当前不保证所有问题均已覆盖。事件记录也不等于可精确重放或断点恢复。这是一套可核查的业务闭环，不是自动事实认证或自主训练系统。

## 真实使用

建议先按 `docs/knowledge-base.md` 从场景模板建立真实选型任务；下面的通用任务命令仍可用于自定义场景。

将 `.env.example` 复制为本项目的 `.env`，在本机填入凭据；不要复制旧项目整份配置、上传或在聊天里提供 key。

```text
DEEPSEEK_API_KEY=本地填写
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic
TAVILY_API_KEY=仅在开启联网搜索时填写
```

先创建任务，记录命令输出的真实 ID，下面的 `TASK_ID`/`MEMORY_ID` 都需要替换：

```bash
uv run --frozen agent4x task new '根据给定资料选择部署方案' \
  --project my-team \
  --constraint deployment='允许托管' \
  --constraint license='接受示例宽松许可证'

uv run --frozen agent4x source add TASK_ID options.md --root examples
uv run --frozen agent4x run TASK_ID
uv run --frozen agent4x report TASK_ID

uv run --frozen agent4x task amend TASK_ID deployment '数据不能离开内网，必须支持自托管'
uv run --frozen agent4x report TASK_ID
uv run --frozen agent4x run TASK_ID
uv run --frozen agent4x report TASK_ID --output reports/revised.md
```

`run` 会发送任务、资料上下文和相关确认记忆到 DeepSeek，并产生 API 费用。程序默认不联网搜索；启用真实 Tavily：

```bash
uv run --frozen agent4x run TASK_ID --allow-search --max-model-calls 8 --max-tool-calls 18 --seconds 120
```

Tavily 返回内容标记为摘要，**本项目不自行抓取 URL，也不声称读过完整网页**。模型可能请求更多资料或不保存足够结论；结束状态 `stopped_unverified` 不能解释为任务成功。

## 澄清与记忆

入口强制调用 `assess_task`。若选择 `needs_clarification`，程序保存问题并直接结束本轮；后续读资料时调用 `request_clarification` 也进入相同等待状态。不是只要求模型在回复里记得追问。

等待期间返回状态 `needs_clarification`，直接重跑不会再次请求模型；用户用 `task amend` 补充或修改实际约束后才重新评估。相同值的修改不会解除等待。不是一个运行中阻塞等待的多用户聊天服务。

评估是否缺少关键信息仍依赖模型判断，程序不保证模型能识别全部缺口。参数、引用和来源错误可返回 `ok=false`、`retryable=true` 的工具反馈；版本过期、预算超限和越权工具仍硬停止，不能靠重试绕过。

```bash
uv run --frozen agent4x task show TASK_ID
uv run --frozen agent4x memory propose TASK_ID '本项目只考虑自托管方案'
uv run --frozen agent4x memory list --project my-team
uv run --frozen agent4x memory confirm MEMORY_ID
uv run --frozen agent4x memory edit MEMORY_ID '本项目默认优先自托管，例外需单独确认'
uv run --frozen agent4x memory confirm MEMORY_ID
uv run --frozen agent4x memory delete MEMORY_ID
uv run --frozen agent4x trace TASK_ID
```

Agent 没有 confirm/edit/delete 工具。删除后未来运行不再加载该记忆，但过去的模型输入/运行记录、数据库页或备份可能保留副本；不承诺取证级数据擦除。项目记忆不是模型权重更新。

## 边界与验收

```bash
uv run --frozen python -m unittest discover -s tests -v
```

验收只覆盖功能、错误和协议边界，不是研究实验或 benchmark。本轮 88 项测试与打包检查见 `docs/acceptance.md`；详细设计见 `docs/architecture.md`，数据/安全说明见 `SECURITY.md`。

- 文件只由用户显式导入：选定 root 内的非隐藏 `.md`/`.txt`，限制大小、禁止目录穿越与符号链接逃逸。
- 默认子 Agent、摘要与待办规划被此应用的模型 profile 禁用，内置文件/shell/task 工具从模型菜单排除，并由运行时白名单拒绝执行。
- 仍保留 DeepAgents 必需 scaffolding 与默认状态 backend，不授予模型宿主文件权限。
- 调用次数是执行器边界限制；`seconds` 为协作式检查，不能强制打断阻塞工具，不是精确费用上限。
- 单用户本地 SQLite，未实现多租户鉴权、Web UI、checkpoint 恢复、生产级沙箱或完整 prompt-injection 防护。
- 来源依赖、结论语义和未知事实仍需人工核验；没有自动“自我进化”保证。

## 发布与许可证

本仓库与学习/复现目录独立。不提交 `.env`、`.venv`、运行数据库、用户记忆或报告。

当前尚未选择许可证，因此公开可见本身不等于已授予开源使用许可。正式授予开源使用许可前，应选择合适许可证并核对依赖许可。

公开发布前审核暂存内容；不要使用 `git add -f` 上传运行数据或凭据。

## 来源与定位

本项目使用 DeepAgents 公共 API，自行实现任务契约、SQLite 账本、依赖失效、修订与确认记忆；没有复制上游研究应用代码，也不将框架自带能力宣称为原创。

- DeepAgents：`https://github.com/langchain-ai/deepagents`
- DeepSeek 兼容接口：`https://api-docs.deepseek.com/guides/anthropic_api`

这是应用工程项目，不包含 GRPO/PPO、PRM/ORM 训练、OPD 或论文实验平台。
