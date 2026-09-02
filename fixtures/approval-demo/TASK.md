# Approval Demo

这是一个专门演示 IntentFlow 单次授权机制的小项目。

当前收据模块依赖 `src/formatter.py`，但这个文件故意缺失，因此测试会在收集阶段失败。任务会匹配 Bug Fix Skill，而该 Skill 不默认开放 `create_file`。Agent 定位问题并尝试创建缺失文件时，应暂停并向用户申请一次授权。

推荐任务：

```text
修复当前项目的失败测试，不修改测试文件，运行 pytest -q 验证。
```

预期过程：运行测试 → 发现缺少 `src.formatter` → 请求创建文件授权 → 用户点击“同意并继续”或按 Enter → 创建文件 → 测试通过 → 完成任务。按 Esc 或点击“拒绝”可以测试拒绝后的重新规划。
