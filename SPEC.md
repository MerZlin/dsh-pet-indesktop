# dsh-pet 项目规范（SPEC）

> 当前基线：v4.2.1（2026-09-23 发布）
> 维护分支：`codex/plugin-dlc-v5`
> 文档更新：2026-09-24

## 1. 项目目标

`dsh-pet-indesktop` 是一个基于 Python + PySide6 的跨平台独立桌面宠物。项目的长期方向是在保持当前桌宠基础体验稳定的前提下，将可选角色、资源和高风险外部能力逐步拆成可验证、可回滚的插件/DLC 边界。

当前必须保持的基础行为包括：

- 透明、置顶、拖动、缩放、位置恢复和系统托盘；
- 角色动画播放、移动、点击交互和基础气泡展示；
- 配置持久化、多实例隔离和旧配置兼容；
- Windows、macOS、Linux 的平台适配；
- Core 自动更新协议及其现有 `update.json` 兼容性。

## 2. 当前范围与非目标

### 当前范围

- v4.2.1 运行时行为维护；
- Phase 1 资源型 DLC 和 Phase 2 Core 插件运行时的兼容维护；
- 工程规范化：行为测试分类、可复现覆盖率、Ruff、mypy、pre-commit、CI 和文档检查；
- 在不改变公共 API/CLI 行为的前提下，逐步收紧模块边界。

### 非目标

当前规范化阶段不实现：

- Worker 插件、JSONL IPC、AI/Agent/视觉/歌词迁移；
- Steam Workshop、远程 DLC 下载或 Core 自动更新协议重构；
- 任意第三方 Python 代码执行或 Python 模块热卸载；
- Qt UI 重设计或 PyInstaller 全面重构；
- 未经确认的历史文档删除、移动或改写。

## 3. 插件化路线边界

插件路线的阶段入口位于 [`docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md`](docs/plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)。现行边界如下：

| 层次 | 责任 | 当前状态 |
|---|---|---|
| Core | 生命周期、窗口、动画原语、配置、诊断、更新和插件宿主 | 现行运行时；Phase 2 已建立基础宿主 |
| `content` DLC | 角色、动画、台词、音效和主题等纯资源 | Phase 1 已闭环 |
| `in_process` 插件 | 受限、低风险的进程内功能 | Phase 2 已迁移官方节日提醒 |
| `worker` 插件 | 网络、截图、Agent、外部程序和长期监视能力 | 留给插件路线 Phase 3 |

插件不得绕过公开 Context/Port 访问 `PetWindow`、`AppShell` 私有字段或全局 `Config.data`。

## 4. 兼容性约束

- 入口 `python -m pet` 保持可用；
- 现有设置键、CLI 参数、角色目录和 Core 更新清单保持兼容，除非另有迁移说明；
- `assets/characters` 旧路径与 `content/characters` Starter DLC 在过渡期并存；
- `pet/updater.py` 和 `pet/update_settings.py` 只能在自动更新专项中修改；
- 修改 Qt/IPC/媒体生命周期时必须使用真实事件循环或进程边界测试，不能只靠 mock；
- 所有新增失败必须归类为既有问题、新引入问题或环境问题。

## 5. 工程质量门

本项目的本地和 CI 目标是调用同一套入口：

```text
python scripts/check.py --fast
python scripts/check.py --quality
python scripts/check.py --ci
```

其中 `--fast` 用于本地快速反馈，`--quality` 对应 Python 3.11–3.13 的静态检查与 `unit` 测试矩阵，`--ci` 执行完整门禁。完整门禁会把已知 Qt/媒体时序敏感族拆到独立进程运行，并用 `coverage --append` 合并覆盖率后再判定阈值。

阶段性门禁包括：

1. `python -m pytest -q` 全量回归；
2. 行为分类测试（`unit` / `integration` / `e2e` / `slow` / `platform` / `external` / `network`）；
3. `python -m ruff check pet tests scripts`；
4. `python -m ruff format --check pet tests scripts`；
5. `python -m mypy pet/plugins pet/content pet/catalog.py pet/config.py`；
6. Markdown 相对链接检查；
7. import/build smoke 和覆盖率基线。

当前覆盖率基线为 83%（40117 statements，2026-09-24 完整门禁，包含隔离测试族合并结果），开发依赖和 CI 均通过 `requirements-dev.txt` 与统一检查入口复现。

## 6. 文档治理

- `README.md`：新人入口、安装、运行、测试和架构概览；
- `SPEC.md`：当前目标、边界、约束和验收标准；
- `LOG.md`：按日期记录实际完成、验证、风险和未完成项；
- `LOG-INDEX.md`：按主题索引 `LOG.md`；
- `docs/INDEX.md`：详细文档、阶段文档和 PR 报告的唯一入口；
- 历史文档先登记、标记和归档方案，删除或移动必须单独确认。

## 7. 变更与回滚

规范化改动按主题拆分为独立提交：Phase 2 checkpoint、测试/覆盖率、四大文档、文档链接检查、Ruff lint、Ruff format、mypy、pre-commit/一键验证、CI。每个主题完成后运行 `git diff --check`、受影响测试和 `git status --short`，需要撤销时使用对应提交的 `git revert`，不使用 reset 覆盖用户改动。
