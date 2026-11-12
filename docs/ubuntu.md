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

## 从项目首页开始

运行 `handoffdesk tui` 后，用方向键选中项目，按 Enter 进入。项目内可以查看“项目全部进展”、选择一个任务，或新建任务。要跨项目阅读，回到首页选“全部进展”。已打开过的记录可以从“继续上次阅读”进入。

第一次登记现有工作目录时，选“添加项目”，粘贴目录的绝对路径，或选择“使用当前目录”。显示名称可以使用中文。检查路径后确认登记，进入项目并选择“新建任务”。任务内还没有记录时，让 Agent 在该项目目录按任务名称和编号继续工作、保存实际进展，返回阅读器刷新。

## 阅读和继续工作

| 想做什么 | 操作 |
|---|---|
| 阅读一条进展 | ↑↓ 选中，Enter 打开正文。宽屏可以先看选中项预览；提交已读发生在打开记录后。Esc 返回。 |
| 查找或筛选 | `/` 搜索记录，查看当前项目和任务范围。`u` 筛选未读，`n` 加载下一页，`r` 刷新。 |
| 找更多操作 | `Ctrl+P` 打开操作菜单，键入中文名称或关键词过滤。按 Enter 执行，Esc 关闭。`?` 显示可滚动的当前操作帮助。 |
| 查看附件 | 在已打开的记录中按 `f`，选中文件按 Enter 预览。`m` 查看位置与用途。`Ctrl+C` 请求终端复制路径。 |
| 导出或续接 | 在详情按 `e` 选择 Markdown 保存路径，或按 `y` 复制续接说明并交给 Agent。剪贴板受限时可从面板中选择原文，使用终端复制。 |

详情先显示下一步、阻塞、实际完成与验证；计划记录省略空的完成段。文件关联指向运行 HandoffDesk 的机器，路径、用途和核验信息可随记录阅读。导出包含完整记录信息。Agent 可以用 `agent sessions --cwd /absolute/project` 找到任务会话，核对任务编号后恢复。

## SSH 与 tmux

如果希望断开 SSH 后继续停留在原来的页面，可在远端的普通终端运行以下命令：

```sh
tmux new-session -A -s handoffdesk 'handoffdesk tui'
```

按 Ctrl+b，松开后按 d 暂时离开。重新连接 SSH 并再次运行同一条命令，就会回到原位置。已经配置持久阅读器的安装可直接使用其日常启动命令。`q` 退出阅读器。远程终端的剪贴板由客户端控制；复制面板会保留可供手工选取的文字。

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

先用 `handoffdesk list --json` 找到项目编号，再用 `handoffdesk worklines PROJECT list` 核对任务编号。以下命令中的 `PROJECT` 和 `TASK` 应替换为对应编号。

```sh
handoffdesk history PROJECT --workline TASK
handoffdesk changes PROJECT --workline TASK
handoffdesk reviews PROJECT --workline TASK
```

刷新来源会写入新快照。恢复历史时，先读历史并核对目标版本，再查看 `handoffdesk restore --help`；恢复操作会新增交接版本。已有工作流也可以使用以下入口。

- `init`：显式创建配置；不带项目参数会创建通用示例。
- `project add`：手工登记项目，适合不使用 Agent 自动登记的情况。
- `record schema/context/validate/save`：兼容协议 1，见 [Agent 指南](agents.md)。
- `legacy-desktop`：旧 Tk 窗口，依赖匹配的 Tk、字体及实际可连接的图形会话。Linux 默认 TUI 无这些要求。

可选 `desktop --browser` 仅提供本机回环地址的 Web 预览，不自动建立 SSH 隧道或同步服务器文件。远端附件需要自行传输/挂载才能在其他机器打开。

## 验收范围

服务器可运行 `python tests/run_tests.py --headless`。发行前仍需在声明支持的目标系统和真实 SSH/tmux 中检查首次安装、升级保留历史、尺寸变化、退出恢复以及剪贴板支持/不支持情况；本机 PTY 通过不等于这些环境全部通过。

更多内容：[安装与 Agent 接入](agent-first.md)、[配置与迁移](configuration.md)、[隐私说明](privacy.md)。

## 分布式文件系统上的启动速度

解释器和依赖安装在共享存储时，打开许多小文件可能拖慢终端和记录命令。在与目标机器兼容的 Linux 构建环境中，安装 `.[tui]` 和 `PyInstaller>=6,<7`，运行 `python scripts/build_portable.py`。将整个 `dist/HandoffDesk` 目录一次复制到用户可执行的快速磁盘，保留其中的 `_internal`。启动包内的 `handoffdesk --app-dir <原状态目录> tui` 即可读取已有项目。

切换前，用同一数据集对比版本查询、出现可操作记录列表和保存 checkpoint 到获得回执的时间。使用独立测试状态验证写入和重试。通过后指向新的可执行文件，并用同一状态目录更新 Agent 接入。原先采用工作规则模式时沿用 `connect codex --skill-only`。

在 Linux 运行 `python scripts/smoke_terminal.py dist/HandoffDesk/handoffdesk --repeat 3` 可测量版本查询、列表可见及回车读到正文的时间，同时验证重复提交和重启读取。可用 `--temp-parent` 指向目标磁盘上已有的私有目录；测试只在其中创建临时项目和状态。若工作库在高延迟共享盘，程序放到本机后，SQLite 访问仍会受存储延迟影响。持续使用同一台服务器时，可用 tmux 保持阅读器打开，分离后再次连接即可继续阅读。
