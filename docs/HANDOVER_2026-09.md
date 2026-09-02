# dsh-pet 性能+结构线交付手册（2026-09-03）

面向：项目维护者（接手 perf/stage-1 分支的人）。
阅读顺序建议：本手册 → `docs/WINDOW_PY_SPLIT_GUIDE.md` → 需要深入哪块再看
`_plan/` 档案（该目录不入库，属工作档案，仅本地保留）。

---

## 1. 这条分支里有什么

分支 `perf/stage-1`，基于上游 main v4.1.0（已合并同步），相对上游的主要内容：

### 性能线（全部灰度/有实测）

- 帧预缩放缓存（64MB 硬预算 LRU，默认开）——动画循环播放的 CPU 链跳过
- 拖拽合帧 ~120Hz + moveEvent 同帧合并（默认开）——高回报率鼠标跟手
- 隐藏即零功耗：隐藏后解码终止、定时器全停
- 闲置解码节流 `idle_low_fps_enabled`（默认关）：闲置期解码 CPU
  台架实测 -54.6%（5.2% vs 11.5% 单核；实机 A/B 未闭环，见 §5）
- 多开共享解码 broker `decode_broker_enabled`（默认关，仅 Windows x64）：
  双开同角色待机 ffmpeg 进程 2→1（实证含父进程归属断言，见
  `_plan/current/P3_DEMO_EVIDENCE.md`）
- 内存瘦身：首帧缓存 32MB 预算 LRU + 删除 _first_pixmap 死字段——
  实测三开热机工作集 361–402MB/只 → 221–228MB/只（约 -40%）

### 结构线

- window.py 结构线期间 4307→3907 行（收敛出 collision_client /
  platform_win / platform_mac / broker 接线等模块）；合并上游 v4.1.0 后
  4239 行（上游新增的系统通知、供应商列表等功能所致）；agent_link 拆
  Reducer/Presentation；chat 双 UI 共享 geometry/utils；设置框控件库 +
  QSS 剥离
- 机器化防线：`tests/test_architecture.py`（依赖方向/私有面冻结/行数预算）+
  `tests/test_config_schema.py`（配置键白名单快照）+ ruff F 级基线 +
  `.github/workflows/pr-test.yml`（PR 三平台门禁）

### 功能修复（上游没有的新东西）

- Agent 联动 opencode 事件流：`step-finish` 按 reason 分流，
  `tool-calls`（等工具/等子代理）不再误报完成
- 气泡配图大小可调：`self_talk_image_scale`（设置 → 自言自语 → 配图大小）
- 会话多前端共享的原子追加（modern/legacy/QuickChat 互不覆盖）
- 其余 25 项审查修复（三方盲审 + 两轮修复复审的收敛结果）见
  `_plan/current/PR_READY_PERF.md` 总账，其中含 PR 拆分建议

## 2. 怎么验证（交付时的实测状态）

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q        # 1102 passed / 6 skipped（两轮）
ruff check pet/ tests/     # 全绿
```

实机验证（Windows 三开）：broker grant/fallback、碰撞、聊天、联动均正常，
日志零错误。随交付提供打包好的绿色版 zip 可直接体验。

## 3. 刻意不做/缓做的（每条有判断依据，非遗漏）

| 项 | 状态 | 理由 |
|---|---|---|
| window.py 继续拆分 | 交给功能作者按 `docs/WINDOW_PY_SPLIT_GUIDE.md` 的功能驱动方式做 | 无功能牵引的拆分无真实验收标准，Qt 生命周期回归测试覆盖不到 |
| 闲置节流/broker 默认开 | 保持灰度 | 新跨进程设施，建议一个发布周期 opt-in 反馈后再翻默认（改默认值 + 快照测试即完成） |
| QQuickWindow 前端迁移 | 有完整设计稿存档，未启动 | 等「合成器级视觉」产品需求或 perfstats 实测证据；硬前置是动画链/屏幕可见性两域拆分 |
| AppId 历史不一致 | 已对齐到发布线值 | 更早的安装包身份问题无法 retroactive 修复 |
| 打字机渲染深度优化 | 只做了安全刀（同状态短路） | 剩余优化等 perfstats 实测数据支撑 |
| 碰撞 orphan registry 锁协议重设计 | 未做 | 未证实存在死锁，不为想象重构 |

## 4. 继续开发的入口

- **加功能前**：读 `docs/WINDOW_PY_SPLIT_GUIDE.md`（建议性质，非强制规范）
- **加配置键**：默认值 dict + reload 白名单 + schema 快照三处登记，
  漏了测试会红（刻意设计）
- **性能改动**：`PET_PERF_STATS=1` + `PET_PERF_STATS_FILE` 有打点设施
  （perfstats.py），先测再改
- **构建**：`scripts/build_onedir.ps1 -Variant webm-chat`（Windows）；
  本地与 CI 共用同一入口

## 5. 已知观察项（未复现/未闭环，不阻塞）

- 一次实机定格事件（动画链静默停约 15 分钟，点击后复活，无日志）：
  档案 `_plan/current/INCIDENT_20260902_slot2_stall.md`。若再现，先
  `py-spy dump --pid <pid>` 再动它。
- 闲置降帧的实机收益有台架数据（-54.6%），实机 A/B 因 ffmpeg 进程轮换
  噪音未测出显著差异——需要更精细的实机测量方案。
