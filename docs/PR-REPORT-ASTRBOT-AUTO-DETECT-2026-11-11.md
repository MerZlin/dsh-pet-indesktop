# AstrBot 联动自动采用已检测目录

基线：`8e74696`（AstrBot 联动本体，与本修正同属一个 PR 的前序提交），分支 `feat/astrbot-agent-link`。2026-11-11 本机源码验收（当时所在的本地集成分支为 `local-test` / `6e3ccfc`）。本报告补充 [AstrBot 联动报告](PR-REPORT-ASTRBOT-AGENT-LINK-2026-11-06.md)。

## 核心特性

用户在 macOS 实测时，开启 AstrBot 联动仍被要求选择目录。根因是已有 `detect_data_dir()` 能找到 `~/.astrbot/data`，调用者却只把结果作为文件选择器的起始目录。现在按“已保存且有效的目录 → 自动检测 → 未找到才手选”解析。

继续使用通用 AstrBot 通道名称：Desktop、源码和 Docker 挂载仍共用现有校验及插件安装流程。自动检测不等于安装授权；安装确认仍显示插件和事件文件的具体位置，拒绝时不写配置、不安装、不启动监视器。

## 修改文件说明

| 文件 | 增删 | 改动与原因 |
|---|---|---|
| `pet/agent_link/__init__.py` | +5 / −3 | `_resolve_astrbot_data_dir` 直接返回检测成功的目录，避免无意义的目录选择。 |
| `tests/test_agent_link_astrbot.py` | +30 / −0 | 从 `set_enabled` 公开入口验证检测成功跳过选择器、同意后安装并保存和监听、拒绝后配置字节不变且无安装或监听。 |
| `docs/INDEX.md` | +2 / −1 | 登记本报告并为原 AstrBot 报告增加目录解析问题的关联入口。 |
| 本报告 | 新增 | 保存根因、红绿测试、本机解析与性能证据。 |

无新增配置键、依赖、目录探测规则或文件删除。

## 性能分析

环境：macOS 27.2 / arm64 / Python 3.13.18，使用仓库 `.venv`；原生 QApplication，无平台模拟。以下探针只解析真实目录，Config 使用 scratch 中的临时目录，不写 AstrBot。

复现命令：在仓库运行 `.venv/bin/python`，执行：

```python
import statistics, tempfile, time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from pet.config import Config
from pet.agent_link import AgentLinkManager
app = QApplication([])
with tempfile.TemporaryDirectory(dir="/Users/imali/.hermes/profiles/nothing/cache/scratch") as tmp:
    mgr = AgentLinkManager(None, Config(base=Path(tmp)))
    t = time.perf_counter()
    result = mgr._resolve_astrbot_data_dir()
    print("resolved", result, "cold_ms", (time.perf_counter() - t) * 1000)
    samples = []
    for _ in range(1000):
        t = time.perf_counter_ns()
        assert mgr._resolve_astrbot_data_dir() == result
        samples.append((time.perf_counter_ns() - t) / 1000)
    print("median_us", statistics.median(samples), "p95_us", sorted(samples)[949])
    mgr.shutdown()
```

实测：解析到 `/Users/imali/.astrbot/data`；首次调用 0.028 ms；1000 次样本中位数 11.708 μs，P95 12.375 μs。此数字是本机目录解析耗时，非完整联动延迟或前后速度对比。

稳态监视路径未改，新增定时任务 0；此入口仅在用户开启未配置目录的联动时调用。原本已执行同一检测，未增加文件系统查询规则、网络、磁盘写入或线程；检测命中后省去文件选择窗口。无新增持久对象或缓存；未采样 RSS，不宣称内存实测下降。

## 实机运行记录

- 现场前提：`/Applications/AstrBot.app/Contents/Info.plist` 存在，bundle ID 为 `com.astrbot.desktop.tauri`；`~/.astrbot/data/cmd_config.json` 与 `plugins/` 均存在。
- 原生 Qt 进程调用修复后的生产目录解析方法，直接得到 `/Users/imali/.astrbot/data`，无目录选择弹窗。探针结果 `bridge_exists False`，未向实际 AstrBot 安装插件。
- 源码桌宠以 `.venv/bin/python -m pet` 重启，PID `46673`；启动确认见本次会话与 `~/Library/Application Support/dsh-pet-standalone/pet-46673.log`。
- 拒绝授权和检测失败的手选回退在回归测试中验证；未在真实 AstrBot 上安装或重载插件，完整消息联动仍待用户继续验收。

## 测试与验证

- 产品修改前新增两个参数用例均失败，精确报错：`Failed: 自动检测成功后不应弹目录选择`。
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_agent_link_astrbot.py tests/test_astrbot_pet_bridge.py tests/test_agent_link.py tests/test_uninstall_cleanup.py`：**320 passed, 2 skipped**（34.89 s）。原有未配置检测失败、手选取消、无效目录、已保存目录、卸载等用例继续通过。
- `.venv/bin/python -m ruff check pet/agent_link/__init__.py tests/test_agent_link_astrbot.py` 与 `git diff --check`：通过。
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_pr_report_discipline.py tests/test_architecture.py`：**251 passed**（0.75 s）。
- 未跑全量或满载时序复跑：本次仅改变开启联动时的同步目录解析，未改模型、配置格式、线程、生命周期、平台分支或稳态监听；已跑相关管理器、桥接及卸载测试。

## 风险与回滚

自动检测继续遵循既有候选顺序，包括 `ASTRBOT_ROOT`；已保存的有效目录优先。多部署用户可沿用该显式配置。未检测到有效目录仍手选，未移除目录校验或安装确认。回滚本次解析分支后恢复原来的预填目录选择行为，配置格式无变化。
