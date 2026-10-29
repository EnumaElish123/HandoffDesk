# Changelog

## Unreleased — Linux workflow improvements

- Preserve registered state directories on source reinstall, provide owned user command launchers, and report actionable connection diagnostics.
- Discover tasks by working directory; continue completed sessions in a new turn, explicitly resume paused recording, and keep recovery responses compact with full detail available on demand.
- Search visible project, task, milestone and attachment content; show tasks and explicit time zones in the terminal reader.
- Keep history selection stable during updates, show active filter results on narrow terminals, and provide first-use guidance and connection status.
- Give attachments a readable full-screen view on small terminals; export the current milestone without overwriting existing files.
- Align Linux documentation with the install → connect → agent record → user read workflow.

## 0.10.0 — Agent-first readers (development)

- Replace the default Windows reading surface with a local HTML/CSS/JavaScript desktop hosted by pywebview; retain Tk as `legacy-desktop`.
- Add a Linux terminal reader, shared paginated feed, per-record unread state, bounded text previews, and complete Markdown exports.
- Discover project roots and Git worktrees automatically; bind agent sessions to tasks and save semantic checkpoints with durable receipts and recovery.
- Add reversible Codex skill/hooks installation, permission diagnostics, and a finite missing-record/report remedy.
- Migrate SQLite v4 to v5 after a consistent backup without changing historical payloads or record identifiers.
- Fix Windows SQLite handle cleanup during migration and permission probes, and remove first-run registration races.
- Keep data boundaries consistent across ordinary and extended Windows paths; correct platform and UTF-8 regression fixtures.
- Separate file-operation errors from connection status; refresh attachment details without moving the reader, and support reversing read acknowledgements.
- Search visible record fields, receipts and decoded paths; show distinct task labels and use readable, Windows-safe export names.
- Add session discovery and compact recovery status, precise attachment diagnostics, save-time file fingerprints and optional bounded evidence copies.
- Handle cancelled Windows HTTP responses without an unhandled connection-aborted traceback.
- Document the local Windows fixes and reader acceptance results in the [validation report](docs/windows-validation-2026-10-29.zh-CN.md).
- Add source and portable-package build tools. Windows native installer, WebView2 and real DPI acceptance remain platform release gates.

## 0.9.0 — Initial HandoffDesk open source release

- Unify the application, Python package, command, and documentation under the HandoffDesk name.
- Separate public source from personal configuration, records, machine paths, runtime bundles, and private verification material.
- Add explicit first-run initialization, a generic demo, configurable user state directories, and project registration commands.
- Document human milestone and file workflows, structured agent records, Ubuntu desktop and server use, local data boundaries, backup, and migration.
- Add an MIT license, contributor and security guidance, packaging metadata, and public release checks.
- Keep selected source and milestone file rows visible after small-window layout changes.

The release retains the local handoff, workline, history, source review, and progress-node file capabilities developed before the public release. The Agent record protocol remains version 1. Personal installations remain independent and are not automatically migrated.
