# Work handoff

Every formal plan keeps a durable handoff, not only unfinished multi-ticket work.
Use [planning and reporting](planning-and-reporting.md) as the authoritative
four-record template; see [the index](../INDEX.md) for the phase design.

Store it at `.scratch/<feature-slug>/HANDOFF.md`, alongside tracked `PLAN.md`
and `STATUS.md`. Persist the records before implementation. Reuse them across
turns; keep a final handoff when complete, labelled “已完成、无剩余执行步骤”.
Do not delete the final handoff or duplicate the design in all three files.

Keep these fields current:

- worktree path, branch and the design/PLAN/STATUS links;
- objective and decisions that must not be reopened without new evidence;
- completed tickets and observable verification, with command, version and date;
- unfinished tickets, blockers, risks and existing/new/environment failure classification;
- current TDD state, including the last red/green test (or why it is not applicable);
- exact next command or edit location, or no remaining execution steps;
- modified/untracked files, actual commit SHAs and independently verified remote state;
- unverified manual gates and the limits of the current user's authorization.

Before resuming, read the handoff and status, verify `git status`, then run the
stated focused test. Replace stale current state rather than appending conversation
history. Preserve historical evidence in its report/log and link it with its version.

Only explicitly stage the task Markdown records from ignored `.scratch/` paths;
raw logs, build output, test private keys and personal data remain excluded.
A completed local commit is not a successful push. Record remote success only
after comparing the remote SHA, and do not rewrite history to insert a commit's
own SHA into itself.
