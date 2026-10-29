# HandoffDesk

**AI agents record the progress. People resume the work with the right files.**

HandoffDesk is a local desktop app and command line tool for project handoffs. Keep what actually changed, the next action, blockers, verification notes, and the files for each milestone together. The desktop lets a person read that history and open a file or reveal its location in one click.

[中文说明](README.zh-CN.md) · [User guide](docs/usage.zh-CN.md) · [Agent integration](docs/agents.md) · [Ubuntu](docs/ubuntu.md)

## What it does

- Separate projects and worklines, with plans, handoff history, drafts, and source change review.
- Progress nodes with file names, purposes, full paths, and availability; search, associate files or folders, relocate a moved file, and remove an association without deleting the original.
- A versioned JSON interface for agents, with atomic record and file saves, safe retries, and revision conflict detection.
- Human readable attribution and verification notes. An agent's reported test result remains attributed to that agent.
- Read explicitly registered local sources and optional Git state; export a local Markdown summary.
- Windows desktop, Ubuntu desktop, and headless Ubuntu recording and queries. No cloud account is required.

The interface is currently primarily Chinese. Agent commands use structured JSON; an English interface is not included in this release.

## Quick start

Source development requires **Python 3.10+**. Windows uses a local WebView2 desktop; Linux uses a Textual terminal reader. Use `python3` in place of `python` where needed.

```sh
git clone https://github.com/EnumaElish123/HandoffDesk.git
cd HandoffDesk
python -m pip install '.[tui]'
handoffdesk tui
```

On Windows install `'.[desktop]'` and run `handoffdesk desktop`. Readers start with an empty project list on a fresh installation. `handoffdesk connect codex` installs the managed skill and optional hooks; Codex still requires its own hook trust. See the [agent-first workflow](docs/agent-first.md). The old Tk interface is available as `handoffdesk legacy-desktop`. To manually initialize a real project:

```sh
python -m handoffdesk init --project alpha --name "My project" --root /absolute/path/to/your-project
```

Replace the path with an existing local absolute directory. On Windows, use a path such as `C:\Projects\Alpha`. An existing configuration is preserved: use `project add beta --name "Another project" --root ...` to add another project.

For use from any directory, install from this checkout:

```sh
python -m pip install .
handoffdesk --version
handoffdesk list --json
```

This is a source installation. These instructions do not assume a package has been published to PyPI. On Ubuntu, use a virtual environment for installation; see [Ubuntu setup](docs/ubuntu.md).

## Agent to human workflow

1. The agent calls `agent begin --cwd … --task …` to bind the project and task.
2. At a meaningful milestone it calls `agent checkpoint` with actual progress, next action, verification scope and related files.
3. It checks the durable receipt and immediately reports the saved record to the user; retries reuse the original checkpoint token.
4. The person opens the Windows desktop or Linux TUI. New records appear automatically while the current detail stays in place; unread state is tracked per record.

Prefix the commands above with `handoffdesk` after installation, or `python -m handoffdesk` from the source checkout. The [agent guide](docs/agents.md) includes the payload, error handling, and a reusable instruction for project agents. HandoffDesk does not automatically capture other agents' conversations; those agents must call the interface.

## Where your information lives

The default state directory contains `config/projects.json` and `data/cards.sqlite3`:

| System | Default state directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\HandoffDesk` |
| Ubuntu | `$XDG_DATA_HOME/handoffdesk`, or `~/.local/share/handoffdesk` |

Set `HANDOFFDESK_HOME` to a local absolute directory to choose a different location, or pass the global `--app-dir` option. An explicit `--app-dir` takes priority. Put global options before the command:

```sh
python -m handoffdesk --app-dir /absolute/path/to/handoffdesk-state list --json
```

Keep state outside registered project roots. Desktop and agent processes must use the same state and data directory to share records. The public repository contains generic examples and source code; personal configuration, databases, exports, backups, credentials, and bundled Python runtimes are excluded. See [configuration](docs/configuration.md) and [privacy](docs/privacy.md).

## Boundaries

File associations store locations and purposes; they do not back up or transfer project files. Source snapshots may contain bounded excerpts of explicitly registered documents. Cross machine root mappings do not synchronize databases or rewrite historical file paths. A server's files need a desktop on that machine or separately mounted/transferred files to open locally.

HandoffDesk does not launch project scripts, training jobs, or development servers. Document opening uses the desktop's default application; scripts open as read-only text and models or unknown file types reveal their directory. The local frontend uses a loopback-only static server; optional browser preview authenticates requests with a per-process token. It is not a remote server or a cloud service.

## Development

```sh
python tests/run_tests.py --headless
# In a working desktop session with Tk:
python tests/run_tests.py
```

Tests use isolated temporary projects. See [CONTRIBUTING.md](CONTRIBUTING.md) for the release checks and supported environments. Test results for a particular commit should be checked in that commit's CI runs; this README does not claim that every desktop environment has been tested.

## License

[MIT](LICENSE). Runtime dependencies and asset notices are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Linux workflow improvements

For a single source installation and Codex connection, run `python scripts/install.py`. Reinstalls reuse registered state. The installer prints an immediately usable absolute launch command and manages its own user command entry without replacing unrelated commands. Use `--no-connect` for reader-only installation and `handoffdesk doctor --human --probe` for readable diagnostics.

The terminal reader supports task selection (`t`), search (`/`), unread filtering (`u`), full-screen attachment reading (`f`), exclusive Markdown export (`e`), connection status (`c`), and copying a task continuation prompt (`y`). Press `?` for help. Times include an explicit offset; `HANDOFFDESK_TIMEZONE=Asia/Shanghai` optionally selects a display zone.

Agents can discover prior tasks with `agent sessions --cwd ...`, continue them with `agent resume --session ...`, and retrieve full proposals with `agent status --session ... --checkpoint ...`. Default recovery output is compact. Paused recording requires explicit user resumption before using `--resume-recording`; save, report, and reader acknowledgement remain separate states.
