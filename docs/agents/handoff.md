# Work handoff

Use a feature handoff only when work remains across tasks or context windows.
Store it at `.scratch/<feature-slug>/HANDOFF.md`; completed work needs no
permanent handoff.

Keep these fields current:

- worktree path and branch;
- objective and decisions that must not be reopened without new evidence;
- completed tickets and observable verification;
- unfinished tickets, blockers, and risks;
- current TDD state, including the last red/green test;
- exact next command or edit location;
- modified/untracked files and whether commits exist.

Before resuming, read the handoff, verify `git status`, then run the stated
focused test. Replace stale state rather than appending conversation history.
