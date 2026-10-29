---
name: handoffdesk
description: Record local project milestones, decisions, blockers, and actionable plans in HandoffDesk during project work, then report each successful receipt to the user. Use when implementing, investigating, planning, or resuming a project with outcomes worth handing off.
---

# HandoffDesk

Keep the user's project progress readable in HandoffDesk. The installed command
below already selects this machine's state directory:

```sh
@HANDOFFDESK_COMMAND@
```

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
