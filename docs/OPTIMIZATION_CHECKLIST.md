# 性能优化复核清单（给 Kimi Code 独立验证）

> 目标：确认本工作区 `D:\dsh-pet-pr` 的性能优化**真实有效、无回归、不影响使用**。
> 复核人：Kimi Code（只读检查 + 跑测试，不提交、不推送、不改代码）。

---

## 0. 前置状态

- [ ] 当前分支为 `main`，与上游同步（`git status` 应只有下列改动/新增文件）
- [ ] 改动文件：
  - `M pet/app.py`
  - `M pet/config.py`
  - `M pet/library.py`
  - `M pet/webm_clip.py`
  - `M pet/window.py`
- [ ] 新增测试：
  - `?? tests/test_window_pause.py`
  - `?? tests/test_library_priority_warm.py`
  - `?? tests/test_webm_meta_cache.py`
  - `?? tests/test_config_instance.py`
- [ ] `tools` 为历史遗留未跟踪文件，与本轮无关，可忽略

---

## 1. 快速验证命令（必须全部通过）

```powershell
cd D:\dsh-pet-pr

# 1) 全量测试
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q
# 期望：122 passed, 4 skipped

# 2) 语法编译
.\.venv\Scripts\python.exe -m compileall -q pet packaging scripts
# 期望：无输出、退出码 0
```

---

## 2. 逐项代码核对

### 2.1 窗口隐藏时暂停解码 / 恢复（`pet/window.py`）

检查点：
- [ ] `PetWindow.__init__` 存在 `self._hidden_paused = False`
- [ ] 存在 `hideEvent` → 调用 `_pause_activity()`
- [ ] `_pause_activity()` 做了：
  - `self.movie.stop()`
  - 停止 `_move_timer` / `_physics_timer` / `_fullscreen_timer` / `_topmost_watchdog` / `_self_talk_timer` / `_animation_gap_timer` / `_click_effect_timer` / `_squash_timer`
  - `self._cancel_move()`、`self._cancel_animation_gap()`
  - `self._speech_bubble.hide()`
- [ ] `showEvent` 中：`if self._hidden_paused: self._hidden_paused = False; self._resume_activity()`
- [ ] `_resume_activity()` 做了：
  - `self._switch(self.anim)` 恢复当前动画
  - 置顶开启时重启 `_topmost_watchdog`
  - `auto_hide_fullscreen` 且 Windows 时重启 `_fullscreen_timer`
  - `self._schedule_self_talk()`
- [ ] `show_bubble()` 和 `set_chat_status()` 开头有 `if not self.isVisible(): return`
- [ ] 测试 `tests/test_window_pause.py` 覆盖：隐藏后动画停止、定时器停、气泡不显示；显示后恢复

### 2.2 素材懒加载 + 默认优先级预热（`pet/library.py`）

检查点：
- [ ] `MovieLibrary.__init__` 有 `self._paths` 和 `self._movies`（懒加载缓存）
- [ ] `_load_all()`：
  - 只把 `name -> Path` 存入 `self._paths`，**不再一次性创建全部 clip**
  - 通过 `_priority_names()` 计算高优先级，并在**主线程**预创建高优先级 clip
  - **不再自动启动任何预热线程**
- [ ] `_priority_names()` 规则：
  - 高优先级 = `idles + turns + moves + clicks + drag`
  - 低优先级 = `acts` 中不在高优先级里的（随机动作池）
- [ ] `movie(name)`：按需创建 `WebMClip`/`GifClip` 并缓存
- [ ] `names()` 返回全部路径名；`movies()` 只返回已创建 clip
- [ ] `schedule_high_priority_warm()`：后台线程预热高优先级，线程内先 `time.sleep(random.uniform(0, 0.5))` 错峰
- [ ] `schedule_low_priority_warm()`：启动 2 秒 QTimer，到期后 `_warm_low_priority_background()`
- [ ] `_warm_low_priority_background()`：**在主线程**创建低优先级 clip，再开 daemon 线程执行 `_warm_objects(clips, workers=1)`（不能阻塞事件循环）
- [ ] 测试 `tests/test_library_priority_warm.py` 覆盖：构造时只创建高优先级、`_priority_names` 划分正确、低优先级预热不自动启动

### 2.3 vision / PIL 延迟导入（`pet/window.py`）

检查点：
- [ ] 文件顶部**不存在** `from . import vision as vision_mod`
- [ ] `_look_worker()` 内第一行附近有 `from . import vision as vision_mod`
- [ ] 效果：无 Chat / 不点“看看屏幕”的实例启动时不加载 Pillow

### 2.4 WebM 元数据跨进程缓存（`pet/webm_clip.py`）

检查点：
- [ ] 模块级存在：
  - `_META_FILE_CACHE_PATH = Path(tempfile.gettempdir()) / "dsh-pet-media-meta-cache.json"`
  - `_META_FILE_CACHE`、`_META_CACHE_LOCK`
  - `_load_meta_file_cache()` / `_get_meta_file_cache()` / `_save_meta_file_cache_entry()`
- [ ] `_ensure_meta()` 逻辑：
  - `cache_key = f"{path}|{mtime_ns}|{size}"`
  - 先查进程内 `_META_CACHE`
  - 再查文件缓存 `_get_meta_file_cache()`
  - 都未命中才调 `imageio_ffmpeg.count_frames_and_secs()`
  - 计算成功后写入内存缓存 + 文件缓存（原子替换）
- [ ] 测试 `tests/test_webm_meta_cache.py` 覆盖：清空内存缓存后第二个 clip 直接命中文件缓存，不再调 ffmpeg

### 2.5 多开配置隔离（`pet/config.py` + `pet/app.py`）

检查点：
- [ ] `Config.__init__(self, base=None, instance_id: str | None = None)`
- [ ] `self.instance_id` 来自参数或环境变量 `DSH_PET_INSTANCE`
- [ ] 有 instance_id 时 `self.path = self.dir / f"config-{instance_id}.json"`
- [ ] 无 instance_id 时仍为 `self.dir / "config.json"`（单开行为不变）
- [ ] `_migrate_legacy_config()` 在 `self.instance_id` 非空时直接 return（不做旧版迁移）
- [ ] `pet/app.py main()` 解析 `--instance <id>` 并传给 `Config`
- [ ] 测试 `tests/test_config_instance.py` 覆盖：实例配置与默认配置互不影响

### 2.6 余额跨实例共享缓存（`pet/app.py`）

检查点：
- [ ] `PetApp.__init__` 有 `self._balance_cache_path = config.dir / 'balance_cache.json'`
- [ ] `show_balance()` 先查内存缓存，再查 `_read_balance_file_cache()`，命中则直接显示
- [ ] `_balance_worker()` 成功查询后调用 `_write_balance_file_cache(text)`
- [ ] 文件缓存带 `ts`，30 秒内有效；写入用 `.json.tmp` + `os.replace` 原子替换

### 2.7 日志按 PID 隔离（`pet/app.py`）

检查点：
- [ ] `_setup_logging()` 使用 `config.dir / f'pet-{os.getpid()}.log'`
- [ ] 不再多个实例共写同一个 `pet.log`

---

## 3. 风险回归检查（人工/代码审阅）

- [ ] **单开行为不变**：不传 `--instance` 时配置路径、日志路径、启动流程与旧版一致
- [ ] **隐藏/恢复**：托盘隐藏、右键“隐藏桌宠”、全屏自动隐藏三条路径都会触发 `hideEvent`；显示时动画恢复、置顶/全屏 watcher 按配置重启
- [ ] **动画链不中断**：`PetWindow._switch` 通过 `_connect_movie` 按需连接信号，同一动画只连接一次；随机动作首次播放不会因为没连接信号而卡死
- [ ] **多开**：第二个实例 `python -m pet --instance pet2` 使用独立配置；余额查询 30s 内多实例共享文件缓存；日志按 PID 分开
- [ ] **预热线程安全**：所有 `WebMClip` 创建都在主线程；后台线程只调 `warm_meta()` / `warm_first_frame()`（QImage 线程安全）
- [ ] **无新增常驻轮询/网络行为**：除了原有余额定时器，没有新增轮询；全屏 watcher 反而在隐藏时停止

---

## 4. 结论输出格式

请按以下格式回复：

```
## 复核结论
- 测试：122 passed / 4 skipped ✅ / ❌（附失败详情）
- compileall：通过 ✅ / ❌
- 逐项核对：2.1~2.7 全部通过 / 列出未通过项
- 风险回归：通过 / 列出风险点
- 问题清单：无 / 具体问题
- 建议：可放行本机试验 / 需要先修复 xxx
```
