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

Requires **Python 3.10+**. The desktop also requires Tk. Use `python3` in place of `python` if that is the name of your Python interpreter.

```sh
git clone https://github.com/EnumaElish123/HandoffDesk.git
cd HandoffDesk
python -m handoffdesk init
python -m handoffdesk desktop
```

`init` creates a small demo and local configuration in your user data directory. It does not import existing projects or records. To initialize a real project instead of the demo, start with:

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

1. The agent reads `record context alpha --workline default` and uses the returned revision.
2. At a meaningful milestone, the agent prepares a record containing actual progress, the next action, verification scope, and related local files.
3. It runs `record validate --file submission.json`, then `record save --file submission.json`, and checks the JSON receipt.
4. The person opens the desktop, accepts the new progress notice, and uses **本次文件** to see exactly that stage's files.

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

HandoffDesk does not launch project scripts, training jobs, or development servers. Document opening uses the desktop's default application; scripts open as text and models or unknown file types reveal their directory. The tool has no web server or remote API.

## Development

```sh
python tests/run_tests.py --headless
# In a working desktop session with Tk:
python tests/run_tests.py
```

Tests use isolated temporary projects. See [CONTRIBUTING.md](CONTRIBUTING.md) for the release checks and supported environments. Test results for a particular commit should be checked in that commit's CI runs; this README does not claim that every desktop environment has been tested.

## License

[MIT](LICENSE). Runtime dependencies and asset notices are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
