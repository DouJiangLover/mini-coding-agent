# TraceCoder

TraceCoder 是一个从零实现的本地编程智能体。用户提交任务后，模型只能通过本项目定义的工具观察和修改工作区；宿主程序负责对话历史、Skill 选择、工具解析、本地执行、安全边界、失败恢复、终止条件和事件记录。React 工作台通过 SSE 实时展示整个过程。

GitHub：https://github.com/NJULidong/mini-coding-agent

完整的工作原理、核心设计和模块框架见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
复杂故障实验的场景、真实运行轨迹和改进分析见 [`docs/FAILURE_LAB.md`](docs/FAILURE_LAB.md)。

本项目没有使用 LangChain、LlamaIndex、OpenAI Agents SDK、AutoGen 等 Agent 框架，也没有使用服务端托管的文件或代码执行工具。

## 已实现能力

- DeepSeek V4 / OpenAI-compatible 原生 Tool Calling，同时保留坏 JSON 参数错误反馈
- `list_files`、`read_file`、`search_text`、`create_file`、`apply_patch`、`run_command`、`finish` 七个本地工具
- Bug Fix、Test Writer、Documentation、Frontend Build 四个可插拔 Skill
- 独立 Skill 管理页：动态启用/停用内置能力，并通过表单添加可持久化的自定义 Skill
- 混合 Skill 路由：支持任务前手动指定，自动模式结合关键词候选与模型语义判断，低置信度时由用户确认
- TracePet 多任务伙伴：不同工作区任务可并行运行，集中展示进度，并提醒等待确认、完成与失败状态
- 独立 Agent 设置页：四种运行模式、三项质量关卡和四项执行预算均可本地调整
- Interaction-First 工作流：新建前端产品时先生成页面流转、状态机和验收标准，用户确认后才写代码
- 显式任务状态机、计划进度、可调步数预算和连续失败停止条件
- 观察、失败基线、根因定位、修改、验证、完成前自检六段质量闭环
- 质量关卡阻止盲目修改、未验证完成和未经复读的结果提交
- Skill 外操作与受限命令的单次授权：暂停、展示完整参数，按钮或 Enter/Esc 快捷键决策后继续原任务
- 工作区路径隔离、凭据文件保护、命令分级校验、超时和输出截断
- SSE 实时事件流、工具时间线、命令输出和文件 Diff
- JSONL 运行审计日志，保存在 `.tracecoder/runs/`
- 无 API Key 也能运行的离线演示模式

## 快速运行

需要 Python 3.11+、Node.js 22+。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm install
python dev.py
```

打开 [http://localhost:3000](http://localhost:3000)。后端 API 位于 `http://127.0.0.1:8000`。

点击“新建任务”后会先打开本地工作区选择器。可以从预置项目中选择，也可以浏览或输入 `TRACE_WORKSPACE_ROOT` 下的相对文件夹路径；确认工作区后再填写任务需求。左侧 TracePet 会统计所有后台任务。当前任务运行时仍可选择另一个工作区继续提交，原任务不会被停止。TracePet 任务中心显示每个任务的执行步骤、最近事件和状态，点击即可切换回来。为了避免文件冲突，同一工作区同一时间只允许一个运行中的任务。

点击工作台侧边栏的“Skill 管理”，或直接打开 [http://localhost:3000/skills](http://localhost:3000/skills)，可以调整 Agent 的可用能力。修改会保存在本地 `.tracecoder/skill-config.json`，不进入 Git；启用状态与新建 Skill 从下一个任务开始生效。

任务输入框底部可以选择“自动选择 Skill”或手动指定一项已启用能力。手动选择具有最高优先级；自动模式先通过触发词生成候选，再由真实模型结合任务语义、Skill 描述和候选得分作出选择。置信度低于阈值时任务会暂停，前端展示候选、推荐项和理由，用户确认后从原任务继续。

点击“Agent 设置”，或直接打开 [http://localhost:3000/settings](http://localhost:3000/settings)，可以选择标准、安全、自主或只读模式，并调整 Interaction-First、强制验证、完成前自检、最大步骤、失败上限、上下文预算和命令超时。配置保存在 `.tracecoder/agent-settings.json`，每个新任务启动时读取一次快照，运行中的任务不会被中途改变。

首次运行不需要 API Key：`TRACECODER_DEMO=auto` 会在没有密钥时启用离线演示。选择 `examples/calculator` 工作区，输入修复失败测试的任务，即可看到 Agent 实际执行测试、读取代码、修改文件并重新验证。

`examples/star-catcher` 是网页游戏修复用例；`examples/2048-game` 初始只有需求文档，可让 Agent 从零构建完整游戏；`examples/approval-demo` 只缺少一个金额格式化文件，用于在一分钟内演示“请求授权—允许一次—继续测试”的闭环；`examples/order-engine-lab` 则包含跨金额、库存、幂等和审计模块的复杂故障。工作区可以通过侧边栏的“重置演示项目”恢复初始状态，重要文件会先进入 `.tracecoder/reset-backups/`。

Frontend Build 任务会先停在“交互流程确认”：前端展示页面流转、状态变化和验收标准。选择“符合预期，继续实现”后 Agent 才进入代码阶段；选择“需要调整”并填写意见，会生成完整的新版本再次确认。该确认仅用于从需求构建新产品，不打断普通修复和文档任务。

## 使用真实模型

复制环境变量模板，但不要提交真实凭据：

```bash
cp .env.example .env
```

默认已按 DeepSeek 配置。在本地 `.env` 或终端环境中设置：

```text
TRACECODER_DEMO=false
DEEPSEEK_API_KEY=你的密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
```

当前后端直接调用 DeepSeek 的 OpenAI-compatible `chat/completions` 接口，并在工具循环中使用非思考模式。也可通过 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL` 接入其他兼容服务。API Key 只用于请求头，不进入日志、模型上下文或前端事件。

## 核心流程

```text
接收任务
  → 手动指定 Skill，或由 SkillRouter 候选评分 + 模型语义路由选择
  → 低置信度时等待用户从候选项中确认
  → Frontend Build 先生成终端用户交互模型并等待确认/修改
  → AgentLoop 构造系统约束、任务与工具 schema
  → 先理解项目；修复类任务运行测试建立修改前基线
  → 模型返回原生 tool_calls
  → ToolRegistry 校验；需要额外权限时暂停并等待用户单次授权
  → 授权通过后执行原始动作，拒绝则把结果反馈给模型重新规划
  → 修改后强制验证，并在首次 finish 时进入完成前自检
  → 结构化结果写回对话上下文
  → EventStore 记录并通过 SSE 推送前端
  → 继续执行，直到 finish、取消、超步数或连续失败
```

前端只展示事件，不参与 Agent 决策。模型也不能直接读写文件或运行命令；所有副作用都经过 ToolRegistry 和 WorkspaceGuard。

## Skill 格式

每个 Skill 是 `skills/<name>/` 下的一组元数据和专用提示词：

```text
skill.json  名称、关键词、允许工具、可视化计划
prompt.md   该领域的执行策略
```

路由采用“手动优先、自动混合”的策略。自动模式使用关键词评分缩小候选范围，真实模型只读取任务和候选元数据进行语义选择，不会在路由阶段接触文件工具。选择结果会记录关键词、路由方式、理由和置信度。选中 Skill 后，它的提示词、执行计划和最小工具权限会动态装配进当前任务上下文。

管理页允许填写名称、适用场景、触发词、执行策略和默认工具权限。自定义 Skill 不会绕过宿主安全策略；未默认开放的工具仍需单次授权，工作区越界和凭据访问仍然直接拒绝。系统也会阻止用户停用最后一个可用 Skill。

## 安全设计

- API 只接受 `TRACE_WORKSPACE_ROOT` 下的相对工作区
- 不同工作区允许并行执行；相同或父子目录重叠的工作区互斥，避免两个 Agent 同时修改相同文件
- 每次文件访问都会在解析软链接后再次检查边界
- 拒绝 `.env`、`.git`、密钥证书等敏感路径
- `create_file` 只创建受支持的 UTF-8 文本文件，拒绝覆盖已有文件并限制单文件大小
- 命令使用参数数组执行，不经过 shell，因此管道和命令拼接不会生效
- 常见开发命令默认执行；Skill 外工具、内联脚本、Git 写操作等需要用户逐次授权
- 安全模式会为写文件和运行命令逐次确认；只读模式从宿主层禁止写文件；自主模式仍不能绕过硬安全边界
- 工作区越界、凭据文件和高风险删除参数属于硬边界，授权也不能绕过
- 单条命令超时可在 5–60 秒间调整，文本输出最多保留 12,000 字符
- 同一个工具调用连续出现三次会被阻止
- 连续失败上限可在 1–10 次间调整；达到失败或最大步骤预算时安全停止
- 测试未通过属于正常诊断反馈，不计入连续工具失败

该安全层是轻量级保护，不等同于容器或操作系统级沙箱。请只把可信项目目录放入工作区。

## 测试

```bash
python -m pytest -q
npm run build
```

测试覆盖 Skill 路由、低置信度确认、多任务状态、交互模型确认与修订、确认前零文件改动、路径越界、凭据保护、安全创建文件、补丁唯一匹配、授权允许/拒绝恢复，以及离线 Agent 从失败测试到修复验证的完整闭环。

## 主要目录

```text
app/                 React 可视化工作台
app/settings/        Agent 模式、质量关卡和执行预算设置页
backend/agent/       Agent 循环、状态推进和上下文裁剪
backend/settings.py  配置校验、原子持久化与任务快照
backend/llm/         OpenAI-compatible 与离线演示模型客户端
backend/tools/       本地工具定义、校验和执行
backend/workspace/   工作区安全边界
backend/events/      JSONL 审计与 SSE 事件源
skills/              可插拔能力包
examples/calculator/ 可重复演示的失败测试项目
examples/star-catcher/ 可试玩的网页游戏与 JavaScript 故障测试
examples/2048-game/ 仅含需求文档的从零构建工作区
examples/approval-demo/ 单次授权的最小演示
examples/order-engine-lab/ 复杂订单引擎失败实验
tests/               单元和集成测试
```

## 可答辩的设计取舍

1. **SSE 而不是 WebSocket**：当前通信是单向事件推送，SSE 实现更少、自动重连、足够可靠。
2. **创建与修改分离**：`create_file` 只负责不存在的新文件，已有文件必须通过唯一文本补丁修改，从协议层避免静默覆盖。
3. **显式状态机而不是无限循环**：状态、步数、重复动作和失败计数共同提供可解释的停止边界。
4. **Skill 只提供策略与权限**：Agent 内核不依赖具体任务类型，新 Skill 不需要修改循环代码；手动或语义路由只决定装配哪一项能力。
5. **事件流与决策解耦**：可视化故障不会影响 Agent，JSONL 还能用于回放和复盘。
6. **交互先于代码**：Frontend Build 将“需求 → 代码”拆成“需求 → 用户交互模型 → 人工确认 → 实现 → 对照验证”，降低需求理解偏差。
7. **可调策略不等于可调安全边界**：设置页控制工作节奏和自主程度，路径隔离、凭据保护与危险命令拦截始终由宿主强制执行。
