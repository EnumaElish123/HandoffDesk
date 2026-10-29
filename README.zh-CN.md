# HandoffDesk · 项目续接卡

**让 Agent 保存进度，让人带着正确的文件继续工作。**

HandoffDesk 把“这次实际完成了什么、下一步做什么、有什么阻塞、验证到什么程度，以及对应哪些文件”保存在本机。Agent 通过简化命令记录，Windows 用户在本地 Web 桌面窗口阅读，Linux 用户在终端阅读器中查找历史与文件。

[English](README.md) · [完整使用手册](docs/usage.zh-CN.md) · [Agent 接入](docs/agents.md) · [Ubuntu 桌面与服务器](docs/ubuntu.md)

## 能做什么

- 一个项目管理多条工作线，各自保留计划、交接、草稿、历史和来源复核。
- 按当前工作、历史交接、自建进度节点查看文件名称、用途、完整位置和可用状态。
- 关联文件或文件夹、搜索、复制路径、打开文件、定位资源管理器；文件搬家后重新定位，移除关联保留原文件。
- Agent 一次提交进度和文件清单，支持协议发现、预检查、重试去重、并发冲突检测和作者标注。
- 读取明确登记的本地文档、JSON 字段和 Git 状态，比较来源变化，导出本机 Markdown 摘要。
- Windows 本地 Web 桌面与 Linux TUI 共用阅读模型，支持搜索、未读、完整详情、附件预览和 Markdown 导出。
- 一次连接 Codex，安装用户级 Skill 和可选 hooks；任务自动绑定项目，保存失败或未汇报可恢复。首次 hook 信任由 Codex 管理。

当前桌面主要使用中文；命令接口返回 JSON。

## 先试用

源码开发需要 Python 3.10+。以下安装当前源码，不假设已发布到 PyPI。Windows 的 Web 桌面使用 WebView2 Runtime；Linux TUI 无需 Tk、X11 或浏览器。

```sh
git clone https://github.com/EnumaElish123/HandoffDesk.git
cd HandoffDesk
python -m pip install '.[tui]'
handoffdesk tui
```

Windows 将安装参数改为 `'.[desktop]'`，运行 `handoffdesk desktop`。阅读器首次打开为空状态，不需要创建示例项目。开发时也可运行 `handoffdesk desktop --browser`，只在本机浏览器预览，关闭命令后停止服务。旧 Tk 窗口保留为 `handoffdesk legacy-desktop`。

连接 Codex：

```sh
handoffdesk connect codex
handoffdesk doctor --probe
```

连接器保留已有配置，不覆盖 AGENTS.md。按 Codex 提示信任 hooks；健康检查不会仅凭安装文件存在宣称全自动就绪。完整步骤、能力限制与撤销见 [低配置接入](docs/agent-first.md)。如果只想手动接入项目，可继续使用兼容命令：

```sh
python -m handoffdesk init --project alpha --name "我的项目" --root /absolute/path/to/your-project
```

将路径替换为已有的本机绝对目录；Windows 示例为 `C:\Projects\Alpha`。已有配置时，`init` 会拒绝覆盖，添加项目用：

```sh
python -m handoffdesk project add beta --name "另一个项目" --root /absolute/path/to/another-project
```

从任意目录调用可先在源码目录运行 `python -m pip install .`，随后使用 `handoffdesk` 命令。这里安装的是当前源码，不假设项目已发布到 PyPI。

## Linux 安装与阅读

从源码希望一次安装并接入时，运行 `python scripts/install.py`。安装器沿用已登记的记录目录，并输出立即可用的启动命令；`--no-connect` 仅安装阅读器。已有同名命令会保留，首次 hooks 信任仍由 Codex 确认。`handoffdesk doctor --human --probe` 显示当前接入状态和下一步。

终端阅读支持任务筛选、按任务名搜索，以及小窗口附件阅读。`/` 搜索、`p` 项目、`t` 任务、`u` 未读、`f` 附件、`e` 导出、`c` 接入状态、`y` 复制续接提示、`x` 清空筛选、`?` 帮助。附件中 Enter 阅读，PgUp/PgDn 翻页，`m` 查看位置/用途，Esc 返回。导出需确认位置，已有文件不会被覆盖。

时间默认按系统时区显示并带 UTC 偏移；远端系统时区与自己不同时，可选用 `HANDOFFDESK_TIMEZONE=Asia/Shanghai handoffdesk tui`。状态和附件均属于运行工具的机器；终端复制到本机剪贴板取决于终端支持。

## 日常怎么用

Agent 新上下文先用 `agent sessions --cwd …` 发现已有任务；继续任务用 `agent resume --session …`，独立新任务才创建会话。恢复默认只返回操作摘要，完整提案按 checkpoint 取得。Agent 开始新任务时运行 `agent begin --cwd … --task …`，之后每个有意义的阶段提交一次 `agent checkpoint`，收到回执后立即向用户汇报记录编号、验证范围和文件数。程序处理项目、任务绑定、版本和重试。纯问答不生成空进展记录。

用户打开阅读器查看“最新进展”，筛选项目或未读，选择阶段查看完整正文和本次附件。新记录自动进入列表，当前详情保持不动。打开一条记录只确认该条已读，不清空其他项目的未读。

低配置接口见 [低配置接入](docs/agent-first.md)，原协议 1 见 [Agent 接入](docs/agents.md)。工具不采集全部聊天记录；Skill-only 模式的调用取决于 Agent，不能保证每轮自动执行。

## 数据在哪里

默认配置和 SQLite 数据库保存在：

| 系统 | 默认状态目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\HandoffDesk` |
| Ubuntu | `$XDG_DATA_HOME/handoffdesk`，未设置时为 `~/.local/share/handoffdesk` |

可以用绝对路径的 `HANDOFFDESK_HOME`，或命令前的 `--app-dir` 指定目录；显式 `--app-dir` 优先。Agent 与桌面应使用同一目录。状态和数据库不能放进接入的项目根目录。

打开既有状态目录时，数据库版本 4 自动备份后升级为 5，保留原始交接内容和编号。不要用旧程序写升级后的数据库；回退使用迁移前备份。安装登记用于所有入口定位同一状态，优先级为显式 `--app-dir`、`HANDOFFDESK_HOME`、安装登记、平台默认。旧安装路径接入见 [配置与迁移](docs/configuration.md)。

文件关联只保存位置和用途，不复制业务文件；登记来源的快照可能缓存文档节选。工具没有跨机器自动同步或文件传输功能。实际隐私边界及发布前检查见 [隐私说明](docs/privacy.md)。

## 测试与许可证

服务器运行 `python tests/run_tests.py --headless`；有 Tk 的桌面运行 `python tests/run_tests.py`。桌面打开文件和定位文件管理器还需要实际图形会话。

本项目采用 [MIT 许可证](LICENSE)。开发与发布说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，依赖和资产说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
