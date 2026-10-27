# Agent 接入与记录协议

适用于 HandoffDesk 0.9.0，记录协议版本 1。Agent 负责保存有证据的进度，人类负责查看与继续工作。工具不自动读取其他会话，也不安装任务结束钩子；接入方式是在项目约定或 Agent 工作流程中明确调用下面的接口。

## 准备调用位置

从源码目录使用 `python -m handoffdesk`，或安装当前源码后使用 `handoffdesk`。从其他工作目录调用时，使用已安装的命令或对应虚拟环境中的 Python；只设置 `--app-dir` 不会帮助 Python 找到未安装的源码包。

人类和 Agent 必须在同一台机器指向同一份状态与数据目录。可用 `HANDOFFDESK_HOME`，也可在命令前显式指定全局参数：

```sh
handoffdesk --app-dir /absolute/path/to/handoffdesk-state list --json
handoffdesk --app-dir /absolute/path/to/handoffdesk-state record context alpha --workline default
```

后续示例省略这段参数；实际执行时应保持一致。`--data-dir` 只覆盖数据库及导出目录，不改变项目配置目录，且同样应放在子命令之前。自动化验收应使用隔离数据目录，避免把测试记录写进人的正式进度。

## 标准流程

1. 用 `list --json` 与 `worklines alpha` 确认项目和工作线。所有 Agent 记录显式指定工作线，不依赖桌面的当前选择。
2. 调用 `record context alpha --workline default`，读取最新记录、`current_revision`、已登记根目录及节点。如需读取来源，另行 `refresh alpha --workline default --json` 并实际核对；不要把未检查的来源写成证据。
3. 完成一个具体阶段、发现重要阻塞或准备交接时，构造一次 JSON 提交。记录实际发生的事、第一步可执行的下一步、验证范围及本次相关文件。
4. `record validate` 预检查；成功后 `record save`。校验与保存之间仍可能发生并发变化，因此始终检查保存结果。
5. 检查退出码和 JSON 回执，通过 `files` 回读文件清单，向用户报告已保存的阶段与记录编号。

```sh
handoffdesk record schema
handoffdesk record context alpha --workline default
handoffdesk record validate --file /absolute/path/to/submission.json
handoffdesk record save --file /absolute/path/to/submission.json
handoffdesk nodes alpha --workline default
handoffdesk files alpha --node record:default:1
```

`record schema` 不需要项目初始化、数据库或 Tk。其他命令使用已初始化配置。`validate` 不保存交接及文件关联；常规服务初始化仍可能创建数据目录和 SQLite 数据库，因此不要把它作为“文件系统完全只读”的探针。

## 输入 JSON

完整合同以本版本 `record schema` 输出为准。格式模板见 [examples/agent-record.template.json](../examples/agent-record.template.json)；模板中的文字和路径是占位符，不能原样作为真实进度提交。

```json
{
  "schema_version": 1,
  "project": "alpha",
  "workline": "default",
  "operation_id": "a-unique-id-for-this-logical-submission",
  "expected_revision": 0,
  "record": {
    "title": "本阶段名称",
    "event_kind": "handoff",
    "last_done": "实际完成的动作、结果及必要限制",
    "next_action": "下一位可以直接开始的第一步",
    "blockers": []
  },
  "actor": {"name": "Codex"},
  "verification": {
    "status": "partial",
    "summary": "实际运行的检查及尚未覆盖的范围"
  },
  "files": [
    {
      "path": "/absolute/path/to/your-project/report.md",
      "purpose": "说明本阶段结果和已知限制",
      "role": "report"
    }
  ],
  "source_snapshot_id": null
}
```

| 字段 | 规则 |
| --- | --- |
| `schema_version` | 整数 `1`，与应用版本号分别维护 |
| `project`、`workline` | 使用已登记的项目 ID 和实际工作线 ID；默认工作线为 `default` |
| `operation_id` | 每次逻辑提交一个唯一值；不确定原提交是否成功时保留它原样重试 |
| `expected_revision` | 来自上下文的 `current_revision`，首次记录通常为 `0` |
| `record.title` | 简洁阶段名称，最多 120 字 |
| `event_kind` | `handoff` 表示实际交接；`plan` 只表示未来计划 |
| `last_done` | `handoff` 必须非空；`plan` 应省略或为空，不把计划写成完成 |
| `next_action` | 必须非空，写清下一次的第一步 |
| `blockers` | 文字列表，最多 100 项 |
| `actor` | 必须有 `name`；仅在已知真实值时提供可选的 `session_id`，未知则省略 |
| `verification` | 必须有状态及范围说明；状态是记录者报告，工具不重新执行检查 |
| `files` | 本次阶段的文件或目录，最多 500 项；每项含本机绝对 `path` 与 `purpose`，可选 `role` |
| `source_snapshot_id` | 仅引用本工作线实际检查过的快照 ID；不确定则 `null` 或省略 |

输入必须是 UTF-8 JSON，最多 1 MiB。也可通过 `record validate --stdin` 和 `record save --stdin` 读取标准输入。不要把 shell 文本拼接当成 JSON 编码；通过 JSON 库或 UTF-8 文件传递路径，避免空格、中文、引号和反斜线损坏。

`verification.status` 为以下之一：

- `passed`：描述范围内的检查实际通过。
- `partial`：只检查了部分范围，说明已验证和未验证部分。
- `not_run`：没有实际运行验证；说明原因或待验证项。
- `failed`：实际检查失败；说明错误和下一步。

测试成功不自动证明产品满足所有需求；科学实验执行成功也不自动证明科学结论成立。阶段记录应把这些边界写清楚。

## 文件清单的语义

一次 `save` 在同一 SQLite 事务内保存记录和文件清单，整体成功或整体不保存。新清单只包含本次 `files`，不自动混入当前工作或其他阶段的文件。文件路径会进行本机路径、存在性与重复检查；不接受网址、网络共享或设备路径。

目录作为一个入口，不递归列出内容。文件内容不复制到附件记录中，清单不能代替业务文件备份。Agent 应选择下一位续接真正需要的文件，为每个文件写用途；不要将整个磁盘或无关的用户资料作为附件。

## 回执、重试和冲突

成功的 `save` 返回 JSON，包含 `record_id`、`node_id`、`revision`、`file_count`、`files` 与 `replayed` 等字段。实际节点编号使用返回值，不从标题猜测。

| 退出码 | 含义 | 处理 |
| --- | --- | --- |
| `0` | 命令成功 | 解析 JSON；保存后回读节点和文件 |
| `2` | 输入、配置或环境错误 | 读取 `error.code` 和 `error.message`；修正后提交，保留不确定提交的重试语义 |
| `3` | 版本冲突 | 读取 `current` 与 `proposed`，重新获取上下文并比较双方内容后合并 |

相同逻辑提交原样重试时，保持 `operation_id` 和内容；已保存则返回 `replayed: true`，不会生成重复节点。文件后来移动也不会让已经成功的原请求生成另一份记录。同一编号携带不同内容会被拒绝；新的逻辑提交使用新编号。

冲突后不能只把 `expected_revision` 改成最新值再覆盖。先确认别人的更新与自己的实际结果是否都应保留，再形成新的交接。`record validate` 不锁定之后的版本。

## 可放入项目 Agent 约定的片段

下面片段是接入建议，不会由 HandoffDesk 自动安装。将 `alpha`、工作线和命令前缀替换为实际值；在修改项目文件前遵循项目自身的授权和约定。

```text
本项目使用 HandoffDesk 保存阶段交接。

记录目标：project=alpha，workline=default。
调用方式：handoffdesk（使用与人类桌面相同的 HANDOFFDESK_HOME 或 --app-dir）。

开始工作时先运行 record context alpha --workline default，读取最新 revision。
完成有意义的阶段、遇到重要阻塞或结束任务前，保存一次结构化交接：
实际完成、可执行的下一步、阻塞、作者、实际验证范围、相关本机文件及用途。
先 record validate，再 record save，检查退出码，并用回执 node_id 回读 files。
原提交结果不确定时原样重试；版本冲突先读取与比较内容，不强行覆盖。
不要猜测测试结论、文件位置或会话编号；没有验证时明确标记 not_run。
成功后向用户报告记录编号。工具不可用时明确说明失败，勿声称已保存。
```

任务结束钩子只能提醒或核验保存结果，不能替代 Agent 对真实进度的判断。HandoffDesk 本版本没有自动把上述约定写入其他项目或自动安装钩子的功能。

## 人类接收与多机器

桌面检测到外部保存后提示更新；首页显示作者与验证说明，“本次文件”进入对应阶段。Agent 提交不会改变人类窗口正在浏览的工作线。

服务器上的路径指向服务器本机。另一台电脑的桌面不会仅凭同名配置自动读到服务器数据库或文件；根目录映射不等于记录同步、文件传输或远程文件打开，见 [Ubuntu](ubuntu.md) 与 [配置](configuration.md)。
