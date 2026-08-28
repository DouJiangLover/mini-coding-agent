# TraceCoder

TraceCoder 是一个从零实现的本地编程智能体。用户提交任务后，模型只能通过本项目定义的工具观察和修改工作区；宿主程序负责对话历史、Skill 选择、工具解析、本地执行、安全边界、失败恢复、终止条件和事件记录。React 工作台通过 SSE 实时展示整个过程。

GitHub：https://github.com/NJULidong/mini-coding-agent

完整的工作原理、核心设计和模块框架见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

本项目没有使用 LangChain、LlamaIndex、OpenAI Agents SDK、AutoGen 等 Agent 框架，也没有使用服务端托管的文件或代码执行工具。

## 已实现能力

- DeepSeek V4 / OpenAI-compatible 原生 Tool Calling，同时保留坏 JSON 参数错误反馈
- `list_files`、`read_file`、`search_text`、`create_file`、`apply_patch`、`run_command`、`finish` 七个本地工具
- Bug Fix、Test Writer、Documentation、Frontend Build 四个可插拔 Skill
- 显式任务状态机、计划进度、30 步执行预算和连续失败停止条件
- 工作区路径隔离、凭据文件保护、命令白名单、超时和输出截断
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

首次运行不需要 API Key：`TRACECODER_DEMO=auto` 会在没有密钥时启用离线演示。选择 `examples/calculator` 工作区，输入修复失败测试的任务，即可看到 Agent 实际执行测试、读取代码、修改文件并重新验证。

`examples/star-catcher` 是网页游戏修复用例；Agent 会复现一条连击计分测试失败，定位 JavaScript 状态更新错误，应用局部补丁并重新运行 `npm test`。`examples/2048-game` 初始只有一份 `REQUIREMENTS.md`，使用真实模型时可由 Frontend Build Skill 自主规划、创建并测试完整游戏。三个工作区都可以通过侧边栏的“重置演示项目”恢复初始状态；2048 的生成文件会被移动到 `.tracecoder/reset-backups/`，而不是直接删除。

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
  → SkillRouter 关键词评分选择策略和工具权限
  → AgentLoop 构造系统约束、任务与工具 schema
  → 模型返回原生 tool_calls
  → ToolRegistry 校验并在本地执行
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

第一版采用可解释的关键词评分路由。选中 Skill 后，它的提示词和最小工具权限会动态装配进当前任务上下文。

## 安全设计

- API 只接受 `TRACE_WORKSPACE_ROOT` 下的相对工作区
- 每次文件访问都会在解析软链接后再次检查边界
- 拒绝 `.env`、`.git`、密钥证书等敏感路径
- `create_file` 只创建受支持的 UTF-8 文本文件，拒绝覆盖已有文件并限制单文件大小
- 命令使用参数数组执行，不经过 shell，因此管道和命令拼接不会生效
- 只允许常见开发命令；Git 仅允许只读子命令
- 命令最长运行 60 秒，文本输出最多保留 12,000 字符
- 同一个工具调用连续出现三次会被阻止
- 连续三次工具失败或达到最大步骤时安全停止

该安全层是轻量级保护，不等同于容器或操作系统级沙箱。请只把可信项目目录放入工作区。

## 测试

```bash
python -m pytest -q
npm run build
```

测试覆盖 Skill 路由、路径越界、凭据保护、安全创建文件、补丁唯一匹配、危险命令拒绝，以及离线 Agent 从失败测试到修复验证的完整闭环。

## 主要目录

```text
app/                 React 可视化工作台
backend/agent/       Agent 循环、状态推进和上下文裁剪
backend/llm/         OpenAI-compatible 与离线演示模型客户端
backend/tools/       本地工具定义、校验和执行
backend/workspace/   工作区安全边界
backend/events/      JSONL 审计与 SSE 事件源
skills/              可插拔能力包
examples/calculator/ 可重复演示的失败测试项目
examples/star-catcher/ 可试玩的网页游戏与 JavaScript 故障测试
examples/2048-game/ 仅含需求文档的从零构建工作区
tests/               单元和集成测试
```

## 可答辩的设计取舍

1. **SSE 而不是 WebSocket**：当前通信是单向事件推送，SSE 实现更少、自动重连、足够可靠。
2. **创建与修改分离**：`create_file` 只负责不存在的新文件，已有文件必须通过唯一文本补丁修改，从协议层避免静默覆盖。
3. **显式状态机而不是无限循环**：状态、步数、重复动作和失败计数共同提供可解释的停止边界。
4. **Skill 只提供策略与权限**：Agent 内核不依赖具体任务类型，新 Skill 不需要修改循环代码。
5. **事件流与决策解耦**：可视化故障不会影响 Agent，JSONL 还能用于回放和复盘。
