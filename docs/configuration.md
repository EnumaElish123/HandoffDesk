# 配置、状态目录与迁移

HandoffDesk 把程序源码与个人状态分开。源码可以升级或重新克隆；本机配置、记录和导出保存在独立状态目录。下文中的路径是格式示例，必须替换为实际位置。

0.10 的自动项目接入通常不需要手工修改本文件所述配置。状态优先级为 `--app-dir`、`HANDOFFDESK_HOME`、用户安装登记、系统默认目录；损坏登记会报错，不静默切换到其他数据库。连接器拒绝单独的 `--data-dir`，避免 Agent 和阅读器读取不同数据库。

版本 4 首次由新版打开时，在数据目录 `migration-backups` 使用 SQLite backup API 保存一致数据库并复制配置，然后事务升级到版本 5。原 handoff payload、编号和文件清单不变。回退程序时应恢复对应版本的备份；不要降低数据库版本号或让旧程序继续写已升级的数据。

## 目录优先级

1. 显式全局参数 `--app-dir /absolute/state-directory`。
2. 环境变量 `HANDOFFDESK_HOME`，使用本机绝对路径。
3. Windows 的 `%LOCALAPPDATA%\HandoffDesk`；Ubuntu 的 `$XDG_DATA_HOME/handoffdesk`，未设置时为 `~/.local/share/handoffdesk`。

状态目录包含：

```text
state-directory/
  config/
    projects.json       # 本机项目、来源、入口和工作线登记
    roots.local.json    # 可选，本机根目录覆盖
  data/
    cards.sqlite3       # 进度、历史、节点、草稿、来源快照等
    ...                 # SQLite 辅助文件、导出等本地状态
```

无参数 `init` 还会生成工具自己的通用演示资料。真实项目的注册仅保存配置，不创建、移动或更改项目内的文件。

`--data-dir` 可单独覆盖 `data/`，配置仍来自状态目录。该参数须放在命令前，例如：

```sh
python -m handoffdesk --app-dir /absolute/path/to/state --data-dir /absolute/path/to/trial-data list --json
```

不允许把数据库放在已接入项目根目录内。应使用当前用户可写的独立位置；不要把工具状态放在代码仓库中再依赖忽略规则保存隐私。

## 初始化和增加项目

```sh
# 创建可直接试用的 demo；已有配置时拒绝覆盖
python -m handoffdesk init

# 尚未初始化时，以真实现有项目作为第一个项目
python -m handoffdesk init --project alpha --name "My project" --root /absolute/path/to/your-project

# 已初始化后，新增另一个项目
python -m handoffdesk project add beta --name "Another project" --root /absolute/path/to/another-project
```

项目 ID 使用字母、数字、下划线或连字符，应保持长期稳定。更改显示名称与更改 ID 的影响不同；历史以 ID 归属，不要通过随意改 ID 来“重命名”已有记录。

## 明确登记来源和入口

编辑状态目录的 `config/projects.json` 前关闭相关窗口并备份。模板见 [config/projects.example.json](../config/projects.example.json)。模板中的 `/absolute/path/to/your-project` 不适用于直接运行；首次使用优先通过 `init` 生成配置，再参照下面添加来源。

```json
{
  "schema_version": 1,
  "projects": [
    {
      "id": "alpha",
      "name": "My project",
      "aliases": ["sample"],
      "roots": {"main": "/absolute/path/to/your-project"},
      "adapters": ["registered_files", "json_fields", "git_readonly"],
      "sources": [
        {"id": "plan", "root": "main", "path": "docs/plan.md", "format": "markdown", "purpose": "current_plan"},
        {"id": "state", "root": "main", "path": "state.json", "format": "json", "fields": {"status": "/status"}}
      ],
      "entries": [
        {"id": "plan-file", "type": "open_source", "source_id": "plan", "label": "Project plan"},
        {"id": "documents", "type": "open_folder", "root": "main", "path": "docs", "label": "Documents"}
      ],
      "worklines": [
        {"id": "implementation", "name": "Implementation", "entry_ids": ["plan-file"]}
      ]
    }
  ]
}
```

根目录是本机绝对路径；来源和文件夹入口的 `path` 是根目录内相对路径，不能含越界跳转或数据流。JSON 字段使用 JSON Pointer，例如 `/status` 或 `/summary/result`。模板中的文件可以尚未存在，界面会显示缺失；只有实际存在的文件才可作为 Agent 新阶段附件保存。

`git_readonly` 读取本地 Git 状态，不执行构建或修改仓库。通用项目不需要专用媒体工作流适配器；已有的可选媒体工作流代码仅在对应配置启用时使用。

`default` 工作线由工具提供，不要在 `worklines` 中重复声明。也可通过桌面或 CLI `worklines alpha create "Review"` 创建工作线，随后使用返回的 ID。更改配置后重启相关进程，Agent 用上下文确认有效项目和根目录。

## 为另一台机器映射根目录

保留项目与来源定义，使用 `config/roots.local.json` 覆盖本机根目录。先查看：

```sh
python -m handoffdesk configure-roots --show
```

在状态目录之外准备一个映射文件，再校验保存：

```json
{
  "schema_version": 1,
  "projects": {
    "alpha": {"main": "/absolute/path/on/this-machine/alpha"}
  }
}
```

```sh
python -m handoffdesk configure-roots --file /absolute/path/to/root-mapping.json
```

项目 ID 和根目录名称必须已登记；新位置应真实存在且符合数据隔离边界。Linux 不能把 Windows 盘符当作本机绝对路径。一个项目有多个根目录时，应替换所有不适用于当前系统的目录。

映射只影响当前来源查找，不重写历史附件位置，也不复制文件或数据库。历史文件搬家后可在相应节点重新定位。要同时使用多台机器，需要自行安排数据传输与单写入者流程；不要让两个进程通过不可靠的网络文件系统同时写同一 SQLite。

## 备份

备份至少包含配置目录和一致的 SQLite 数据库。最简单的人工流程是关闭所有 HandoffDesk 桌面及写入命令，确认没有 Agent 正在保存，再把整个状态目录复制到独立位置。备份前后保留版本信息，避免混淆正式与试用数据。

自动化备份应使用 SQLite backup API 取得一致数据库副本，而不是在写入期间仅复制 `cards.sqlite3`。下面仅备份数据库；源路径与目标均由操作者填写，目标不得覆盖现有备份：

```python
from pathlib import Path
import sqlite3

source = Path("/absolute/path/to/state/data/cards.sqlite3")
target = Path("/absolute/path/to/backups/cards-backup.sqlite3")
if target.exists():
    raise SystemExit("Choose a new backup file")
with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
```

配置文件需另行复制。文件关联所指向的业务文件由项目自己的备份负责；数据库与摘要导出都不代替这些文件。

## 从旧版个人安装迁移

开源版使用独立名称与状态目录，原个人版本可继续保留。它不会自动导入旧版数据库、配置或运行时。

1. 保留原安装、原配置与数据，关闭旧版及相关 Agent 写入；先做完整备份和 SQLite 一致性检查。
2. 为 HandoffDesk 选择一个新的独立状态目录。安装程序源码，确认 Python 和 Tk 适用于本机；不要混用旧 Windows 运行时与 Linux 文件。
3. 将旧 `config/projects.json` 及必要的本机根映射复制到新状态的 `config/`，使用 SQLite backup API 把旧数据库复制到新 `data/cards.sqlite3`。目标必须是新的迁移副本，禁止覆盖仍在使用的数据库。
4. 保留项目和工作线 ID；在新机器上通过 `configure-roots` 映射根目录。来源定义可能包含私密文件路径，只在个人状态中保存。
5. 用明确的 `--app-dir` 查询项目、历史和节点，核对数量及代表性记录，再启动桌面检查文件位置。确认前继续保留旧版备份。

旧版 0.8 系列与本版本沿用记录协议 1，但程序可能升级数据库结构；不要让两个版本轮流写同一数据库，也不要把升级后的唯一副本直接交回旧版本。跨机器路径映射不会自动修复所有历史附件，应逐节点核对。

开源贡献或发布时，禁止把个人迁移配置、数据库或导出提交到 Git；见 [隐私说明](privacy.md)。
