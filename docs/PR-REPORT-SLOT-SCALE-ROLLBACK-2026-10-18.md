# 右键改桌宠大小不再被「自己写盘触发的那次重读」回滚

> 分支 `fix/slot-scale-rollback`，基线 `a19157f`。关掉 #304。

## 〇、现象与根因

开启「直播捕获兼容」（`stream_capture_mode: true` → legacy 单窗拓扑 `PetWindow`）后，
右键 →「大小」→ 任选一档：尺寸**闪一下**变新档，随后立即弹回；`config-slot-0.json`
里 `scale` 仍是旧值；只有「桌宠设置 → 桌宠 → 桌宠大小」能改。报告者已把链条写在
issue 里，本次逐行核实成立：

1. `pet/window.py:990 change_scale()` 改 `self.scale` + `setFixedSize()`（那一下"闪"），
   然后 `self.cfg.set("user_customized", True)`。该键在 `SLOT_LOCAL_KEYS` 里
   （`pet/settings_slot_config.py:109`），`set()` 对 slot 类键**只写 `_pending_slot`
   草稿区、不落盘**。
2. 接着 `_save_position()` → `cfg.save_geometry({... "scale": 新值}, token)`。这条是
   **直写档案的旁路**：不清草稿、不作废快照。
3. 写出文件触发 `QFileSystemWatcher` → `pet/app.py:2174 _apply_legacy_profile(0)` →
   `inst.config.reload()` → `_invalidate_slot_snapshot()`，而它的门是
   `if not self._pending_slot:` —— **草稿里压着 `user_customized`，于是拒作废快照**。
4. `win.refresh_pet_settings()`（`pet/window.py:3820`）→ `cfg.get('scale')` 命中被冻住
   的旧快照 → `change_scale(0.72)` 回滚，并把旧值写回盘。

设置窗口为什么正常：表单保存走 `save()` → `_flush_slot()`，批量写档案、
`_pending_slot.clear()`、再作废快照。

**这条缺陷与「直播捕获」无关**，它是「任何 slot 键的残留草稿 + 自写几何」的组合；
直播捕获只是把用户放进 legacy 窗口、从而走到这条路径。

## 一、核心特性

两处接缝，各管一侧，缺一条都不能闭合：

- **写侧**（`change_scale`）：`user_customized` 当场落盘，不再压在草稿区。
  与 `window.py` 里既有的八处 `cfg.set(...)` 紧跟 `cfg.save()` 完全同形，未引入新 API。
- **读侧**（`save_geometry`）：写成功后调 `refresh_profile_view()`——只作废**只读**
  快照，不碰草稿、不碰 `_pinned_edit_generation`。这条接缝 `sync_chat_character`
  本来就在用，不是第二种语义。

**为什么单独上写侧不够**：`_flush_slot()` 会因 `stale-generation` / 锁超时失败
（文档化的正常结局，失败保留草稿），此时草稿又留在原地、快照照样冻住。实测：

```
save() = False  pending = {'user_customized': True}     # 外部推进代次后旗标那半写失败
save_geometry = True
get(scale) after reload = 0.5                            # 只有写侧时，回滚原样复现
```

叠上读侧后同一场景：`get(scale) after reload = 0.85`，且 `pending` 仍保留
`{'user_customized': True}`（草稿留给下次保存重试）。

## 二、修改文件说明

| 文件 | 增/删 | 改了什么 + 为什么 |
|---|---|---|
| `pet/window.py` | +3 / −0 | `change_scale()` 里旗标 `set` 后立刻 `save()`：不落盘的旗标会冻住档案快照，紧接着自存触发的那次 `reload` 读回旧尺寸把窗口弹回去 |
| `pet/settings_slot_config.py` | +12 / −1 | `save_geometry()` 写成功后 `refresh_profile_view()`：这条旁路写完自己的文件，下一次 `reload()` 解不开旧快照，`get('scale')` 读回写入前的值 |
| `tests/test_settings_profile_integration.py` | +33 | 两条纯配置层用例（红→绿 + 一条边界守卫） |
| `tests/test_slot0_lifecycle.py` | +39 | 一条窗口级用例（真 `AppShell` + `PetWindow` + `_apply_legacy_profile`） |

`pet/app.py` **一行未动**。`window.py` 实测 4535 行，仍在
`tests/test_architecture.py` 的 4671 预算内。

## 三、性能分析

本机 Windows + tmpdir，n=300：

| 路径 | 中位数 | p95 |
|---|---|---|
| 新增：`write_profile_settings({}, user_customized=True)` | 4.38 ms | 5.79 ms |
| 新增：一次 `read_slot_snapshot`（含目录锁） | 0.89 ms | 1.21 ms |
| 对照：既有 `write_profile_geometry`（改大小本来就要付） | 12.31 ms | 14.91 ms |

- **触发频率**：写侧只在尺寸**真的变化**时执行（`change_scale` 顶部
  `abs(scale - self.scale) < 1e-6` 早退，`refresh_pet_settings` 的反复轮询不会重复写盘）；
  读侧跟着几何自存（松手收尾 / 回右下角 / 改大小），都是用户动作级。
- **新增系统调用**：每次改大小多 1 次目录锁 + 1 次原子替换；每次几何自存后多 1 次
  档案重读（0.89 ms）。**无新线程、无网络、无内存增长**。
- 稳态（不改大小、不移动）**零开销**。

## 四、实机运行记录

用例级红（改产品代码前）：

```
FAILED tests/test_settings_profile_integration.py::test_geometry_write_does_not_freeze_the_slot_snapshot
  assert 0.5 == 0.92 ± 9.2e-07
FAILED tests/test_slot0_lifecycle.py::test_legacy_window_scale_survives_the_reload_its_own_save_triggers
  AssertionError: 自存触发的那次 reload 不许把尺寸弹回改之前的值
  assert 0.5 == 0.85 ± 8.5e-07        # 与 issue 报告的「弹回原尺寸」逐位吻合
2 failed, 2 passed, 41 deselected
```

改后：`4 passed, 41 deselected`。

真实窗口级复现/验证（真 `QApplication(offscreen)` + 真 `AppShell` + 真 `PetWindow`）：

```
start scale = 0.72  pending = {}
change_scale(0.85) → win.scale=0.85  pending={'user_customized': True}  disk=0.85
_apply_legacy_profile(0) -> True
修复前 → win.scale=0.72  disk=0.72        # 闪一下 + 回写盘
修复后 → win.scale=0.85  disk=0.85        # 不回滚
```

门禁：

- 指定 9 个配置/生命周期族：**300 passed**（两次复跑同结果）。
- 相关族（`test_architecture` / `test_pet_profiles` / `test_overlay_spawn_state` /
  `test_window_position` / `test_window_pause` / `test_window_effects` /
  `test_overlay_geometry_parity` / `test_overlay_profile_reset` /
  `test_overlay_sprite_settings` / `test_overlay_recycle_settings` /
  `test_sprite_menu_facade` / `test_sprite_menu_target` / `test_settings_default_marks`）：
  **260 passed**。
- `ruff check pet/ tests/` → `All checks passed!`
- 用例数核对（`--collect-only`）：`test_settings_profile_integration` 20→22、
  `test_slot0_lifecycle` 22→23、`test_settings_slot_config_route` 24 未变。
  **没有删除或放宽任何既有用例**（`test_overlay_per_slot_settings.py:517` 的
  `slot.get("user_customized") is True` 仍成立）。

## 五、已知限制与风险（如实）

1. **写失败不重试**：`save()` 返回 False 时没有处理返回值——旗标留在草稿区等下一次
   保存，尺寸不会回滚（读侧兜住），但这一次档案里 `user_customized` 不会置位。
   已实测该路径读侧正确、草稿保留。
2. **写侧理论上会顺带 flush**：`save()` 同时 flush 运行期视图的 `_pending_main`。
   核查过：`window.py` 里所有全局键的 `cfg.set(...)` 都紧跟 `cfg.save()`（8 处），
   活路径上 `_pending_main` 恒空。风险等级：理论存在、当前不可达。
3. **读侧的语义边界**：几何写成功后只作废只读快照，因此**不在草稿里**的几何键
   （scale / rx / ry / facing / screen_name）会改读盘上值；草稿优先不变、编辑代次
   基线不变（有专门用例守）。设置表单是另一份视图（`new_form_view`），不受影响。
4. **overlay 路径零影响**：每宠几何/设置写走的是 `pet_profiles.write_profile_geometry`
   与 `write_profile_settings(..., user_customized=True)`，不经
   `SlotRoutedConfig.save_geometry`，也不经 `PetWindow.change_scale`。
5. **未做真机 GUI 手测**：验证全部在 `QT_QPA_PLATFORM=offscreen` 下用真
   `AppShell` / `PetWindow` / 真档案文件跑，没有在真实桌面上点一次菜单确认"闪一下"
   消失。这一条留给本地部署后的人工验收。
