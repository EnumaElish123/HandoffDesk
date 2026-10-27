# HandoffDesk · 项目续接卡

**让 Agent 保存进度，让人带着正确的文件继续工作。**

HandoffDesk 把“这次实际完成了什么、下一步做什么、有什么阻塞、验证到什么程度，以及对应哪些文件”保存在本机。Agent 通过结构化命令记录，人类通过桌面窗口阅读、查找并打开每个阶段的文件。

[English](README.md) · [完整使用手册](docs/usage.zh-CN.md) · [Agent 接入](docs/agents.md) · [Ubuntu 桌面与服务器](docs/ubuntu.md)

## 能做什么

- 一个项目管理多条工作线，各自保留计划、交接、草稿、历史和来源复核。
- 按当前工作、历史交接、自建进度节点查看文件名称、用途、完整位置和可用状态。
- 关联文件或文件夹、搜索、复制路径、打开文件、定位资源管理器；文件搬家后重新定位，移除关联保留原文件。
- Agent 一次提交进度和文件清单，支持协议发现、预检查、重试去重、并发冲突检测和作者标注。
- 读取明确登记的本地文档、JSON 字段和 Git 状态，比较来源变化，导出本机 Markdown 摘要。
- Windows 和 Ubuntu 桌面可视化使用；Ubuntu 服务器无界面记录和查询。应用本身不提供网络服务。

当前桌面主要使用中文；命令接口返回 JSON。

## 先试用

准备 Python 3.10 或更高版本；桌面还需要同一解释器能使用 Tk。Ubuntu 命令通常使用 `python3`，依赖安装见 [Ubuntu 说明](docs/ubuntu.md)。

```sh
git clone https://github.com/EnumaElish123/HandoffDesk.git
cd HandoffDesk
python -m handoffdesk init
python -m handoffdesk desktop
```

首次 `init` 在用户数据目录生成通用演示项目和本机配置。它不会读取你的其他项目，也不会导入历史记录。若首次就接入真实项目，用以下命令代替无参数的 `init`：

```sh
python -m handoffdesk init --project alpha --name "我的项目" --root /absolute/path/to/your-project
```

将路径替换为已有的本机绝对目录；Windows 示例为 `C:\Projects\Alpha`。已有配置时，`init` 会拒绝覆盖，添加项目用：

```sh
python -m handoffdesk project add beta --name "另一个项目" --root /absolute/path/to/another-project
```

从任意目录调用可先在源码目录运行 `python -m pip install .`，随后使用 `handoffdesk` 命令。这里安装的是当前源码，不假设项目已发布到 PyPI。

## 日常怎么用

Agent 开始工作时读取明确的项目和工作线，完成阶段或需要交接时提交事实、验证范围、下一步和文件清单。人类打开窗口，点击“有新进度，点击更新”，查看首页的进度和“本次文件”。选择历史节点即可回到那个阶段的文件清单。

首次接入 Agent 的完整命令、提交 JSON、重试规则和可复制的项目约定见 [Agent 接入](docs/agents.md)。把约定交给 Agent 后，仍需要它实际执行命令；工具不会自动监听或保存其他会话。

## 数据在哪里

默认配置和 SQLite 数据库保存在：

| 系统 | 默认状态目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\HandoffDesk` |
| Ubuntu | `$XDG_DATA_HOME/handoffdesk`，未设置时为 `~/.local/share/handoffdesk` |

可以用绝对路径的 `HANDOFFDESK_HOME`，或命令前的 `--app-dir` 指定目录；显式 `--app-dir` 优先。Agent 与桌面应使用同一目录。状态和数据库不能放进接入的项目根目录。

开源版与原个人安装独立使用，不自动迁移旧数据。旧版升级、备份和多机器路径设置见 [配置与迁移](docs/configuration.md)。

文件关联只保存位置和用途，不复制业务文件；登记来源的快照可能缓存文档节选。工具没有跨机器自动同步或文件传输功能。实际隐私边界及发布前检查见 [隐私说明](docs/privacy.md)。

## 测试与许可证

服务器运行 `python tests/run_tests.py --headless`；有 Tk 的桌面运行 `python tests/run_tests.py`。桌面打开文件和定位文件管理器还需要实际图形会话。

本项目采用 [MIT 许可证](LICENSE)。开发与发布说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，依赖和资产说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
