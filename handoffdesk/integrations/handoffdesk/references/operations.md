# HandoffDesk 操作索引

把下面的操作接在主技能给出的本机启动命令后。全局选项放在子命令前。`P` 是项目 ID，`W` 是工作线 ID，`R` 是当前修订号。先用实际查询返回的 ID 替换示例。传给 CLI 的名称或路径作为单独参数处理。

## 阅读、搜索、导出

- “看看最近的进展”用 `feed --project P`。可加 `--workline W`、`--unread`、`--query TEXT`、`--limit N`，按返回的游标翻页。省略项目可看全部项目。用 `read ID` 看详情及附件；`read ID --markdown` 输出这一条的可读文本。仅在要求标为已读时加 `--ack`。
- “这个工作线当前怎样”用 `show P --workline W --json`。查看已登记来源的新状态用 `refresh P --workline W --json`，检查退出状态、warnings 和 last_attempt。历史观察不能证明作业现在仍在运行。
- “导出工作线摘要”用 `export P --workline W --format markdown`，返回工具写出的本机文件路径。若用户要导出**单条记录**，用 `read ID --markdown` 保存到指定文件或使用阅读器的导出操作。Markdown 含清单和路径；原附件单独存放。

## 工作线、材料和来源复核

- `worklines P list` 查 ID，`worklines P create NAME` 建工作线，`worklines P select W` 切换当前线。只查询另一条线时直接指定 `--workline W`。
- 从 `show` 的 entries 选择入口，用 `worklines P bind E1 E2 --workline W` 绑定 1–3 个已登记材料到下一步。用 `open P ENTRY --action copy` 查询材料路径，`source P SOURCE_ID --action copy` 查询来源路径；两者也支持 `folder` 和 `open`。在有图形会话的机器上打开或定位。
- “来源变了什么”先刷新，再 `changes P --workline W --json`。若要确认已复核，实际核对该工作线的新旧内容，取得当前修订及已检查的快照编号，执行 `review P --workline W --expect-revision R --snapshot S`。`reviews P --workline W` 回看复核历史。
- `focus P` 列出支持的关注任务，`focus P --job-id ID` 选定任务，再刷新。用空字符串恢复历史示例。根据实际候选操作。

## 计划、交接、历史与结构化提交

- `history P --workline W` 查版本，`record context P --workline W` 查 current_revision。通常按主技能走 Agent 会话；用户指定导入已有交接时，用 `handoff P --workline W --file FILE --expect-revision R --operation-id KEY`。UTF-8 JSON 包含 current_task、last_done、next_action 和 blockers（字符串数组）。导入内容应先与用户给出的材料核对。
- 只更新下一步用 `plan P --workline W --file FILE --expect-revision R --operation-id KEY`，填写 next_action 并留空 last_done。请求重试时复用同一操作编号和输入。版本冲突先读取并合并双方变动。
- 用户要求恢复时，先核对历史版本，再 `restore P --workline W --revision OLD --expect-revision R`。它会追加一个版本；用 history 核对新增版本及来源。
- 使用底层结构化记录协议时，先 `record schema` 和 `record context P --workline W`，按返回的字段构造 JSON；`record validate --file FILE` 通过后再 `record save --file FILE`，两者也支持 `--stdin`。同一成果只走一种保存流程，保存成功以回执为准。
- `nodes P --workline W` 列进度节点，`files P --node NODE_ID` 查该节点文件清单。使用上一步返回的节点 ID。用途中写明文件与阶段的关系。

## 项目、Agent 接入与诊断

- `init` 无参会创建演示项目；接入真实目录走主技能的 `init --project` 或 `project add`。现有项目用 `list`、`agent inspect` 验证，搬迁用 `agent relocate`，本机其他根目录映射用 `configure-roots`。修改查找位置保留历史文件引用。显式选择独立状态时用全局 `--app-dir`；接入命令使用该统一状态目录。
- `connect codex --skill-only` 安装或更新技能；要求使用宿主 hooks 时用 `connect codex` 并按返回的宿主信任提示操作。更新时沿用已选择的模式。用 `doctor --human` 查看接入、宿主和状态，用 `doctor --probe` 检查实际调用进程的写权限。用户要求断开接入时用 `disconnect codex`，已有项目记录保留。
- 日常记录的 `agent sessions`、`agent begin`、`agent resume`、`agent status`、`agent checkpoint`、`agent ack-report`、`agent conclude` 见主技能。宿主集成用 `agent observe` 关联真实宿主会话和轮次，用 `agent turn-status --turn TURN` 查该轮次状态。`integration-hook` 接收宿主事件和输入，留给宿主调用。

## 交互界面和附件管理

- `tui` 打开终端阅读器。项目页可添加项目；在记录列表搜索、筛未读、选择项目或任务，从列表或正文按 f 查看所选节点的附件、用途和完整路径，Enter 预览文件，Ctrl+C 复制路径。正文中可导出当前记录或复制续接提示。按 . 或 ? 浏览当前可用操作、各页面快捷键与全部 CLI 命令，Enter 查看参数或执行界面操作。Ctrl+P 按名称查找当前操作。
- `desktop` 打开本地 Web 桌面，`desktop --browser` 用本机浏览器。阅读器可筛选、搜索、查看详情和附件、标为未读、复制或导出。Windows 阅读器可对支持的普通文件保存当下的证据副本（每文件最多 10 MiB）；副本代表保存时的文件内容。
- `legacy-desktop` 打开兼容窗口。可编辑计划和交接草稿，在“节点文件”新建节点、关联文件或目录，从“更多”修改节点说明、文件用途、重新定位或移除关联。移除关联保留磁盘原件。对仅有界面入口的请求，引导用户到对应页面，实际操作后再确认完成。
- 备份或迁机时保留状态配置和一致的 SQLite 数据（可停止写入后复制状态目录），项目文件按需单独备份。新机器用本机根目录映射定位材料。不同机器不会自动同步。
