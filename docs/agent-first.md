# 一次接入 Codex

Windows 使用本地 Web 桌面阅读，Linux 使用终端阅读。两端复用本机状态目录，默认不跨机器同步。Agent 在项目中产生成果时自动识别项目并记录；用户无需给每个项目复制配置。

已安装命令后运行一次：

```sh
handoffdesk connect codex
handoffdesk doctor --human --probe
```

安装器只管理自己的 Skill 文件和 `hooks.json` 中对应的事件组，保留已有设置和 `AGENTS.md`。初次接入及 hook 定义改变后，Codex 要求在宿主内信任定义；使用 `/hooks` 查看。安装程序不会绕过这一步。新对话加载用户级 Skill；Skill 自动匹配并非逐轮保证。

接入状态为 `awaiting_host_trust`，表示文件已准备，而非宿主已确认启用。`doctor` 区分可执行文件、配置完整性、最近 handler 调用和当前进程写入探针；手动执行 handler 也能留下调用记录，因此该记录不被当作信任证明。让实际 Codex 任务运行 `doctor --probe`，才能检查该调用上下文的文件和 SQLite 权限。探针使用独立临时数据库，不向历史时间线写试验记录。

宿主不支持或不允许 hooks 时可使用 `connect codex --skill-only`。记录功能仍可用，但缺少结束时的遗漏补救。`disconnect codex` 只移除未被用户改动的接入文件和精确匹配的 hook 组；保留记录、备份、安装登记和用户修改。

## 记录与汇报

`SessionStart` 提供入口提示，`UserPromptSubmit` 登记轻量轮次。Agent 根据实际成果调用 `agent begin/checkpoint`，程序负责路径、版本、幂等和回执。每次保存成功后 Agent 应立即简短汇报，并登记 `agent_acknowledged`。结束时看到明确记录编号可登记 `observed_in_final`；这两种证据均不等于用户已收到或已阅读。

`Stop` 检查本轮处置及待汇报内容，最多请求一次补救。补救延续原轮次的持久预算。纯问答使用内部 `no_record`，不创建空项目或空进度；用户暂停记录时使用 `paused`。中断只保留恢复状态，不重启任务。接入代码只使用宿主稳定事件字段，不读取全量聊天转录。

## 找回会话与简洁状态

没有稳定的宿主会话 ID 时，同名 `agent begin` 会要求明确继续旧任务或用 `--new` 创建独立任务，避免静默生成重复工作线。显式新建可返回同名恢复候选；已经中断的任务应选择原会话。丢失会话编号时，可以先查询：

```sh
handoffdesk --app-dir <原状态目录> agent sessions --cwd <项目绝对目录>
handoffdesk --app-dir <原状态目录> agent sessions --project <项目编号> --task "原任务名称" --limit 100
```

查询不会登记新项目，也不会在未初始化的目录中创建状态。返回最近会话的工作线、目录、记录数、最近活动和恢复命令；默认最多 20 个，最多 100 个，`has_more` 表示还有更早会话，可按任务名缩小范围。`resume_argv` 供程序直接调用；`resume_command` 可复制到返回的 `resume_shell`（Windows 为 PowerShell），其中保留原状态/数据目录和当前解释器。

```sh
handoffdesk --app-dir <原状态目录> agent status --session <会话编号> --summary
handoffdesk --app-dir <原状态目录> agent resume --session <会话编号> --summary
```

`--summary` 返回当前令牌、最新版本、checkpoint 状态、待汇报编号与下一动作，不重复正文和文件清单。待汇报编号最多列 5 个，同时返回总数和是否截断。公开 CLI 默认也返回精简操作摘要，`--summary` 保留另一种兼容摘要格式。需要原提案、附件错误详情或其余待汇报回执时，使用 `status --full` 并按游标翻页；可以加 `--checkpoint <令牌>` 定位一次提交。失败提交修复文件后原令牌原内容重试；冲突或需要修改原提案时，明确使用 `resume --replace-checkpoint <令牌> --reason "处理原因"`，不自动替换已有请求。

## 从源码安装

开发者可在 Python 3.10+ 环境运行 `python scripts/install.py`；这会建立独立用户环境、安装当前源码及平台阅读依赖，并连接 Codex。`--check` 仅显示目标，不写入或下载。`--prefix` 指定程序目录，`--state-dir` 指定已有状态目录，二者须分离。`--no-connect` 只登记安装。`--codex-home`、`--skills-home` 可用于隔离验证；普通用户无需填写。

Linux 安装器会创建本安装拥有的 `~/.local/bin/handoffdesk` 入口；已有外部同名文件或用户修改会保留。改变程序安装目录时，仅在旧安装登记、入口清单和内容摘要共同证明所有权后转交入口；旧安装的移除操作不会删除新入口。无论 PATH 是否配置，安装结尾都给出可直接复制的绝对启动命令。`--remove-launcher` 只移除该安装未被修改的命令入口，不删程序或记录。

更新时，记录目录解析与阅读器一致：显式 `--state-dir` > `HANDOFFDESK_HOME` > 已有安装登记 > 平台默认。普通重装沿用已有记录；登记损坏会停止，不会切换到新的空目录。`--registry-path` 可为隔离验证选择登记文件。

`doctor --human` 提供中文分层状态和针对当前阻塞的下一步；默认 JSON 接口保留，新增 `issues`、`next_action` 与 `layers` 便于 Agent 和阅读器消费。没有 CLI 入口只说明当前进程无法检测它，不代表桌面宿主不存在；Skill-only 的状态也会单独说明。

这个源码脚本需要已有 Python。面向无需配置 Python 的用户，应发布下面的便携包；不能把源码安装说成免运行环境安装。

## 便携包构建与验收

在目标操作系统创建干净的构建环境，安装项目的 `desktop`（Windows）或 `tui`（Linux）依赖以及 PyInstaller，然后运行 `python scripts/build_portable.py`。构建产物位于 `dist/HandoffDesk`，不包括本机数据库或项目配置。Windows 同目录包含 `HandoffDesk.exe` 和 `HandoffDeskCLI.exe`；连接和 hooks 使用后者以保留标准输入输出。Linux 使用 `handoffdesk`，不包含 Tk 或 WebView。

Windows 的 WebView2 Runtime 与 Python 包是独立依赖。`scripts/windows-installer.iss` 是按用户安装的 Inno Setup 脚本：检查 Runtime 注册表，缺失时从 Microsoft 官方地址取得 Evergreen 安装器，验证 Microsoft Authenticode 签名后安装，再次检查成功；不会以 Edge 浏览器代替 Runtime 检查。离线构建可将 `HANDOFFDESK_WEBVIEW2_OFFLINE` 设置为官方 x64 完整 Runtime 安装器的绝对路径，该文件会嵌入安装包并在安装时验证签名。

先构建 Windows 便携包，再用 Inno Setup 6 的 `ISCC.exe /DAppVersion=<版本> scripts/windows-installer.iss` 编译。安装器提供开始菜单、可选桌面入口和默认勾选的 Codex 连接，明确提示首次宿主信任。卸载仅断开属于该程序目录的连接并移除程序；状态、安装登记和备份保留。`.github/workflows/portable.yml` 提供手动触发的 Windows/Linux 未签名构建产物，不创建 Release 或自动发布。

安装器代码和构建流程已准备；Linux 环境没有 Inno Setup 编译器或 Wine，无法完成 Windows 编译、Runtime 安装和图形验收。已检查安装脚本的原生函数与路径/引号规则，并验证构建脚本与工作流语法；这些检查不能替代 Windows 编译。相应产物须在 Windows CI/真机跑通下列检查后才能作为完成验证的发行包。

构建的 Linux 包与构建机 glibc 有兼容性关系，应使用发行目标中最旧的受支持 Linux 环境构建，再向更新系统验证；本地成功不代表所有发行版通过。Windows 产物当前未签名，发行签名与真正发布是独立步骤。构建目录与测试状态均被源码仓库忽略，不应提交二进制或个人状态。

发行前必须在干净目标系统验证：

1. 没有系统 Python，移动整个程序目录后可启动与连接；中文及空格路径有效。
2. Windows WebView2 存在/缺失两种路径；100%、125%、150%、200% 缩放下长文本和附件完整可读。
3. Linux 无图形桌面、普通用户、SSH 的 TUI 与命令可用。
4. 已有记录的升级备份、恢复、卸载保留，以及接入文件被用户修改后的行为。
5. 真实 Codex 首次信任前后、写权限不足、暂停、中断和一次补救链；避免把单元测试当作宿主通过。
6. 记录各依赖与运行时许可证、构建版本及哈希；发布经过检查的包，不含个人状态。

参考：[Codex hooks](https://learn.chatgpt.com/docs/hooks)、[Skill 安装与发现](https://learn.chatgpt.com/docs/build-skills)、[WebView2 分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。
