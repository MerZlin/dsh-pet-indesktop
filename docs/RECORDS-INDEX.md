# 记录文档分类索引

本文件是 `docs/` 中 **PR、Issue、Release 和工程记录** 的分类导航。它不替代记录原文，也不改变记录的历史性质；每份记录仍保持独立文件，便于追溯提交、问题和验收证据。

> 当前阶段只完成分类与索引，暂不移动 PR 报告、Issue 记录或 Release 记录。原因是 `tests/test_pr_report_discipline.py` 和 `docs/DEV-HANDOVER.md` 当前将 PR 报告路径约定为 `docs/PR-REPORT-*.md`。如需迁移到子目录，必须先同步修改机器校验、交接规范和所有引用，并单独验证。

## 如何使用

- **查某次 PR 的交付证据**：先看 [PR 报告](#pr-报告)，再根据报告中的修改文件和验证命令进入代码或测试。
- **查某个问题、事故或修复背景**：看 [Issue 与 Bug 记录](#issue-与-bug-记录)。Issue 草稿不等于已确认的产品规范。
- **查版本发布内容**：看 [Release 记录](#release-记录)。当前实现细节仍以现行架构和专题文档为准。
- **查工程经验**：看 [合并与工程过程记录](#合并与工程过程记录)。这类文档通常是方法论或复盘，不是运行时合同。

## PR 报告

PR 报告是交付证据，**不合并、不覆盖、不删除**。同一主题的多份报告如果存在口径差异，应在 `docs/INDEX.md` 标注现行口径，而不是把历史证据拼成一份。

模板：[`PR-REPORT-TEMPLATE.md`](PR-REPORT-TEMPLATE.md)

| 文档 | 用途 |
|---|---|
| [`PR-REPORT-ENGINEERING-NORMALIZATION-2026-09-24.md`](PR-REPORT-ENGINEERING-NORMALIZATION-2026-09-24.md) | 工程规范化审计与方案证据。 |
| [`PR-REPORT-FESTIVAL-REMINDER-2026-09-16.md`](PR-REPORT-FESTIVAL-REMINDER-2026-09-16.md) | 节日提醒功能交付证据。 |
| [`PR-REPORT-GATES-2026-09-10.md`](PR-REPORT-GATES-2026-09-10.md) | Report Gates 专项交付证据。 |
| [`PR-REPORT-ISLAND-HIDDEN-CHAT-DEADLOCK-2026-09-23.md`](PR-REPORT-ISLAND-HIDDEN-CHAT-DEADLOCK-2026-09-23.md) | 灵动岛隐藏与 Chat 死锁修复证据。 |
| [`PR-REPORT-ISLAND-RESHOW-NOCHAT-2026-09-23.md`](PR-REPORT-ISLAND-RESHOW-NOCHAT-2026-09-23.md) | 灵动岛重新显示和无 Chat 状态修复证据。 |
| [`PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md`](PR-REPORT-ISSUE-186-TRAY-MENU-2026-09-23.md) | Issue #186 托盘菜单修复证据。 |
| [`PR-REPORT-LOCAL-WIP-BATCH-2026-09-22.md`](PR-REPORT-LOCAL-WIP-BATCH-2026-09-22.md) | 本地 WIP 批次交付证据。 |
| [`PR-REPORT-music-lyric-2026-09-16.md`](PR-REPORT-music-lyric-2026-09-16.md) | 音乐歌词基础能力交付证据。 |
| [`PR-REPORT-music-lyric-align-2026-09-22.md`](PR-REPORT-music-lyric-align-2026-09-22.md) | 音乐歌词对齐能力交付证据。 |
| [`PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md`](PR-REPORT-MUSIC-LYRIC-SYSTEM-PROXY-2026-09-22.md) | 音乐歌词系统代理行为交付证据。 |
| [`PR-REPORT-MUSIC-PLAYER-PATHS-2026-09-22.md`](PR-REPORT-MUSIC-PLAYER-PATHS-2026-09-22.md) | 音乐播放器路径处理交付证据。 |
| [`PR-REPORT-ONLINE-UPDATE-2026-09-24.md`](PR-REPORT-ONLINE-UPDATE-2026-09-24.md) | Core 自动更新交付证据；不等同于 DLC 更新协议。 |
| [`PR-REPORT-PERF-ISLAND-CONSOLIDATED-2026-09-23.md`](PR-REPORT-PERF-ISLAND-CONSOLIDATED-2026-09-23.md) | 灵动岛性能合并交付证据。 |
| [`PR-REPORT-PLUGIN-DLC-PHASE1-2026-09-24.md`](PR-REPORT-PLUGIN-DLC-PHASE1-2026-09-24.md) | Phase 1 资源 DLC 交付证据。 |
| [`PR-REPORT-PLUGIN-PHASE2-2026-09-24.md`](PR-REPORT-PLUGIN-PHASE2-2026-09-24.md) | Phase 2 Core 插件运行时交付证据。 |
| [`PR-REPORT-PR76-2026-09-10.md`](PR-REPORT-PR76-2026-09-10.md) | PR76 批次交付证据；Report Gates 专项以 `PR-REPORT-GATES-2026-09-10.md` 为现行口径。 |
| [`PR-REPORT-SELF-TALK-IMAGE-CHANCE-2026-09-20.md`](PR-REPORT-SELF-TALK-IMAGE-CHANCE-2026-09-20.md) | 自言自语图片概率交付证据。 |
| [`PR-REPORT-SELF-TALK-PRECACHE-2026-09-20.md`](PR-REPORT-SELF-TALK-PRECACHE-2026-09-20.md) | 自言自语预缓存交付证据。 |
| [`PR-REPORT-SETTINGS-INTERACTION-TABS-2026-09-22.md`](PR-REPORT-SETTINGS-INTERACTION-TABS-2026-09-22.md) | 设置交互标签页交付证据。 |
| [`PR-REPORT-VOICE-CHIME-2026-09-15.md`](PR-REPORT-VOICE-CHIME-2026-09-15.md) | 语音报时交付证据。 |

## Issue 与 Bug 记录

Issue/bug 文档用于记录问题背景、复现条件、影响范围或修复过程。它们不自动代表当前行为；判断当前实现时应结合代码、测试和 `docs/INDEX.md` 中的现行文档。

| 文档 | 用途 |
|---|---|
| [`BUGFIX-AND-FEATURES-2026-08-24.md`](BUGFIX-AND-FEATURES-2026-08-24.md) | 一次 Bug 修复与功能批次记录。 |
| [`ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md`](ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md) | Windows session-end 与 FFmpeg 路径问题；修改相关代码前必读。 |
| [`ISSUE-EDGE-TTS-VOICE-DEPRECATION-2026-09-22.md`](ISSUE-EDGE-TTS-VOICE-DEPRECATION-2026-09-22.md) | Edge TTS voice 弃用和兼容性记录。 |
| [`issue-draft-主动识屏v420.md`](issue-draft-主动识屏v420.md) | 主动识屏 Issue 草稿；现行设计入口为 [`PROACTIVE-SCREEN-DESIGN.md`](PROACTIVE-SCREEN-DESIGN.md)。 |

## Release 记录

Release 文档是版本发布快照。它们不替代当前 README、SPEC 或阶段路线图，也不应与 PR 报告合并。

| 文档 | 用途 |
|---|---|
| [`RELEASE-v4.2.0.md`](RELEASE-v4.2.0.md) | v4.2.0 发布记录。 |
| [`RELEASE-v4.2.1.md`](RELEASE-v4.2.1.md) | v4.2.1 发布记录。 |

## 合并与工程过程记录

| 文档 | 用途 |
|---|---|
| [`PR-MERGE-LESSONS-2026-09-12.md`](PR-MERGE-LESSONS-2026-09-12.md) | PR 合并、堆叠分支和时序测试的经验；合并 PR 前必读。 |

## 后续迁移计划

当需要真正把记录移入子目录时，按以下独立步骤执行：

1. 先修改 `tests/test_pr_report_discipline.py`，使其递归扫描新的 PR 报告目录；
2. 同步更新 `AGENTS.md`、`docs/DEV-HANDOVER.md`、`docs/INDEX.md` 和所有 context pointer；
3. 先迁移一类记录并运行链接检查、纪律测试和全量测试；
4. 使用 Git 可识别的 rename，保留原文件名、日期和历史内容；
5. Issue、Release 与 PR 报告分开提交，不在一次提交中混合归档和业务代码。

在上述合同修改完成前，**不要直接将 `PR-REPORT-*.md` 移出 `docs/` 根目录**。

返回：[`docs/INDEX.md`](INDEX.md)
