# petcore 开发者 README 重写

基线：`f5fbc0df2333fa2c8f9709075dedd51af7def5d7`；分支：`feat/petcore-vpet-architecture`。
日期：2026-11-09。用户明确要求根 README 面向开发者，直接重写并说明近期改动与 Linux/macOS 适配。
入口见 [README](../README.md)，平台证据见 [Linux 报告](PR-REPORT-PETCORE-POSIX-2026-11-07.md)
与 [Mac 报告](PR-REPORT-PETCORE-MACOS-2026-11-08.md)。

## 一、修改文件说明

<!-- README-FILE-STATS-BEGIN -->

| 文件 | 增删 | 修改与原因 |
| --- | --- | --- |
| `README.md` | +210/-750 | 完整重写为 petcore 分支开发者入口；移除旧用户教程，记录近期改动、架构、平台范围、命令及构建证据。 |
| `petcore/README.md` | +32/-33 | 更新过时开发阶段、平台验收、实例/扩展数量与分层说明，链接当前分支入口。 |
| `tests/test_requested_regressions.py` | +0/-91 | 移除与根 README 新受众不符的四个旧教程文字断言和专用 helper；保留其余产品回归。 |
| `docs/INDEX.md` | +4/-2 | 修正文档受众说明，登记本次纯文档报告并互链。 |
| `docs/PETCORE-LOG.md` | +14/-0 | 追加 L195，记录用户明确范围、文档守卫清理理由和验证豁免。 |
| `docs/PETCORE-LOG-INDEX.md` | +1/-0 | 登记 L195，使 README 重写可按批次追踪。 |
| `docs/PR-REPORT-PETCORE-README-2026-11-09.md` | 新增 +66/-0 | 新增逐文件说明、文档豁免与检查记录，避免把文档编辑当作新平台验收。 |

<!-- README-FILE-STATS-END -->

根 README 删除旧版下载/安装/日常操作教程，保留开发所需的架构、依赖、CLI、扩展、
测试与候选构建入口。明确旧 `pet/` 与当前 `petcore/` 的拓扑、配置和产物区别。
近期变更按批次归纳，并链接原始报告，不把历史测试数或候选版本写成当前全绿承诺。

Linux 说明 XWayland/xcb、XFixes、逻辑位置/视口、用户自启和 ABI 隔离；Mac 说明
普通 NSWindow、焦点、菜单、权限、原生输入与资源边界。已验 Intel 源码与未交付
arm64 `.app` 分开，KDE/Hyprland/混合屏/长期内存等未验项保留。

移除的四项测试只断言旧版根 README 必须含普通用户教程段落（DSH 自启说明、
全局快捷键教学、AI 配置路径、设置重启例外），与新受众及内容范围不再一致。
其产品行为测试没有删除、跳过或放宽；剩余顶层定义与基线逐项比较。

## 二、性能分析

**纯文档及旧文档文字守卫清理豁免**：没有运行时代码、依赖、配置或生命周期改动，
不新增线程/系统调用/网络/磁盘路径，因此不新增性能采样，也不重复整仓与高负载测试。
README 引用既有实测时注明日期、范围和口径；Linux 进程组与 Mac 主进程 CPU 不混比。
提交说明同步记录本豁免，不把此前未通过的门禁改成通过。

## 三、实机运行记录

本批在 Windows 工作区对照源码、构建约束、workflow 和原生验收报告进行文档核对。
没有重启 VM、运行新安装验收或修改用户数据；Linux/Mac 实机结论沿用上面两份报告，
不把 README 编辑当成新的平台认证。用户架构图两个文件保持原始内容，不纳入提交。

## 四、验证

| 检查 | 命令 / 方法 | 结果 |
| --- | --- | --- |
| 文档与报告门 | `python -m pytest -q tests/test_petcore_docs.py tests/test_pr_report_discipline.py` | 373 passed，3.90s，退出 0 |
| 静态检查 | `python -m ruff check pet/ tests/ petcore/` | All checks passed |
| 链接与 Markdown 结构 | `.scratch/petcore-readme/verify_docs.py` 检查两份 README 和本报告的本地路径、标题锚点及代码围栏 | 63 处链接，无失效路径/锚点；围栏闭合 |
| 其余回归完整性 | 将修改后 AST 与基线扣除四项文档测试及专用 helper 后逐节点比较 | 54 个测试函数及其余 AST 保持一致 |
| 测试收集 | `python -m pytest --collect-only -q tests/test_requested_regressions.py` | 55 项收集成功（含参数化），0.22s；未冒充执行通过 |
| 差异与范围 | `git diff --check`；用户架构图 SHA256 与改动前记录比较 | 通过；两个架构图内容不变 |

原始结果保留在 `.scratch/petcore-readme/`。本批不新增用于匹配文案的测试；
只移除已经不属于根 README 契约的旧教程断言，没有删除产品能力或放宽产品回归。

## 五、限制

当前 Windows 整仓七项既有失败、压力下 Qt 原生退出超时仍未解决；Mac arm64 包、
独立桌面 Space 切换和物理多屏等仍待后续验证。本批仅改善开发者信息入口。
