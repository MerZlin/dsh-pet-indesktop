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

## 6. 跨对话总结与启动阅读顺序（现行补充）

交接不只保存“下一条命令”，还要让一个新对话可以在不重读整段历史的情况下继续工作：

1. 先读 [`docs/PROJECT-ENTRY.md`](../PROJECT-ENTRY.md)，了解项目分层、文档导航和当前不确定性；
2. 读当前任务的 `STATUS.md`，确认阶段状态和验收门；
3. 读 `PLAN.md`，确认稳定编号、依赖和剩余步骤；
4. 读 `HANDOFF.md`，取得准确停点与下一条动作；
5. 读 `SUMMARY.md`，取得跨对话的最终有效事实；
6. 再读对应阶段的设计与验收文档、PR 报告和必要源码。

`SUMMARY.md` 不复制失败调试输出、重复尝试或无关讨论；它必须明确目标、已修改文件、关键决策、待办、用户要求、回答偏好、保护边界、提交/推送/验证状态和可体验效果。任务完成后保留最终交接和总结，不删除。相关模板和状态词汇见 [`WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md`](WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md)。
