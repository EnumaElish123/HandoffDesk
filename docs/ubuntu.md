# Ubuntu 桌面与服务器

HandoffDesk 0.9.0 使用 Python 3.10+。服务器的 Agent 记录与查询不需要 Tk 或图形显示；桌面查看和打开文件需要同一解释器的 Tk 及真实桌面会话。源码仓库不附带 Python、Tcl/Tk 或系统软件包。

## 选择运行方式

| 环境 | 可以做什么 | 需要准备 |
| --- | --- | --- |
| 无界面服务器 / SSH | 初始化、记录、查询、节点和文件清单、导出 | Python 3.10+；可选 Git |
| 本地 Ubuntu 桌面 | 完整窗口、查看进度、打开文件、文件管理器定位 | 匹配的 Tk、字体、默认应用和桌面接口 |
| Ubuntu 远程桌面 | 与本机桌面相同，操作远端机器的本机文件 | 已连接且可用的远程图形会话 |

工具没有 Web 界面、HTTP API 或常驻服务，通常无需后台运行。CLI 保存后直接退出，数据保存在本机 SQLite 中。

## 安装系统依赖

先检查当前 Python 版本：

```sh
python3 --version
```

在提供 Python 3.10+ 的 Ubuntu 环境，可由操作者按需安装：

```sh
# CLI 与源码安装
sudo apt install python3 python3-venv git

# 需要桌面时
sudo apt install python3-tk xdg-utils libglib2.0-bin fonts-noto-cjk
```

应用不自动运行这些系统安装命令。Ubuntu 20.04 默认 Python 3.8，需单独准备 Python 3.10+；不要为本工具替换系统 Python。使用另一套解释器时，系统的 `python3-tk` 不一定能为它提供 Tk，应检查同一个解释器：

```sh
/absolute/path/to/python3 -c 'import sys, tkinter; print(sys.version); print(tkinter.TkVersion)'
```

Tk 可导入仅证明模块存在；实际窗口仍依赖可连接的图形会话和字体。

## 从源码使用

```sh
git clone https://github.com/EnumaElish123/HandoffDesk.git
cd HandoffDesk
python3 -m handoffdesk init
python3 -m handoffdesk list --json
```

默认状态位于 `$XDG_DATA_HOME/handoffdesk`，未设置时为 `~/.local/share/handoffdesk`。如需指定自己的位置：

```sh
export HANDOFFDESK_HOME=/absolute/path/to/handoffdesk-state
python3 -m handoffdesk init --project alpha --name 'My project' --root /absolute/path/to/your-project
```

`init` 不覆盖已有配置；已有状态增加项目使用 `project add`。设置状态目录时，应选择不位于已接入项目中的独立目录。

安装到虚拟环境后，可在其他工作目录调用：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
handoffdesk list --json
```

重新打开终端时激活相同环境，或使用该虚拟环境的绝对 `bin/handoffdesk` 路径。应用不会修改系统 Python、SSH 配置或登录配置。

## 服务器 Agent 流程

```sh
handoffdesk record schema
handoffdesk record context alpha --workline default
handoffdesk record validate --file /absolute/path/to/submission.json
handoffdesk record save --file /absolute/path/to/submission.json
handoffdesk nodes alpha --workline default
handoffdesk files alpha --node record:default:1
```

保存 JSON 与并发规则见 [Agent 指南](agents.md)。`node_id` 以实际回执为准。普通 SSH 会话没有图形显示时，记录与查询仍正常；尝试打开窗口会提示需要桌面。

服务器附件路径指向服务器本机文件。另一台电脑无法仅凭复制路径打开它们；可以在服务器的远程桌面中使用 HandoffDesk，或由自己的文件传输/挂载流程准备文件后重新定位。工具不自动同步数据库、传输文件或打开 SSH 隧道。

## 桌面使用与文件打开

在已登录的本地或远程桌面终端运行：

```sh
handoffdesk desktop
```

Tk 需要有效的 `DISPLAY`；Wayland 桌面通常由 XWayland 提供。不要把手工设置一个 `DISPLAY` 字符串当成图形连接已经建立，应在实际图形会话中启动。还需有当前用户可用的桌面总线和默认文件应用。

普通文档使用默认程序；文件管理器支持 `org.freedesktop.FileManager1.ShowItems` 时选中目标，否则退回父目录。脚本使用内置只读文本查看器，模型与未知文件类型定位所在目录。中文、空格、逗号路径作为独立参数传递。

如果中文显示异常，检查字体和 Tk 构建的字体支持；独立 Python 自带的 Tk 与发行版 Tk 不一定具有相同功能。桌面和 Agent 要使用同一状态与数据目录，才能共享进度和新记录提醒。

## 在自己的环境验收

```sh
python3 tests/run_tests.py --headless
# 有显示服务、Tk 的桌面：
python3 tests/run_tests.py
```

无界面集合应能运行而不导入 Tk。桌面还应实际检查：

1. Agent 保存一条真实试用阶段，窗口收到更新，进入“本次文件”。
2. 关联含中文、空格或逗号的文件，确认完整路径、搜索、用途和复制路径。
3. 打开文档、定位文件管理器、查看脚本文本；确认脚本未执行。
4. 文件移动后重新定位，移除关联后原文件仍存在。
5. 关闭再打开窗口，阶段及文件清单仍可查询。

这些操作使用独立试用状态和临时文件，不写入正式个人数据。自动测试、真实 X11 应用交互和物理 GNOME/Wayland 验收是不同的证据；请根据本版本实际验收结果判断覆盖范围，不把模块可导入等同于完整桌面验收。

## 依赖接口参考

- [Ubuntu Python Tk package](https://packages.ubuntu.com/noble/python3-tk)
- [xdg-open](https://portland.freedesktop.org/doc/xdg-open.html)
- [File manager D-Bus interface](https://www.freedesktop.org/wiki/Specifications/file-manager-interface/)

配置、备份和旧版迁移见 [configuration.md](configuration.md)。
