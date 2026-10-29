# Linux 终端使用

Linux 的默认入口是 TUI，不需要 Tk、X11 或浏览器。日常流程是：安装一次、连接 Agent、让 Agent 完成工作并记录，然后打开阅读器。项目由 Agent 根据工作目录登记，首次使用无需 `init` 或编辑配置。

## 安装与首次使用

从源码安装需要 Python 3.10+；在源码根目录执行：

```sh
python3 scripts/install.py
```

脚本建立独立用户环境，安装终端组件并准备 Codex 接入文件。结束时会给出可直接复制的绝对启动命令，即使用户命令目录不在 PATH 中也能立即使用。Linux 默认程序目录为 `~/.local/lib/handoffdesk`，并尝试创建归本安装管理的 `~/.local/bin/handoffdesk`；遇到其他来源的同名命令或用户修改会保留它。改变程序目录时，只有能由旧登记、清单和内容摘要证明为本工具未修改入口的命令才会转交新安装。若入口已在 PATH 中：

```sh
handoffdesk tui
handoffdesk doctor --human --probe
```

按 Codex 提示确认首次 hooks 信任，再开启新对话。没有 hooks 支持时使用 `handoffdesk connect codex --skill-only`。普通用户不需要给每个项目重复安装或复制 Skill。安装时选择 `--no-connect` 则只安装阅读器，之后可运行 `handoffdesk connect codex`。

如果只是开发当前源码，使用虚拟环境安装 `python -m pip install '.[tui]'`，随后执行 `handoffdesk connect codex`。便携版已包含运行时和 TUI 组件，解压后运行包内的 `./handoffdesk`；不要给便携版再安装 Python 组件。已验证的发行环境以对应构建验收记录为准。

Ubuntu 20.04 的默认 Python 3.8 不满足源码要求；准备独立 Python 3.10+，或使用适配系统的便携包，不要替换系统 Python。程序不会修改 SSH 配置、shell 登录配置或安装系统软件。

## 阅读和继续工作

Agent 在实际完成阶段后保存记录，立即汇报记录编号、成果、验证范围和文件。用户从终端阅读这些内容：

- `/` 搜索；项目/任务条件用于缩小结果；`u` 筛选未读。
- 选择记录并按 Enter 阅读，Esc 返回列表；新记录更新时保留阅读位置。
- `f` 阅读附件。路径指向运行 HandoffDesk 的机器；复制到本机剪贴板取决于终端支持。
- `?` 查看当前快捷键。可从详情导出 Markdown，也可用下方 CLI。

需要继续任务时，告诉 Agent 继续该项目中的具体工作。Agent 可先通过 `agent sessions --cwd /absolute/project` 发现既有任务，再明确恢复；用户无需搬运内部会话令牌。记录的附件是文件引用，正文以磁盘当前版本为准，不是文件备份。

## 无交互终端和脚本调用

TUI 需要交互式 TTY。自动任务、管道或非交互 SSH 使用无需界面的命令：

```sh
handoffdesk feed
handoffdesk feed --query '任务名称'
handoffdesk read '实际记录编号' --markdown
```

`feed` 首次运行可以返回空列表，不创建演示项目。`read --ack` 可明确将该条标为已读；普通查询不会把所有记录清为已读。Agent 的记录命令独立于终端组件，接口说明见 [低配置接入](agent-first.md)。

## 接入状态与排错

```sh
handoffdesk doctor --human --probe
```

中文诊断分别说明阅读、Skill 文件、CLI 检测、hooks、宿主信任及当前进程写权限，并给出针对当前问题的下一步。默认 `doctor` 仍返回 JSON，供 Agent 使用。

找不到 `codex` 命令不等于没有桌面宿主；应在实际使用的 Codex 任务中运行探针。文件已配置不等于宿主已信任。手动 handler 调用和当前进程权限通过也不能证明自动触发成功。状态目录与 Agent/阅读器应在同一机器；默认不跨机器同步。

## 更新与移除入口

再次执行源码安装器会按显式 `--state-dir`、`HANDOFFDESK_HOME`、已有安装登记、平台默认的顺序定位记录。普通重装沿用已登记目录；登记损坏时会停止并指出文件，避免切到空目录。`--check` 仅检查目标，不安装或下载。`--prefix` 为程序目录，与记录目录分离。

`handoffdesk disconnect codex` 只移除未被用户改动的 Skill 和本工具对应的 hooks，保留记录、备份及用户修改。

需要移除本源码安装创建的用户命令入口时，可从源码运行：

```sh
python3 scripts/install.py --remove-launcher
```

自定义程序目录应同时提供同一个 `--prefix`。此操作只移除未修改的用户入口，不删除程序或记录；用户替换过的同名命令会保留。

## 旧版兼容与手工管理

以下接口保留给已有工作流，首次自动接入无需执行：

- `init`：显式创建配置；不带项目参数会创建通用示例。
- `project add`：手工登记项目，适合不使用 Agent 自动登记的情况。
- `record schema/context/validate/save`：兼容协议 1，见 [Agent 指南](agents.md)。
- `legacy-desktop`：旧 Tk 窗口，依赖匹配的 Tk、字体及实际可连接的图形会话。Linux 默认 TUI 无这些要求。

可选 `desktop --browser` 仅提供本机回环地址的 Web 预览，不自动建立 SSH 隧道或同步服务器文件。远端附件需要自行传输/挂载才能在其他机器打开。

## 验收范围

服务器可运行 `python tests/run_tests.py --headless`。发行前仍需在声明支持的目标系统和真实 SSH/tmux 中检查首次安装、升级保留历史、尺寸变化、退出恢复以及剪贴板支持/不支持情况；本机 PTY 通过不等于这些环境全部通过。

更多内容：[安装与 Agent 接入](agent-first.md)、[配置与迁移](configuration.md)、[隐私说明](privacy.md)。
