# Issue 草稿：v4.2.0 单进程共享模式下主动识屏永不触发

> 可直接贴到 GitHub Issues（仓库 MerZlin/dsh-pet-indesktop）。建议标题：
> 【v4.2.0 bug】experimental_single_process_spawn=true 时主动识屏永不触发（MultiWindowProxy._physics_mode 类型不匹配）

## 环境

- dsh-pet-standalone-webm-chat v4.2.0，Windows 10/11
- `experimental_single_process_spawn: true`
- `proactive_screen.enabled: true`，白名单非空，dry_run=false

## 现象

- 自动主动识屏**从不触发**：无任何日志（成功/失败格式均无）、`proactive_screen_state.json` 从未创建
- 手动「看看屏幕」一切正常（视觉链路完好，已用独立脚本端到端实测：模型能准确描述测试图片）
- 右键菜单反复切换主动识屏开关无效
- `dry_run=true` + `change_threshold=0` 组合下同样零输出（排除了截图、频控、dHash、API 全部环节）

## 根因（已对照 v4.2.0 tag 源码定位）

`multi_window_shared.py` 中 `MultiWindowProxy._physics_mode` 是聚合属性，返回 **bool**：

```python
@property
def _physics_mode(self) -> bool:
    return any(getattr(w, "_physics_mode", None) is not None for w in self._windows())
```

而 `proactive.py` 的 `_on_tick` G1 守卫按「哨兵值」语义读取它：

```python
interacting = (
    getattr(self.win, "_dragging", False)
    or getattr(self.win, "_physics_mode", None) is not None   # bool False is not None -> True !
    or getattr(self.win, "_click_effect_phase", 0) > 0
)
```

共享模式下 `self.win` 是 proxy：无窗口处于物理模式时属性返回 `False`，而 `False is not None` 恒为 `True` →
`interacting` 恒为 `True` → `should_watch()` 恒为 `False` → **每次 8s tick 都在 G1 被静默拦截**。
又因 `app.py` 将 `shared.proactive` 注入为窗口的 `proactive_watcher`，右键菜单开关拿到的也是同一实例，用户侧无法绕过。

## 修复建议

保持哨兵语义，属性无物理模式时返回 `None` 而非 `False`：

```python
@property
def _physics_mode(self):
    modes = [getattr(w, "_physics_mode", None) for w in self._windows()]
    modes = [m for m in modes if m is not None]
    return modes[0] if modes else None
```

## 附带问题（同一版本）

`experimental_single_process_spawn=false` 路径存在启动装配缺口：`PetWindow.__init__` 不调用
`sync_optional_services()`，`proactive_watcher` 与 `agent_link_manager` 均为懒创建（唯一触发点=
设置对话框关闭 / 右键开关），导致**配置已 enabled 的功能重启后不自启**。建议启动时按配置装配一次。

---

> 基线：v4.2.0（2026-09-22 对照 tag 源码定位）。本文件是**贴 issue 用的草稿**，贴出后
> 按 GitHub issue 跟踪，不再随代码演进更新；相关依据见
> [`PROACTIVE_SCREEN_PLAN.md`](PROACTIVE_SCREEN_PLAN.md)（识屏机制的设计出处与口径），
> 索引见 [`INDEX.md`](INDEX.md) 的「主动识屏与感知」小节。
