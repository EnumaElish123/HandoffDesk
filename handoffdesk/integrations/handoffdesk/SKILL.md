---
name: handoffdesk
description: Manage HandoffDesk projects from chat. Use to register a project, set or relocate its local path, read progress, or proactively record milestones, decisions and blockers during work on a connected project. 支持对话建项、路径和工作线管理、主动记录、来源复核、历史恢复、材料查询、导出与接入诊断。
---

# HandoffDesk

Keep the user's project progress readable in HandoffDesk. The installed command
below already selects this machine's state directory:

```sh
@HANDOFFDESK_COMMAND@
```

Append the subcommands below to that command. Supply paths and names as individual
arguments, including spaces and non-ASCII text. A user can give the project
directory in chat; use that directory even when the chat opened elsewhere.
Use the current directory when the user identifies it as the project. Ask for
the missing directory or project identity only if the target is unclear.

## Register a project or set its path

- For “把 /path/to/project 接入 HandoffDesk，以后主动记录”, resolve the
  existing local directory and run `agent inspect --cwd <directory>`.
  For ordinary work, use the returned project, including a known Git worktree.
  The deepest registered root owns the current directory. When the user
  explicitly requests a separate project at a directory inside a registered
  root, register that exact directory in the same state. Reuse an existing
  registration for the same resolved directory. A parent can also be
  registered after its child.
- To register the exact directory, use the requested display name or its
  directory name. Choose an unused ID made of ASCII letters, digits, hyphens
  or underscores unless the user supplied an ID. `list` shows registered IDs
  and names. With an existing configuration run
  `project add <id> --name <name> --root <directory>`. For a confirmed fresh
  state use `init --project <id> --name <name> --root <directory>`. If a
  registration races with another, inspect again and check that the
  resolved root matches the requested directory before reusing the binding. Report other failures. A missing directory requires the correct
  path or a request to create it.
- For “项目搬到了 /new/path”, identify the existing project by ID (resolve a
  name with `list`) and run `agent relocate --project <id> --root <directory>`.
  This changes the main root while retaining the project ID and history. It
  does not move files. For a named root other than main, read
  `configure-roots --show`, merge the requested registered project/root key
  into that mapping, and pass the resulting UTF-8 JSON in a temporary file
  outside the project to `configure-roots --file <file>`. Preserve all other
  mappings. Example shape: `{"schema_version":1,"projects":{"alpha":{"docs":"/new/docs"}}}`.
- Verify registration or relocation with `agent inspect --cwd <directory>`.
  Confirm the expected ID and root, then report the display name, ID and
  effective path. A request to register or map a path alone needs no invented
  work session. Project registration and these rules live outside the project;
  users do not need to copy instructions or add an AGENTS.md to each checkout.

## Track work and read progress

During work on a connected project, use this workflow without waiting for
another reminder. Save when a stage produces a result, a decision is made,
a blocker changes the next step, or work is handed off. Before finishing,
save any outstanding outcome. Skip duplicate checkpoints and pure Q&A.
Keep the state directory separate from project files and select only files
relevant to the requested task.

For “这个项目做到哪里了”, use `feed --project <id>` and
`read <record_id> --markdown`. Use `read --ack` only when asked to mark the
record as read. For worklines, materials, source review, history, imports, exports, readers,
setup or diagnostics, follow [the operations reference](references/operations.md).
Use subcommand help for optional flags in the installed version.

When entering a project without the prior session reference, run
`agent sessions --cwd <absolute-project-directory>` to discover existing tasks.
Continue the task matching the user's request with `agent resume --session
<session_ref> --cwd <absolute-project-directory>`. Multiple candidates require
choosing the intended task; a shared title alone does not establish identity.
For distinct work, run `agent begin --cwd <absolute-project-directory> --task
<task-title> --actor Codex`; use `--new` when deliberately creating a separate
same-named task. A different checkout remains a different working directory.

Add `--turn <turn_ref>` when the host supplies a compatible open turn. Keep the
returned session, checkpoint token and current turn reference. A continued
completed task gets a new turn; interrupted work keeps its recovery chain.
Use `--resume-recording` only after the user has explicitly resumed recording.
When a supplied host turn belongs to a different host session, recover the old
session's pending proposals/reports first. Then use `begin --new --workline
<task_id> --cwd <same-directory> --task <task-title> --turn <current-host-turn>`
to continue that workline in a new session without reassigning old receipts.
The program chooses stable project/workline identities and revisions.

Default `status` and `resume` responses contain compact summaries. Retrieve a
failed proposal or full receipt with `agent status --session <session_ref>
--checkpoint <token>` before retrying or reporting. Use `--full` and `--cursor`
for older checkpoint pages; use `--pending-cursor` to follow remaining report
pages. Counts and next cursors indicate more work, not successful completion.

At a meaningful milestone, submit `agent checkpoint --session <session_ref>
--checkpoint <token> --stdin` with JSON:

```json
{
  "kind": "handoff",
  "title": "Readable milestone title",
  "actual_done": "What was actually completed and what remains uncertain",
  "next_action": "The next concrete step",
  "blockers": [],
  "verification": {"status": "not_run", "summary": "What was or was not checked"},
  "files": [{"path": "relative/deliverable.md", "purpose": "Why the user should read it"}]
}
```

Plans use `kind: "plan"` and an empty `actual_done`. Verification can be
`passed`, `partial`, `not_run`, or `failed`; report only checks actually run.
Attach deliberate deliverables with purposes. A saved path is a reference,
not a backup of the file.

After every confirmed save, immediately tell the user the record ID, what was
recorded, relevant files, and verification result in a short message. Use the
receipt's report text if suitable. Then use `agent ack-report` with evidence
type `agent_acknowledged` for the supplied turn and receipt. This acknowledges
your report, not proof that the user received or read it. Summarize receipt IDs
again in the final response so a supported host can observe them.

Reuse the same token and same content after a lost response. A replay confirms
the original record; describe it that way and preserve its original save time.
When recovering `pending_reports`, acknowledge each using **that receipt's
`turn_ref`**, which may differ from the newly begun turn:
`agent ack-report --turn <receipt.turn_ref> --receipt <record_id>
--evidence-type agent_acknowledged --evidence-ref <brief-report-reference>`.
For conflicts, inspect the current
state and preserve the unsaved content; never change a revision just to force
it through. A failed save is a failed save, not a recorded milestone.

Use `agent conclude` with the turn reference and recorded receipt IDs when the
turn ends. Pure explanations with no project outcome conclude as `no_record`
with a short reason, without beginning a project session. Respect a user's
request to pause recording using `paused`. Pending failures or receipts need
recovery rather than being hidden under `no_record`. The parent Agent normally
aggregates its subagents' work into the milestone to avoid duplicate reports.

Use `--help` for the exact recovery and acknowledgement options. If the command
or state directory cannot be accessed, report the failed integration and keep
the user's work moving; `doctor --probe` checks the actual calling process.
The reader and Agent must run on the same machine/state unless separately
configured; this installation does not synchronize machines.
