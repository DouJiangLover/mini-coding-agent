TraceCoder——可视化本地编程智能体

Git仓库：https://github.com/NJULidong/mini-coding-agent

运行：需要 Python 3.11+、Node.js 22+。执行 `python3 -m venv .venv`，激活环境后运行 `python -m pip install -r requirements.txt`、`npm install`、`python dev.py`，浏览器打开 http://localhost:3000。默认在无 API Key 时进入离线演示模式；点击“重置示例”和“开始运行”，Agent 会真实修复 examples/calculator 的失败测试。使用真实模型时，按 .env.example 在本地设置 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 和 TRACECODER_DEMO=false，凭据不得提交。

特色：项目未使用任何 Agent 框架或服务端代码执行工具，自行实现模型交互、原生 Tool Calling 解析、对话历史与上下文裁剪、Skill 路由、本地工具、循环终止和错误恢复。六个受控工具支持项目浏览、代码搜索、局部补丁和测试命令；WorkspaceGuard 阻止路径越界、凭据读取和危险命令。前端通过 SSE 实时展示 Skill 选择、计划状态、工具调用、命令输出、代码 Diff 和最终结果，运行轨迹同时保存为 JSONL，便于审计和回放。
