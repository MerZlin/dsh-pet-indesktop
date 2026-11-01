# petcore 扩展制作指南（从零到一个能装进设置页的 mod）

**基线**：petcore `0.4.0`、`api_version = "1"`、`schema = 1`（2026-11-01）。
本文是**动手向**指南：怎么建目录、每个文件写什么、装到哪、怎么验证。
读者有两类，命令不同：库里开发者用 `.venv/Scripts/python -m petcore …`；**只装了安装包的人走 GUI**（见 §7.1，窗口化 exe 的 `mods` 子命令看不到输出）。
字段/端口的完整参考不在这里重复：

| 你要查的东西 | 看哪份 |
|---|---|
| `mod.json` 每个字段与报错原文、能力模型、安装器细节 | [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md) |
| 角色包目录、动画分类、manifest、帧序列采纳规则 | [`PETCORE-CHARACTER-PACKS.md`](PETCORE-CHARACTER-PACKS.md) |
| 动画素材怎么画/怎么抠像成 640×360 透明 WebM | [`ASSET-PIPELINE.md`](ASSET-PIPELINE.md) |
| 10 个可抄的官方样板（待办/报时/抽签/AI 对话/网页 AI…） | [`../examples/petcore-mods/README.md`](../examples/petcore-mods/README.md) |
| **把这本手册丢给你的 AI** 让它代写扩展（自包含提示词） | [`PETCORE-MOD-AI-GUIDE.md`](PETCORE-MOD-AI-GUIDE.md) |
| core 内部分层与启动路径 | [`PETCORE-ARCHITECTURE.md`](PETCORE-ARCHITECTURE.md) |

---

## 1. 先分清三类产物

| 产物 | `mod.json` 的 `kind` | 能带代码吗 | 放在哪 | 典型例子 |
|---|---|---|---|---|
| **功能型扩展（feature mod）** | `feature` | 能（`entrypoint` 指向 `host/…:create_host`） | `<data_root>/mods/<id>/`；安装器勾选后 `<install>/mods/<id>/` | 待办、报时、天气、AI 对话 |
| **角色包（content mod）** | `content` + `character` 块 | **不能**（包内不许出现 `.py/.dll/.exe…`） | 同上（id 形如 `character.<名字>`） | 换一只新形象的桌宠 |
| **内容块**（音效/主题/短语/节日） | `content` | 不能 | 同上 | **运行时暂未消费，见 §10，别照抄** |

另外还有「内置 feature」——`petcore/builtin/features/` 里随 core 分发的那几个（自言自语、点击台词、快捷启动…）。
它们**不通过导入安装**，也不在扩展管理里启用/停用；写第三方扩展时不要照它们的位置放。

---

## 2. 东西到底放在哪（最容易搞错的一张表）

```
仓库（开发期）                     运行期（用户机器）
─────────────────────────────    ─────────────────────────────────────────────
examples/petcore-mods/<id>/   →   不是运行位置！只作为样板/安装源
petcore/builtin/features/     →   随 core 打包，不是用户扩展
assets/characters/<id>/videos →   随包只读（_internal/assets），不是用户位置

<data_root>/mods/<id>/            ① 用户数据目录：本地安装、市场安装、角色导入都落这里
<install>/mods/<id>/              ② 安装器投放：只有这里能带 .trusted（逐包授权）
<repo>/mods/                      ③ 源码树目录：会被扫描，但 feature **永远 disabled**
--mods-dir <dir>                  ④ 额外目录：同上，feature 也 **永远 disabled**
```

`<data_root>` 按平台：

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\dsh-pet-core` |
| macOS | `~/Library/Application Support/dsh-pet-core` |
| Linux | `~/.config/dsh-pet-core` |
| `--portable` | `<安装目录>/data`（带 `--instance` 时 `data-<name>`） |
| `--instance NAME` | `dsh-pet-core-NAME`（多份配置并存） |

**为什么 ③④ 的 feature 一定被禁用**：`petcore/mods/loader.py` 的判据是
「在 user_mods_dirs 内 **且**（`allow_feature_mods` 或安装目录里的 `.trusted`）」。
`<repo>/mods` 与 `--mods-dir` 都不在 user_mods_dirs 里，所以只适合放 **content**（角色包）；
**开发 feature 时请直接 `mods install` 到数据目录**，然后在「扩展管理」勾选「允许功能扩展」。

扫描顺序与覆盖关系（同名 id）：`<data_root>/mods` → `<install>/mods` → `<repo>/mods` → `--mods-dir`，
**后扫描的覆盖前面的**，并写一条 `duplicate mod id ...` 诊断（`mods doctor` 能看到）。

---

## 3. 功能型 mod：最小可运行骨架

一个能挂菜单、能进设置页、能定时说话的最小包（5 个文件）：

```text
hello.timer/
  mod.json
  host/
    __init__.py          # 只有一行导出
    factory.py           # create_host(context) -> FeatureDefinition
    runtime.py           # 运行逻辑 + commands
    settings_page.py     # 设置页（受信 UI，可以用 QtWidgets）
```

### 3.1 `mod.json`

```json
{
  "schema": 1,
  "kind": "feature",
  "id": "hello.timer",
  "name": "Hello timer",
  "name_zh": "整点打招呼",
  "version": "1.0.0",
  "api_version": "1",
  "core_requires": ">=0.2.0,<1.0.0",
  "author": "你的名字",
  "execution": "host-only",
  "entrypoint": "host/__init__.py:create_host",
  "capabilities": ["speech.present", "notification.present", "filesystem.user_data"]
}
```

要点（完整校验见 [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md) §2）：

- `id` 必须匹配 `^[a-z0-9][a-z0-9._-]{0,127}$`，且不能是 `con/prn/aux/nul/com1..9/lpt1..9`。
- `version` 必须是 `X.Y.Z`；`api_version` 必须字面等于 `"1"`（**不兼容就会被整包丢弃**）。
- `core_requires` 是逗号分隔的范围子句，例如 `">=0.2.0,<1.0.0"`；空串表示不限制。
- `execution` 目前只支持 `host-only`。写 `"worker"` 合法但**永远不会被加载**，只在日志里留
  `worker feature is not supported yet`。
- `capabilities` 只是**声明**（给人看/给市场看），运行期没有强制；取值必须来自
  `petcore/contracts/capabilities.py`，写错直接 `unknown capability`。
- `entrypoint` 只允许包内相对路径，`file.py:function`；`host/__init__.py` 也支持（可用 `from .runtime import …`）。

### 3.2 `host/__init__.py`

```python
from .factory import create_host

__all__ = ["create_host"]
```

### 3.3 `host/factory.py`（唯一的装配点）

```python
from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from .runtime import TimerRuntime
from .settings_page import TimerPage


def create_host(_context):
    holder = {}

    def runtime_factory(context):
        holder["runtime"] = TimerRuntime(context)
        return holder["runtime"]

    return FeatureDefinition(
        owner="hello.timer",
        contributions=(
            Contribution(
                kind="menu",
                id="greet",
                data={"label": "打个招呼", "command": "greet", "order": 90, "icon": "chat"},
            ),
            Contribution(
                kind="settings_page",
                id="timer",
                data={"title": "整点打招呼", "order": 700},
            ),
        ),
        runtime_factory=runtime_factory,
        settings_factory=lambda host: TimerPage(host, runtime=holder.get("runtime")),
    )
```

- `owner` 必须等于 `mod.json` 的 `id`。
- **`settings_factory` 收到的是 app host，不是 `FeatureContext`**（契约注解写的是后者，实际按前者调用）。
  所以设置页里可以直接 `host.config` / `host.window`，这也是官方样板的写法。
- 用 `holder` 把 runtime 传给设置页是官方样板（`examples/petcore-mods/reminder.todo/host/factory.py`）的做法：
  设置页可能需要让 runtime 立刻响应改动。

### 3.4 `host/runtime.py`（无 Qt）

```python
import logging

OWNER = "hello.timer"
log = logging.getLogger(OWNER)


class TimerRuntime:
    def __init__(self, context):
        self.context = context
        self.ports = context.ports
        # 菜单/命令面板按这个名字找入口：就是 Contribution 里的 command
        self.commands = {"greet": self.greet}
        self._handle = None

    # ---- 生命周期：start/stop 由 FeatureManager 调用
    def start(self):
        self._reschedule()

    def stop(self):
        if self._handle and self.ports.scheduler:
            self.ports.scheduler.cancel(self._handle)
        self._handle = None

    # ---- 命令：由菜单项触发（可能带 slot）
    def greet(self, slot=None):
        handle = self._pet(slot)
        text = self._message()
        if handle is not None:
            handle.say(text)
        elif self.ports.presentation:
            self.ports.presentation.say(text)

    def _pet(self, slot=None):
        registry = self.ports.pets
        if registry is None:
            return None
        if slot is None:
            return registry.primary()
        return next((item for item in registry.pets() if item.slot == int(slot)), None)

    # ---- 定时：一律经过 scheduler（跨线程安全，回调在 GUI 线程）
    def _reschedule(self):
        if self._handle and self.ports.scheduler:
            self.ports.scheduler.cancel(self._handle)
            self._handle = None
        cfg = self.ports.config
        interval_min = int(cfg.get("hello_timer_minutes", 60)) if cfg else 60
        if interval_min <= 0 or not self.ports.scheduler:
            return
        self._handle = self.ports.scheduler.call_every(
            interval_min * 60_000, self._tick, owner=OWNER
        )

    def _tick(self):
        self.greet(None)

    def _message(self):
        cfg = self.ports.config
        return str(cfg.get("hello_timer_text", "喝口水吧") if cfg else "喝口水吧")

    # ---- 设置页改完调这里（就是「立刻生效」的接缝）
    def preferences_changed(self):
        self._reschedule()
```

红线（有自动化测试守着）：

- **运行时代码不许 `import PySide6`**，也不许 `import petcore.net / petcore.api / petcore.account / petcore.platform`；
  网络、语音、截图、配置写入只能走端口。
- 需要回 GUI 线程时用 `ports.scheduler.call_later(0, cb)`——**这是唯一明确允许从任意线程调用的端口**。
- 一个扩展的异常不会拖垮 core：`FeatureManager.load()` 会把它变成一条诊断，但你也因此**永远不会收到报错弹窗**，
  所以关键失败要在自己的日志里说清楚（`logging.getLogger("hello.timer")`）。

### 3.5 `host/settings_page.py`

```python
from PySide6.QtWidgets import QLineEdit

from petcore.ui.settings.base import SettingsPage, make_button, make_spin

KEYS = ("hello_timer_minutes", "hello_timer_text")


class TimerPage(SettingsPage):
    title = "整点打招呼"

    def __init__(self, host, parent=None, *, runtime=None):
        super().__init__(host, parent)
        self.runtime = runtime
        self._initial = {key: host.config.get(key) for key in KEYS}

        self.group_heading("整点打招呼")
        card = self.section("提醒内容", level=3)
        self.minutes = make_spin(self)
        self.minutes.setRange(0, 240)
        self.minutes.setSuffix(" 分钟")
        self.text = QLineEdit(str(host.config.get(KEYS[1], "喝口水吧")), self)
        self.text.setPlaceholderText("到点要说的话")
        card.add_row("间隔", self.minutes, "0 表示关闭定时。", expand=True)
        card.add_row("说的话", self.text, "会显示在这只桌宠的气泡里。", expand=True)
        card.add_buttons([make_button("现在试一次", self, primary=True)])
        self.add_note("改动立即生效；点「取消」会恢复到打开设置前的值。")

        self._load()
        self.minutes.valueChanged.connect(self._changed)
        self.text.textChanged.connect(self._changed)

    def _load(self):
        self.minutes.blockSignals(True)
        self.minutes.setValue(int(self.host.config.get(KEYS[0], 60)))
        self.minutes.blockSignals(False)

    def _changed(self, _value=None):
        self.host.config.set(KEYS[0], self.minutes.value())
        self.host.config.set(KEYS[1], self.text.text())
        if self.runtime:
            self.runtime.preferences_changed()
        self.changed.emit()          # 必须自己发：基类不会替你发

    # ---- 设置窗只在 dirty() 为真时才保存/回滚这一页
    def dirty(self):
        return any(self.host.config.get(key) != value for key, value in self._initial.items())

    def confirm_save(self):
        self._initial = {key: self.host.config.get(key) for key in KEYS}

    def discard_changes(self):
        for key, value in self._initial.items():
            self.host.config.set(key, value)
        self._load()
        if self.runtime:
            self.runtime.preferences_changed()
        self.changed.emit()

    def keywords(self):
        return ["打招呼", "定时", "间隔", "hello timer"]
```

设置页契约（`petcore/ui/settings/base.py`）：

| 成员 | 作用 |
|---|---|
| `title`（类属性） | 导航/标题兜底（`settings_page.data["title"]` 优先） |
| `changed` 信号 | 实时预览；**页面自己 emit** |
| `dirty()` / `confirm_save()` / `discard_changes()` | 设置窗的保存/取消语义；`confirm_save()` 返回 `False` 表示保存失败 |
| `dispose()` / `reflow()` | 页面被卸载 / 字号变化后重排 |
| `keywords()` | 设置搜索命中的关键词（页面里所有可见文字也会被索引） |
| `group_heading/section/add_note/add_row/add_buttons` | 统一的 H2/H3/说明/行布局 |
| `make_button/make_checkbox/make_spin/make_slider/value_field` | 统一控件工厂 |

**不继承 `SettingsPage` 也能跑**，但会被包成只读占位页并提示「改动不会随设置保存」——别踩。

---

## 4. 接线点：菜单、命令、设置页

| 你想要 | 怎么做 | 真实生效的 `data` 键 |
|---|---|---|
| 右键菜单多一项 | `Contribution(kind="menu", id=..., data={...})` | `label`、`command`（默认取 `id`）、`order`（默认 500）、`icon`、`checked`（callable） |
| 设置里多一页 | `Contribution(kind="settings_page", id=..., data={...})` + `settings_factory` | `title`、`order` |
| 让菜单项能被执行 | runtime 的 `self.commands = {"<command>": callable}` | **不是** `Contribution(kind="command")`，那个没人读 |
| 命令要知道是哪只桌宠 | 命令声明 `slot` 形参即可（`def greet(self, slot=None)`） | core 会按发起菜单的桌宠传 `slot` |
| **对话窗里占一页** | `Contribution(kind="chat_view", id=..., data={...})` + `view_factories` | `label`（必填）、`icon`、`order`；见 §4.2 |
| 内嵌进 Core 页面 | `data={"host_page": "interaction"}` 且 `owner` 在 `CORE_FEATURE_IDS` | 仅 core 内置 feature 用得上 |

当前**真正被消费的 contribution 是 `menu`、`settings_page` 与 `chat_view`**。
`command / hotkey / card / install_hook` 虽在 `CONTRIBUTION_KINDS` 白名单里，但没有任何
消费点——写了不会生效；`phrases / sound / theme / seasonal` 由 loader 的内容块处理，
更推荐直接写在 `mod.json` 里。

菜单项注册后的 id 是 `<owner>:<contribution>`。默认只出现在右键菜单的
「扩展 → <manifest 里的 name_zh / name / id> → 你的菜单项」这一层里（分组标题取 manifest 的名字）。
想放到主菜单固定位置，要在默认布局里按这个 id 摆位（core 侧行为，第三方扩展不需要动）。

端口怎么选（按用途）：

| 想做的事 | 用哪个端口 | 关键签名 / 备注 |
|---|---|---|
| 冒泡、图片气泡、通知、动画 | `presentation` | `say(text, duration_ms=, force=, subtitle=)`、`show_image`、`play_animation(name, loop=)`、`notify` |
| 连续多气泡 | `presentation.say_sequence(items, on_show=…)` | 不要在扩展里自己拼 QTimer |
| 定时/回 GUI 线程 | `scheduler` | `call_later(ms, cb, owner=)`、`call_every`、`cancel`、`cancel_owner`；**唯一跨线程安全** |
| 存自己的数据 | `storage.data_dir(owner)` | 落 `<data_root>/feature-data/<owner>/`，自己写 JSON 时请原子替换 |
| 读偏好 | `config.get(key, default)` | 只读；运行时代码**不要**拿它当状态存储反复写 |
| 用户点/拖文件/每帧 | `interaction` | `on_click` / `on_drop(list[Path])` / `on_tick(seconds)`，回调在 GUI 线程 |
| 桌宠句柄（位置/动画/说话） | `pets` | `pets()` / `primary()` / `pet(slot)`；`PetHandle.body_rect()`、`say`、`play_animation` |
| 取网络数据 | `http` | `get_json(url, …)` / `request(HttpRequest)`；**同步阻塞**，自己开线程 |
| 语音朗读 | `speech` | `speak(request, owner=, slot=, on_done=…)` 非阻塞；`synthesize()` 阻塞 |
| 截图/识屏 | `screen` | `capture()` / `sample_foreground()`；**GUI 线程** |
| 截图提问窗口 | `screen_ask` | `on_ask` 回调在**后台线程**，只许碰纯数据 |
| 免打扰状态 | `quiet` | `quiet_active()`、`set_quiet(seconds)` |

### 4.1 宿主能力与窗口：2026-10-27 实测边界（`tool.ai-browser` 探针）

写扩展前必须知道的三条实测结论（不是推断，证据见
[`PR-REPORT-AI-BROWSER-MOD-2026-10-27.md`](PR-REPORT-AI-BROWSER-MOD-2026-10-27.md)）：

1. **窗口不是端口，但事实上能造；对话窗里的「一页」则有正式接口。**
   要独立窗口：官方样板 `fun.divination/host/runtime.py` 就在命令里惰性导入 `QDialog` 再 `show()`。
   core 不提供窗口端口/生命周期治理（置顶、跟随桌宠、多宠归属都得自己做），所以**别自己再造聊天窗**；
   要占对话窗的一页，用 §4.2 的 `chat_view`——窗口 chrome、圆角、airspace、深色与生命周期
   都由 core 负责，扩展只交一个 QWidget。
   自造窗口时：Qt 代码单独成模块、只在 GUI 线程命令里惰性导入，窗口对象自己持引用
   （`_WINDOW`），关闭时自己存几何。
2. **宿主有没有某个 Qt 模块，取决于它怎么打包，不取决于你的 `mod.json`。** PyInstaller
   产物只收被静态分析到/显式声明的模块。0.4.0 起安装包**有** `PySide6.QtWebView`
   （`Qt6WebView.dll` + `plugins/webview/qtwebview_webview2.dll`，见
   `scripts/slim_bundle.py` 的 `REQUIRED_GLOBS`），**没有** `QtWebEngine*`（200MB 的
   Chromium 整套没打）。写法：`importlib.util.find_spec("PySide6.QtWebView")` 探测 →
   不可用就别声明依赖它的功能，并在设置页显示当前档位
   （`tool.ai-browser/host/engine.py`）。
3. **`capabilities` 只是声明。** `network.request` / `process.spawn` 不产生端口、不产生闸门，
   也没有沙箱：`tool.ai-browser` 用标准库 `subprocess` 直接拉 Edge `--app` 也在运行。
   声明照写（便于阅读与将来审计），但不要指望它替你兜底。

设置页容器还有一条几何事实：页面内容在 `QScrollArea` 里、默认阅读宽度 860
（`content_max_width=None` 可放开）。**把 webview 塞进设置页不可用**——浏览器类 UI
必须自造窗口。

---

### 4.2 对话窗里加一页：`chat_view`（0.4.0 起）

网页 AI 就是这条缝的第一个客户：`Contribution(kind="chat_view")` 声明按钮，
`FeatureDefinition.view_factories` 交一个 QWidget。

```python
# factory.py
def build_view():
    from .my_view import MyView      # Qt 只在真正切过去时导入
    return MyView()

Contribution(kind="chat_view", id="mine",
             data={"label": "我的面板", "icon": "globe", "order": 100})
FeatureDefinition(..., view_factories={"mine": build_view})
```

```python
# my_view.py（四个生命周期方法全部可选）
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

class MyView(QWidget):
    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        box.addWidget(QLabel("我的一页"))

    def activate(self):        # 切到这一页：恢复轮询/继续加载
        ...
    def deactivate(self):      # 切走：停加载、停定时器（别在后台烧 CPU）
        ...
    def set_dark(self, dark):  # 对话窗深色开关变化
        ...
    def shutdown(self):        # 对话窗关闭：释放页面
        ...
```

菜单一键切过去：`self.ports.chat.show_view(OWNER, "mine", pet_slot=slot)`——
它会复用用户**正在用的那扇**对话窗，不会另开一个。

铁律：工厂只在 GUI 线程、第一次切到该页时调用一次；工厂抛异常或返回非 QWidget 会显示
错误占位（对话窗不会挂）；视图被放进内缩 12px 的槽位（原生子窗口不吃圆角裁剪）；
每开一次对话窗都回到 AI 对话，**别指望你的页是默认页**。完整契约见
[`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md) §5.2，参考实现
`examples/petcore-mods/tool.ai-browser/host/web_pane.py`。

---

## 5. 线程与生命周期铁律

1. **只有 `scheduler` 明确允许从任意线程调用**，回调一律回到 GUI 线程。
2. `presentation / pets / interaction / audio / screen / system.screens / reminders` 必须 GUI 线程。
3. `http.request/get_json`、`speech.synthesize`、`config.set`、`storage.data_dir` 都是**同步阻塞**：
   放在你自己的后台线程里，别堵 UI。`speech.speak()` 是异步入口，回调会回 GUI 线程。
4. `screen_ask.on_ask/on_action` 的回调在**后台线程**：不要在里面碰 Qt，把结果通过 scheduler 送回来。
5. `stop()` 里先 `cancel(handle)` / `cancel_owner(...)`，再清状态；core 还会替你
   `cancel_owner`（`speech`/`reminders`/`interaction`）。
6. `Runtime.close()` 从未被 core 调用——把清理写在 `stop()` 里。

---

## 6. 配置与数据

- **第三方扩展的偏好键会自动落进 `config.json` 的 `extra` 块**：键名必须匹配
  `^[a-z0-9][a-z0-9._-]{0,127}$`，单值 ≤ 4KB、总量 ≤ 64KB。
- 设置页里用 `host.config.set(...)` 并立即 `changed.emit()`（官方样板就是这么写的：改动即时生效，
  「取消」靠 `discard_changes()` 回滚）。
- runtime 里只用 `ports.config.get()` 读；**不要**把配置当数据库反复写。
- 自己的业务数据（清单、缓存）放 `ports.storage.data_dir(OWNER)`，写文件用
  「临时文件 + `os.replace`」原子替换；core 的卸载**不会删** `feature-data/<owner>`，所以要在设置里给用户明确的清理入口。

---

## 7. 本地开发闭环（照着敲就行）

```bash
# 0) 假设包目录是 ./hello.timer
.venv/Scripts/python -m petcore mods validate ./hello.timer     # 静态校验，输出 ok: ./hello.timer

# 1) 装进用户数据目录（<data_root>/mods/hello.timer）
.venv/Scripts/python -m petcore mods install ./hello.timer

# 2) 打开「允许功能扩展」（全局开关，默认关；它不是 mods 子命令，别搞混）
.venv/Scripts/python -m petcore --allow-feature-mods     # 启动时置 True 并落盘
#    或者：设置 → 扩展管理 → 勾选「允许功能扩展」→ 点「重新加载扩展」
#    （`mods enable <id>` 只清掉「已停用」记录，不负责这个全局开关）

# 3) 启动应用；已经在跑就点「扩展管理 → 重新加载扩展」
.venv/Scripts/python -m petcore

# 4) 排查
.venv/Scripts/python -m petcore mods list          # enabled/disabled + 诊断（! 前缀）
.venv/Scripts/python -m petcore mods doctor        # root、状态文件、目录存在性、统计；有诊断返回码 1
#    日志：<data_root>/logs/petcore.log，grep "feature/mod diagnostic"

# 5) 卸载（会按 install.json 逐文件删；feature-data 保留）
.venv/Scripts/python -m petcore mods remove hello.timer
```

两个已知坑：

- `install` 同 id 已存在会拒绝（`already installed: ... (use --force to replace)`）；覆盖加 `--force`。
- `mods` 子命令在 argv 里的位置是宽松的，但 `pack-character` / `pack-legacy` **必须是第一个参数**：
  `petcore --root X pack-character …` 会落到 GUI 启动分支。

### 7.1 只有安装包、没有源码和 venv 的人

安装包里的 `dsh-pet-core.exe` 是**窗口化**程序：`mods` 子命令仍然能跑，但
**控制台输出会被丢弃**（`petcore/cli.py` 的 `_ensure_streams()` 把 `stdout/stderr` 接到 `devnull`），
所以别指望在 cmd 里看到 `mods list` 的结果。这种人（包括外部测试者/第三方作者）走 GUI：

1. 设置 →「扩展管理」→「导入 ZIP」或「导入目录」→ 选你的包（落 `<data_root>/mods/<id>`）
2. 同一页勾选「允许功能扩展」（默认关）
3. 点「重新加载扩展」
4. 包有问题时，同页的「诊断」区会列出原因（`entrypoint not found`、`feature mods disabled`、`duplicate mod id …`）
5. 卸载：列表里选中 →「卸载」（`feature-data/<owner>` 保留）

要看到命令行输出，只能二选一：用源码环境（`.venv/Scripts/python -m petcore mods …`），
或者自己用 `scripts/build_petcore.ps1 -Console` 构建一个带控制台的版本。

---

## 8. 打包与分发

```text
hello.timer.zip
└── hello.timer/           ← 只允许一层 wrapper 目录
    ├── mod.json
    └── host/…
```

- `mods install x.zip` 与安装目录都接受；压缩包里出现**两个及以上** `mod.json` 根会报
  `multiple mod.json roots found in archive`（请只留一层）。
- ZIP 会做安全扫描：绝对路径、盘符、`..`、NTFS 保留名、大小写重复、符号链接条目一律拒绝。
- 安装成功后写 `<mod>/install.json`（每个文件的 sha256），卸载按它逐文件删；**没有它的包**（安装器投放的）
  卸载时整目录删。
- `.trusted` 只在**安装目录**下有效；从 UI/CLI 导入时会剥掉包里自带的 `.trusted`，所以「自己放个空文件绕过开关」不可行。
- Windows 安装器把官方扩展开成勾选项：勾了才写 `<install>/mods/<id>` + `.trusted`，不勾完全不落盘；
  第三方扩展目前仍走「导入 ZIP / 目录」或插件市场（见 [`PETCORE-MARKET.md`](PETCORE-MARKET.md)）。
- **没有签名与哈希校验**：安装器只记录文件 sha256 用于卸载，不用于加载前验证。

---

## 9. 素材包（角色包）怎么做、放哪

角色包的**规范**在 [`PETCORE-CHARACTER-PACKS.md`](PETCORE-CHARACTER-PACKS.md)（布局、动画分类、`move_strides`、
帧序列采纳）。这里只讲开发流程与落位。

### 9.1 两种来源

```text
A. 仓库里的官方角色（读路径，不是用户路径）
   assets/characters/<id>/videos/{manifest.json, idle/, move/, click/, events/…}
   → 随包进 _internal/assets，升级即替换；改它等于改产品

B. 你自己做的角色（推荐）
   任意源目录/<你的素材>/
     manifest.json      {"name": "...", "body_box": [x1,y1,x2,y2], "author": "..."}
     videos/…           ← 也可以把媒体直接放源目录根
   → pack-character 生成 content mod：<data_root>/mods/character.<id>/
```

`manifest.json` 只认这几个键：`name`、`body_box`、`head_box`、`author`。
`canvas` / `feet_y` 固定 640×360 / 330（写了也不读）；legacy 的 `chat`/`prompt`/`theme_color` petcore 不读。

`body_box` 不是装饰：贴边、右下角落位、位置恢复、屏幕热插拔都用 `body_box × scale` 的身体框。
**按 alpha≥128 量**，不写就退化成整张画布。

### 9.2 三个入口（都是同一套构建器）

| 入口 | 命令 / 操作 | 说明 |
|---|---|---|
| 设置页 | 设置 →「常规」→ 数据 →「导入角色目录 / 导入角色 ZIP」 | 内部 `install_character_source()`：校验 id → 构建到 `.character-import` → 安装；**不覆盖已有 id** |
| CLI 构建 + 安装 | `pack-character <src> --out <dir> --id <名字>` 再 `mods install <dir>` | `pack-character` 只**生成目录**，不装、不读 `--root` |
| 手工 | 直接把 `mod.json` + `videos/` 放进 `<data_root>/mods/character.<id>/` | 启动时会被扫到 |

> `pack-character` 的产物**不是 zip**：`--out dist/mycat.zip` 会生成一个叫 `mycat.zip` 的**目录**。
> 要分发就自己把这个目录压成 zip。
> 源目录里已经有 `mod.json` 会被拒（`source already contains mod.json`）。

### 9.3 动画文件夹 → 动作映射（写错文件夹名 = 该动作没有）

| 文件夹 | 动作 | | 文件夹 | 动作 |
|---|---|---|---|---|
| `idle/`、`idle_turn/` | 待机循环 | | `touch_head/` | 摸头 |
| `turn/` | 转身 | | `say/` | 说话 |
| `move/` | 移动 | | `sleep/` | 睡觉循环 |
| `click/` | 点击 | | `startup/`、`start_up/` | 启动 |
| `drag/`、`raised_static/` | 被拖起 | | `work/` | 工作循环 |
| `raised_dynamic/` | 拖起挣扎 | | `events/<组>/` | 只由 `play_event()` 触发 |

其余文件夹（包括 `random/`）按 `DEFAULT/SINGLE` 处理。媒体后缀只认 `.webm` / `.gif`。

### 9.4 明确不支持（别在文档/README 里承诺）

- **没有** `<data_root>/characters` 自动扫描（那是 legacy `pet` 的行为）。
- **没有热加载**：装完要在「扩展管理」点「重新加载扩展」或重启。
- **没有** 打包预览、远程角色下载、角色级图标/头像、签名与校验和。
- `character.phrases` / `phrases.json` 会被读进 `CharacterSpec`，但**当前没有任何消费方**。

---

## 10. 内容块（sounds / themes / phrases / seasonal）：现在别用

`mod.json` 允许声明 `sounds` / `themes` / `phrases` / `seasonal` 四个块，loader 也会把它们变成
`ContentEntry`；但 `petcore/content/collect.py` 只消费 `kind == "character"` 的条目，
**运行期没有任何代码读另外四类**。所以今天写它们＝写了个不会被看见的包。
等这批接线落地后再更新本节（届时会同步 [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md)）。

---

## 11. 红线与门禁（提交前自查）

| 规则 | 由谁守 |
|---|---|
| 扩展不许 `import petcore.net / petcore.api / petcore.account / petcore.platform` | `tests/test_petcore_architecture.py::test_mods_use_ports_not_core_transport` |
| 扩展不许 `import petcore.ui.*`（设置页除外：`settings_page.py` 或 `petcore.ui.settings.*`） | 同上 |
| 有菜单贡献的**内置/示例**扩展必须有设置页 | `tests/test_petcore_builtin_mods.py`、`tests/test_petcore_example_mods.py` |
| `mod.json` 字段/路径/可执行文件扫描 | `petcore/mods/manifest.py`（`mods validate` 就是它） |
| core 内置菜单项必须有设置落点 | `tests/test_petcore_settings_coverage.py` |
| 引擎层无 Qt、contracts 只用标准库 | `tests/test_petcore_architecture.py` |

注意两处**只是文档口径、没有自动化守卫**：运行时代码不许 `import PySide6`（架构测试不查直接 import）；
`capabilities` 不做运行期强制。别因为没人拦就违反。

---

## 12. 排错对照表（错误原文 → 原因 → 修法）

| 现象 / 报错 | 原因 | 修法 |
|---|---|---|
| 扩展管理里看不到自己的包 | 没装进 `<data_root>/mods`，或 `mod.json` 校验失败 | `mods validate`；`mods list` 看 `!` 诊断 |
| 包在那儿但显示「已停用」/菜单没项 | feature 包位于 `<repo>/mods` 或 `--mods-dir`；或没开 `allow_feature_mods`；或该包被 `mods disable` 过 | 装到数据目录；`python -m petcore --allow-feature-mods`（或设置里勾选）；`mods enable <id>` |
| `feature mods disabled` | 同上（loader 的诊断原文） | 同上 |
| `worker feature is not supported yet` | `execution: "worker"` | 改 `host-only` |
| `entrypoint not found: …` | 路径写错（只 warning，包仍加载但运行时为空） | 核对相对路径与文件名 |
| `create_host must return FeatureDefinition` | 工厂返回了别的对象 | 用 `FeatureDefinition(...)` |
| `api_version must be '1'` | 写了别的版本 | 改成 `"1"` |
| `core_requires not satisfied` | 范围子句与当前 core 版本不符 | 放宽 `core_requires` |
| `invalid id: …` | 大写/空格/保留名 | 用 `^[a-z0-9][a-z0-9._-]{0,127}$` |
| `path escapes pack: …` | `entrypoint`/资源路径跑出包外 | 改成包内相对路径 |
| `executable file in content pack` | content 包里混进了 `.py/.dll/.exe…` | content 包只放媒体与 json |
| `duplicate mod id … overridden by …` | 同 id 在多个目录 | 删掉旧副本（`mods doctor` 会列出） |
| `already installed: … (use --force to replace)` | 同 id 已存在 | 先 `mods remove` 或加 `--force` |
| `multiple mod.json roots found in archive` | zip 里有多层包 | 只留一层 wrapper |
| `invalid character id: …` | 目录/ZIP 名带空格或大写 | 重命名成合法 id |
| 角色装上了但「切换角色」里没有 | `character.media` 指向的目录不存在 / 媒体为空 | 检查 `mod.json` 的 `character.media` 与 `videos/` |
| 改代码没生效 | 扩展模块被缓存；或只改了 `examples/` 源目录 | 「重新加载扩展」（会清 `sys.modules`）；确认改的是**已安装**那份 |
| 自造的窗口不出现/被别的窗口压住 | 没有持住窗口引用；或只 `show()` 没有 `raise_()`/`activateWindow()` | 模块级 `_WINDOW` 持引用；`show()` 后再 `raise_()`；需要压过桌宠时才临时置顶 |
| 设置页里塞 webview 变成一条缝 | 设置页内容在 `QScrollArea` 里，默认阅读宽度 860 | 浏览器/大画布类 UI 自造窗口；设置页只放开关与按钮 |
| 内嵌 `QWebEngineView` 报 ImportError / 窗口空白 | 宿主安装包没打 QtWebEngine（`_internal/PySide6` 无 `QtWebEngine*`） | 先 `importlib.util.find_spec("PySide6.QtWebEngineWidgets")` 探测；不可用就走外部通道并在设置页显示档位 |
| 窗口能开但关掉后内存不降 | QtWebEngine 进程复用，属引擎固有行为 | 不是泄漏；要省内存就别在常驻设置页里养 webview |

---

## 13. 提交前 checklist

1. `mods validate` 干净；`mods install` 后 `mods list` 里是 `enabled` 且没有 `!` 诊断。
2. 菜单项能点、设置页能存能取消；`dirty()` 在改过之后为真、保存后归零。
3. 运行时代码里没有 `PySide6`、没有 `petcore.net/api/account/platform`、没有 `petcore.ui.*`（设置页文件除外）。
4. 所有定时器走 `scheduler`，`stop()` 里能全部取消；后台线程的回调用 `call_later` 回 GUI。
5. 自己的数据写在 `storage.data_dir(owner)`，原子写；不要在 `config` 里堆业务状态。
6. 新配置键符合 `^[a-z0-9][a-z0-9._-]{0,127}$`，体积在 `extra` 限额内。
7. 若带菜单贡献：同时提供设置页（内置/示例包会被测试强制）。
8. 角色包：`body_box` 按 alpha≥128 量过；文件夹名对上 §9.3；不覆盖已有 id。
9. 跑一遍 `tests/test_petcore_architecture.py`、`test_petcore_builtin_mods.py`、
   `test_petcore_example_mods.py`（如果你改的是内置/示例包）。
10. 若自造窗口：Qt 代码单独成模块、只在 GUI 线程的命令里惰性导入；窗口自己持引用、
    关闭时存几何/停定时器；**先探测宿主有无所需 Qt 模块**再决定是否降级（见 §4.1）。
10.1 若用 `chat_view`：`label` 非空、`view_factories` 有同名工厂；视图实现 `deactivate()`
    （至少停加载）；别在工厂里做耗时 IO；需要宿主 Qt 模块时先 `find_spec` 自检，
    不可用就**不要**声明这个贡献（宁可没按钮，也别给用户一个点了必报错的入口）。
11. 若新增 example 包：`packaging/petcore.iss` 要加默认不勾选的安装项，
   否则 `tests/test_petcore_installer_mods_opt_in.py` 会红。

### 本文骨架的实测记录（2026-10-27）

§3 的五个文件被原样抽出来跑过一遍（脚本只解析本文件，不手抄代码）：

```text
mods validate            → ok
mods install             → installed hello.timer 1.0.0 -> <root>/mods/hello.timer
CoreHost 加载            → loaded features 含 hello.timer
右键菜单                 → 扩展 → 整点打招呼 → 打个招呼
runtime.commands         → ['greet']
设置页                    → 导航里出现「整点打招呼」
```

也就是说：照抄三个代码块、改个 id/名字就能跑通「菜单 → 命令 → 设置页」这条最短链路；
剩下的（定时、说话、存数据）已经在 §3.4 的 runtime 里给全了。
