# Changelog

## 0.10.0 — Agent-first readers (development)

- Replace the default Windows reading surface with a local HTML/CSS/JavaScript desktop hosted by pywebview; retain Tk as `legacy-desktop`.
- Add a Linux terminal reader, shared paginated feed, per-record unread state, bounded text previews, and complete Markdown exports.
- Discover project roots and Git worktrees automatically; bind agent sessions to tasks and save semantic checkpoints with durable receipts and recovery.
- Add reversible Codex skill/hooks installation, permission diagnostics, and a finite missing-record/report remedy.
- Migrate SQLite v4 to v5 after a consistent backup without changing historical payloads or record identifiers.
- Add source and portable-package build tools. Windows native installer, WebView2 and real DPI acceptance remain platform release gates.

## 0.9.0 — Initial HandoffDesk open source release

- Unify the application, Python package, command, and documentation under the HandoffDesk name.
- Separate public source from personal configuration, records, machine paths, runtime bundles, and private verification material.
- Add explicit first-run initialization, a generic demo, configurable user state directories, and project registration commands.
- Document human milestone and file workflows, structured agent records, Ubuntu desktop and server use, local data boundaries, backup, and migration.
- Add an MIT license, contributor and security guidance, packaging metadata, and public release checks.
- Keep selected source and milestone file rows visible after small-window layout changes.

The release retains the local handoff, workline, history, source review, and progress-node file capabilities developed before the public release. The Agent record protocol remains version 1. Personal installations remain independent and are not automatically migrated.
