IntentFlow——交互优先、可验证的本地编程智能体

Git 仓库：https://github.com/DouJiangLover/mini-coding-agent

一、项目定位
IntentFlow 是我独立设计和实现的交互优先本地 Coding Agent。它不是在 Claude Code、Codex 等现成产品外包装界面，也未使用 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 等 Agent 框架。大语言模型只负责判断下一步行动并生成结构化工具参数；文件读取、代码修改、命令执行、权限判断、状态推进、历史管理和完成判断均由本项目的本地宿主程序实现。

二、运行方式
需要 Python 3.11+、Node.js 22+。在项目根目录依次执行：
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm install
python dev.py
访问 http://localhost:3000。无密钥时可运行离线示例；使用真实模型时，参照 .env.example 在未入库的 .env 中配置 DEEPSEEK_API_KEY。密钥不会进入前端、日志或模型上下文。

三、特色功能
1. 不使用 Agent 框架或服务端文件、代码执行工具；自行实现模型请求、Tool Calling 解析、对话历史、循环控制和错误恢复。模型只选择行动，宿主在本地受控执行。
2. Interaction-First 工作流：从零构建产品时先生成终端用户流程图，用户确认或修改后再写代码；普通修复任务自动跳过。
3. 自动组合多个 Skill；常驻上下文只保存能力描述，需要时再加载完整策略。用户可导入、启停自定义 Skill。
4. 将“已确认需求—实现文件—验证结果”连接成可视化证据链。独立 before/after Hook 根据真实工具结果执行基线、验证、自检和终端用户 README 交付关卡。
5. 运行中可发送 Steering 消息调整方向；消息在下一次模型决策前生效，但不能绕过权限和质量检查。
6. Session 采用可分支树结构；切换工作区或重启后仍可恢复历史。上下文超限时保留结构化目标、进度、文件、命令和错误。
7. 每个项目使用独立工作区；宿主拦截路径越界、凭据读取和危险命令，高风险操作支持单次授权。前端通过 SSE 展示计划、工具、Diff、命令输出和多任务进度。

四、核心工作流程
用户选择本地项目工作区并输入任务后，宿主先判断是否属于从零构建终端产品。符合条件时进入 Interaction-First 阶段：读取需求资料，生成终端用户页面流转图和验收标准，等待用户确认或提出修改；修复、解释和局部调整等任务会自动跳过该阶段。

随后 SkillRouter 根据触发词产生候选，真实模型再从候选描述中选择一至三项互补 Skill。Skill 的完整提示词不会全部常驻上下文，模型只有确实需要时才调用 load_skill 获取，从而降低多 Skill 组合的上下文占用。系统目前内置 Bug Fix、Test Writer、Documentation 和 Frontend Build，也允许用户导入 ZIP、JSON 或 Markdown 格式的自定义 Skill。

进入执行阶段后，AgentLoop 循环调用模型。模型返回原生 tool_calls，宿主解析并验证 JSON 参数，先经过 before Hook，再交给 ToolRegistry 在本地执行。工具结果无论成功或失败都会作为新的 observation 写回消息历史，模型据此继续读取、修改、测试或调整方案。after Hook 只根据真实执行结果记录文件变化、验证命令和需求证据，不接受模型直接宣称“已经完成”。

调用 finish 时，宿主依次检查已确认需求是否具有实现和验证证据、修改后是否运行过成功验证、是否复读改动文件完成自检。对于从零构建的终端产品，还必须生成并检查根目录 README.md，写清功能、环境准备、启动方法、使用步骤和常见问题。任何关卡不满足时，finish 都会转换为结构化反馈，让 Agent 返回循环补齐证据，而不是直接结束。

五、上下文与持续交互
每个工作区拥有持久化 Session。一次任务对应树中的一个节点，“继续完成”会沿当前路线创建子节点，也可以从任意历史节点建立新分支而不覆盖原路线。上下文超过预算后，宿主保留结构化目标、计划进度、已读和已改文件、成功命令、最近错误及需求证据；原始运行事件仍保存在本地 JSONL 中。

任务运行时，输入框会切换为 Steering 模式。用户可以补充“不要修改后端”“优先适配移动端”等方向修正。消息在当前工具结束后的下一次模型决策前生效；若 Agent 正准备完成，宿主会先暂停完成并应用消息。Steering 不是权限凭证，不能绕过工作区限制、危险操作确认或质量关卡。

六、安全与可靠性
Agent 只能访问 INTENTFLOW_WORKSPACE_ROOT 下当前选中的相对工作区；解析软链接后仍会再次校验边界，并拒绝 .env、.git、证书和密钥文件。文件创建与修改分离：create_file 不能覆盖已有文件，apply_patch 要求旧文本唯一匹配并返回 Diff。命令以参数数组运行，不经过 Shell，管道、命令拼接和高风险删除会被限制；命令具有超时和输出截断。

标准模式按 Skill 最小权限执行；安全模式为写文件和命令逐次确认；只读模式从宿主层禁止写入；自主模式减少普通确认，但仍不能突破硬安全边界。同一工作区同一时间只允许一个活动任务，不同工作区可以并行运行。模型连接、解析和工具错误会转换为可观察反馈；重复动作、连续失败和最大步数提供明确停止条件，避免无限循环。

七、可视化与审计
React 前端不参与 Agent 决策，只订阅事件并展示当前阶段、执行计划、Skill 选择、BEFORE→TOOL→AFTER Hook 管线、权限请求、文件 Diff、终端输出、需求覆盖率和最终总结。FlowPet 汇总多个工作区任务的运行、等待确认、完成和失败状态。后端使用 SSE 推送实时事件，并将完整轨迹追加写入 .intentflow/runs/；Session 树保存在 .intentflow/sessions/，因此刷新页面、切换工作区或重启后端后仍可恢复。

八、推荐演示
选择 Calculator 工作区，输入“修复当前项目中失败的测试并确保全部通过”。Agent 会加载 Bug Fix Skill、查看目录、运行 pytest 建立失败基线、读取源码和测试、应用局部补丁、再次运行测试，并在完成前复读改动文件。运行过程中可发送 Steering：“不要扩大修改范围，只运行现有测试”，界面会分别显示消息已收到和已进入上下文。该过程能直观展示 Agent 不是一次性生成答案，而是根据真实工具反馈完成观察—行动—验证闭环。

也可以选择一个仅含需求文档的新工作区，要求构建小游戏或网页系统，演示“需求理解—终端用户流程确认—多 Skill 组合—项目实现—测试验证—README 交付”的完整软件工程过程。

九、实现说明与测试
前后端及 Agent 核心逻辑均在本仓库独立实现。运行 python3 -m pytest -q 可执行自动化测试。

主要模块：app/ 为可视化工作台；backend/agent/ 实现 AgentLoop、Hook 和上下文管理；backend/llm/ 调用 OpenAI-compatible API 并解析 Tool Calling；backend/tools/ 实现本地工具；backend/workspace/ 负责工作区隔离；backend/skills/ 负责 Skill 路由与导入；backend/events/ 负责 SSE 与运行审计；backend/sessions/ 负责 Session 树；backend/traceability/ 负责“需求—实现—验证”证据账本。

设计取舍：选择 SSE 而不是 WebSocket，是因为当前通信主要是后端向前端单向推送，SSE 更简单且支持自动重连；使用局部补丁而非整文件覆盖，是为了降低误删代码的风险并方便审查；将 Hook 与 Skill 分离，是为了让外部 Skill 只能提供策略和声明式权限，不能向 Agent 进程注入可执行代码；由宿主而非模型判断完成，是为了把“我认为完成了”变为可核查的文件、命令和测试证据。
