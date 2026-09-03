# dsh-pet 性能+结构线交付手册（2026-09-03）

面向：项目维护者（接手 perf/stage-1 分支的人）。
阅读顺序建议：本手册 → `docs/WINDOW_PY_SPLIT_GUIDE.md` → 需要深入哪块再看
`_plan/` 档案（该目录不入库，属工作档案，仅本地保留）。

---

## 1. 这条分支里有什么

分支 `perf/stage-1`，基于上游 main v4.1.0（已合并同步，合并提交 6cc4aed），
相对上游的主要内容：

### 性能线

各项的证据强度不同，逐项标注（默认开/关均指配置默认值）：

- 帧预缩放缓存（默认开）：64MB 硬预算 LRU（`pet/frame_cache.py`），
  命中时跳过显示转换链（toImage→镜像→预乘→缩放→fromImage）。
  证据：单元/行为测试 + 预算收紧的实测记录（256MB→64MB 的取舍见
  frame_cache.py 头部注释）。
- 拖拽合帧 + moveEvent 同帧合并（默认开）：鼠标移动事件只记录最新目标，
  由 8ms 定时器消费（理论消费上限 125Hz）。证据：行为测试
  （`tests/test_drag_move_coalescing.py`）；无端到端延迟实测。
- 隐藏即停：窗口隐藏后停止动画播放/解码与相关定时器。
  注意：自动全屏隐藏路径会保留 fullscreen watcher（低频后台轮询），
  不是字面意义的进程零 CPU。
- 闲置解码节流 `idle_low_fps_enabled`（默认关）：闲置期隔帧呈现并背压
  解码。证据：台架实测解码 CPU -54.6%（5.2% vs 11.5% 单核，测试台架
  记录见 `_plan/current/PERF_BASELINE.md`）；实机 A/B 未闭环（见 §5）。
- 多开共享解码 broker `decode_broker_enabled`（默认关；运行时另有
  Windows + AMD64/x86_64 平台门禁）：双开同角色待机时 ffmpeg 解码进程
  2→1。证据：实机演示含父进程归属断言
  （`_plan/current/P3_DEMO_EVIDENCE.md`）。
- 内存瘦身：首帧缓存从无总量上限收紧为 32MB 预算 LRU（代码事实）+
  删除 `_first_pixmap` 死字段（写两次、零读取）。实测记录（单台 Windows
  机器、工作集口径、方法学局限见档案）：三开热机 361–402MB/只 →
  221–228MB/只，约 -40%（`_plan/current/MEM_MEASUREMENT_20260903.md`）。

### 结构线

- window.py 行数变迁（全部可由 git 历史复核）：结构线前 4307 →
  结构线收官（5eef896）3853 → broker 接线等（ad3daa1）4198 →
  合并上游 v4.1.0（6cc4aed）4229 → 当前 HEAD 4239。
  拆出的模块：collision_client / platform_win / platform_mac / broker 接线；
  agent_link 拆 Reducer/Presentation；chat 双 UI 共享 geometry/utils；
  设置框控件库 + QSS 剥离。
- 机器化防线：`.github/workflows/pr-test.yml`（PR 三平台 pytest + ruff 门禁）、
  `tests/test_architecture.py`（依赖方向 / 窗口私有面 / 行数预算 4300）、
  `tests/test_config_schema.py`（配置键白名单快照）、ruff F 级基线
  （pyproject.toml）。

### 功能修复（相对上游新增）

- Agent 联动 opencode 事件流：`step-finish` 按 reason 分流，
  `tool-calls`（等工具/等子代理）不再误报完成（本机 opencode.db 实测
  tool-calls 占绝大多数；回归测试见 `tests/test_agent_link.py`）。
- 气泡配图大小可调：`self_talk_image_scale`（设置 → 自言自语 →
  配图大小，50–300%）。
- 会话多前端原子追加（modern/legacy/QuickChat 经
  `SessionStore.append_messages` 在 io 锁内读-改-写，互不覆盖）。
- 其余审查修复（三方盲审 + 两轮修复复审）的批次与清单见
  `_plan/current/PR_READY_PERF.md` 总账（含 PR 拆分建议）。

## 2. 怎么验证（交付时的实测状态）

环境：Windows 11，Python 3.13.7（本机 venv；CI 用 3.11），PySide6 6.11.2，
pytest 9.1.1，分支 perf/stage-1 @ 交付提交。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
./.venv/Scripts/python.exe -m pytest -q   # 1102 passed / 6 skipped / 8 warnings
./.venv/Scripts/python.exe -m ruff check pet/ tests/   # All checks passed
```

留档：`_plan/current/_handover_fullrun_1.log`、`_handover_fullrun_2.log`
（两轮全量，结果一致）、`_handover_ruff.log`。
8 个 warning 为既有的 PySide6 弃用 API 提示（如 QImage.mirrored），
非本分支引入。

实机验证（Windows 三开，交付会话内）：broker grant/fallback、碰撞、聊天、
联动均正常；三份运行日志无业务错误（日志位于各实例配置目录
`pet-<pid>.log`）。打包绿色版 zip 随交付渠道提供（不入库）。

## 3. 刻意缓办/不做（每项有判断依据）

| 项 | 状态 | 理由 |
|---|---|---|
| window.py 继续拆分 | 交功能作者按 `docs/WINDOW_PY_SPLIT_GUIDE.md` 的功能驱动方式进行 | 无功能牵引的拆分缺乏真实验收标准；Qt 生命周期回归测试覆盖不到 |
| 闲置节流/broker 翻默认开 | 保持默认关（灰度） | broker 是新跨进程设施，建议一个发布周期收集 opt-in 反馈后再评估翻默认（改默认值 + 更新 schema 快照测试即可） |
| QQuickWindow 前端迁移 | 未启动；设计稿存档于 `_plan/archive/WIN_PERF_RESEARCH_SOL.md` §3.3 及交付方留存的专项设计文档 | 等「合成器级视觉」产品需求或 perfstats 实测证据；硬前置是动画链/屏幕可见性两域拆分 |
| 打字机渲染 | 已做安全刀（同状态短路）；18ms 全文重排/样式重算仍是已知未闭环的性能风险（`REVIEW_DS_POSTMERGE.md` M8） | 等 perfstats 实测数据支撑再定优先级 |
| 碰撞 orphan registry 锁协议 | 未证实存在死锁（审查原文如此），暂缓重设计 | 出现真实症状或改动该区域时再评估 |
| AppId 历史不一致 | 本轮已对齐：`.iss` 默认值、CI、README 统一为发布线值（3424d6cc…） | 更早发布的安装包身份无法追溯修复 |

## 4. 继续开发的入口

- **加功能前**：读 `docs/WINDOW_PY_SPLIT_GUIDE.md`（建议性质，非强制规范）
- **加配置键**：普通顶层键需三处登记——默认值 dict + reload 白名单 +
  schema 快照（漏登记测试会红，刻意设计）；特例键（version /
  proactive_screen / agent_link / chat）走专门合并/迁移路径，
  不塞进普通白名单
- **性能改动**：`PET_PERF_STATS=1` + `PET_PERF_STATS_FILE` 启用打点
  （perfstats.py，默认零开销），先测再改
- **构建**：`scripts/build_onedir.ps1 -Variant webm-chat`（Windows）；
  本地与 CI 共用同一入口

## 5. 已知观察项（未复现/未闭环，不阻塞）

- 一次实机定格事件（动画链静默停约 15 分钟，点击后复活，无日志）：
  档案 `_plan/current/INCIDENT_20260902_slot2_stall.md`。若再现，先
  `py-spy dump --pid <pid>` 再动它。
- 闲置降帧的实机收益有台架数据（-54.6%），实机 A/B 因 ffmpeg 进程轮换
  噪音未测出显著差异——需要更精细的实机测量方案。
