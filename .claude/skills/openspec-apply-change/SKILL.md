---
name: openspec-apply-change
description: Implement tasks from an OpenSpec change. Use when the user wants to start implementing, continue implementation, or work through tasks.
license: MIT
compatibility: Requires openspec CLI and bd (beads) CLI.
metadata:
  author: openspec
  version: "2.0"
  generatedBy: "1.3.1"
---

Implement tasks from an OpenSpec change. Tasks are tracked in **beads**, not markdown.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Conventions**

- Each change's tasks live as beads issues labeled `openspec:<change-name>`.
- Implementation status comes from beads (`open` / `in_progress` / `closed`), never from a `tasks.md` checkbox.
- The openspec `tasks` artifact (if present from older changes) is treated as read-only context, not as a source of truth for progress.

**Steps**

1. **Select the change**

   If a name is provided, use it. Otherwise:
   - Infer from conversation context if the user mentioned a change
   - Auto-select if only one active change exists
   - If ambiguous, run `openspec list --json` to get available changes and use the **AskUserQuestion tool** to let the user select

   Always announce: "Using change: <name>" and how to override (e.g., `/opsx:apply <other>`).

2. **Sync beads and check the change schema**

   ```bash
   bd dolt pull                                  # pull latest beads state
   openspec status --change "<name>" --json      # schema + artifact info
   ```

   Parse the openspec JSON to understand:
   - `schemaName`: The workflow being used (e.g., "spec-driven")
   - `contextFiles`: artifact ID -> file paths (proposal/specs/design/etc.)

3. **Get apply context (read-only)**

   ```bash
   openspec instructions apply --change "<name>" --json
   ```

   Use this **only** for `contextFiles` and any dynamic guidance text.
   Ignore the `tasks` / progress fields — beads is the authority.

   **Handle states:**
   - If `state: "blocked"` (missing prerequisite artifacts like proposal/design/specs):
     show message, suggest `/opsx:propose` or completing missing artifacts.
   - Otherwise: proceed.

4. **Read context files**

   Read every file listed under `contextFiles` (proposal, design, specs, etc.).
   Do **not** rely on `tasks.md` for progress — read it only if it exists and provides useful context.

5. **Load the task list from beads**

   ```bash
   bd list --label="openspec:<name>" --json
   ```

   Bucket issues:
   - `closed` → done
   - `in_progress` → resume these first
   - `open` (with no open blockers) → ready to work
   - `open` (blocked) → skip, surface in pause output

   If **no issues exist** with the label:
   - If a legacy `tasks.md` is present, offer (via AskUserQuestion) to bootstrap beads issues from its `- [ ]` lines, one issue per task, each labeled `openspec:<name>`. On confirm, create them with `bd create` (priority 2 by default) and proceed.
   - Otherwise, tell the user there are no tracked tasks and suggest running `/opsx:propose` or creating beads issues manually with the `openspec:<name>` label.

6. **Show current progress**

   Display:
   - Schema being used
   - Progress: "N/M tasks complete" (counts from beads)
   - Next ready task
   - Any blocked tasks

7. **Implement tasks (loop until done or blocked)**

   For each ready task (resuming `in_progress` first):

   ```bash
   bd update <id> --claim          # marks in_progress, assigns to you
   ```

   - Show which task is being worked on (`bd show <id>` for context)
   - Make the code changes required
   - Keep changes minimal and focused
   - On success:
     ```bash
     bd close <id>
     ```
   - Continue to next ready task

   **Pause if:**
   - Task is unclear → ask for clarification, leave as `in_progress`
   - Implementation reveals a design issue → suggest updating openspec artifacts; optionally `bd note <id> --message="..."` to record context
   - Error or blocker encountered → report and wait for guidance; if another beads issue blocks this one, add the dep with `bd dep add <this> <blocker>`
   - User interrupts

8. **On completion or pause, show status**

   ```bash
   bd list --label="openspec:<name>" --json
   ```

   Display:
   - Tasks completed this session (the IDs you closed)
   - Overall progress: "N/M tasks complete"
   - If all closed: suggest `/opsx:archive`
   - If paused: explain why and wait for guidance

**Output During Implementation**

```
## Implementing: <change-name> (schema: <schema-name>)

Working on ff-42 (3/7): <task title>
[...implementation happening...]
[DONE] Closed ff-42

Working on ff-43 (4/7): <task title>
[...implementation happening...]
[DONE] Closed ff-43
```

**Output On Completion**

```
## Implementation Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Progress:** 7/7 tasks complete

### Closed This Session
- ff-41 <title>
- ff-42 <title>
...

All tasks complete! Ready to archive — run `/opsx:archive`.
```

**Output On Pause (Issue Encountered)**

```
## Implementation Paused

**Change:** <change-name>
**Schema:** <schema-name>
**Progress:** 4/7 tasks complete
**Active beads issue:** ff-45 (in_progress)

### Issue Encountered
<description>

**Options:**
1. <option 1>
2. <option 2>
3. Other approach

What would you like to do?
```

**Guardrails**
- Beads is the source of truth for task state. Never edit a `tasks.md` checkbox to record progress.
- Always read context files (proposal/design/specs) before starting.
- Claim a task in beads (`bd update <id> --claim`) before touching code for it.
- Close a task in beads (`bd close <id>`) immediately after implementing it — don't batch.
- If a task is ambiguous, pause and ask before implementing; leave the issue `in_progress`.
- If implementation reveals an artifact-level issue, pause and suggest artifact updates rather than mutating beads silently.
- Keep code changes minimal and scoped to each task.
- Pause on errors, blockers, or unclear requirements — don't guess.
- Use `contextFiles` from the openspec CLI for file paths; don't assume names.

**Fluid Workflow Integration**

This skill supports the "actions on a change" model:

- **Can be invoked anytime**: Before all artifacts are done (if beads tasks exist), after partial implementation, interleaved with other actions.
- **Allows artifact updates**: If implementation reveals design issues, suggest updating openspec artifacts — not phase-locked, work fluidly.
- **Coexists with legacy `tasks.md`**: Older changes may still have a `tasks.md`. Treat it as historical context only; the bootstrap step in #5 migrates it into beads on first apply.
