# petcore 扩展制作指南（从零到一个能装进设置页的 mod）

> **基线**：petcore `0.5.0`、`api_version = "1"`、`schema = 1`（2026-11-01 工作区）。
> 本文是**动手向**指南：目录怎么建、每个文件写什么、装到哪、怎么验证、哪些事今天做不到。
>
> 只想拿一份自包含说明丢给 AI 代写扩展，用
> [`PETCORE-MOD-AI-GUIDE.md`](PETCORE-MOD-AI-GUIDE.md)；字段级参考与报错原文见
> [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md)。本文的命令与结论都在当前工作区核对过。

---

## 0. 五分钟判断：你要做哪一种

| 你想做的事 | 产物 | 能带 Python 吗 | 典型例子 |
|---|---|---|---|
| 换一只新形象的桌宠 | **content 包**（`kind=content` + `character`） | 不能 | `character.shenshen` |
| 加菜单、设置页、对话窗里的一页、联网/读文件/语音 | **feature 包**（`kind=feature`） | 能 | 待办、天气、AI 对话、抽签 |
| 加音效/主题/台词/节日内容 | 内容块（`sounds/themes/phrases/seasonal`） | 不能 | **运行期暂未消费，见 §10** |

另外还有「内置 feature」：`petcore/builtin/features/` 里随 core 分发的 7 个包
（`fun.click-talk`、`fun.self-talk`、`tool.quick-launch`、`tool.quiet-mode`、
`tool.links`、`tool.memory-trim`、`tool.network-info`）。它们不通过导入安装、不在扩展管理里启停，
**写第三方扩展时不要照它们的位置放**。

### 0.1 今天明确做不到的事

- **没有窗口端口**：core 不提供「给我一个受管窗口」。要独立窗口就自己建 `QDialog`/`QMainWindow`，
  置顶、跟随、多宠归属、关闭清理全自己写（官方样板 `fun.divination` 就是这么做的）。
- **没有沙箱**：`capabilities` 只是声明，不是权限闸门；mod 与桌宠同进程、同解释器。
- **没有 worker 执行**：`execution: "worker"` 在 manifest 里合法，但启动时会被跳过并记
  `worker feature is not supported yet`；今天只支持 `host-only`。
- **没有模块热卸载**：`重新加载扩展` 会停 runtime、清包目录下的 `sys.modules`、重新导入；
  但已经开着的对话窗、已经建好的设置页不会自动换新（见 §7.3）。
- **没有依赖解析**：`requires` 会被校验格式，但不会自动安装或检查依赖是否真的存在。

---

## 1. 东西到底放在哪

```
仓库（开发期）                     运行期（用户机器）
─────────────────────────────    ─────────────────────────────────────────────
examples/petcore-mods/<id>/   →   不是运行位置，只作为样板/安装源
petcore/builtin/features/     →   随 core 打包，不是用户扩展
assets/characters/<id>/videos →   随包只读，不是用户位置

<data_root>/mods/<id>/            ① 用户数据目录：本地导入、市场安装、角色包都落这里
<install>/mods/<id>/              ② 安装器投放：只有冻结包有；带 .trusted 才免开全局开关
<repo>/mods/                      ③ 源码树目录：会被扫描，但 feature 永远 disabled
--mods-dir <dir>                  ④ 额外目录：同上，feature 永远 disabled
```

### 1.1 数据目录默认在哪

| 情况 | 路径 |
|---|---|
| Windows | `%APPDATA%\dsh-pet-core` |
| macOS | `~/Library/Application Support/dsh-pet-core` |
| Linux | `~/.config/dsh-pet-core` |
| `--portable` | 冻结包：`<安装目录>/data`；源码树：`<仓库根>/data` |
| `--instance NAME` | `dsh-pet-core-NAME`（与默认根并列，独立锁） |
| `--root PATH` | 显式指定；优先级最高 |
| `location.json` 引导指针 | 用户在设置里搬过数据/缓存根时，默认根会被指针替换 |

> **常见误会**：源码运行**也**默认用 `%APPDATA%\dsh-pet-core`，不是仓库的 `data/`。
> 仓库里的 `data/config.json` 是某个 `--root data` 运行留下的样本，不是默认值。
> 想隔离测试，显式传 `--root ./tmp/mymod-data`。

### 1.2 扫描顺序与同 id 覆盖

`root/mods` → `install/mods`（仅冻结包）→ `repo/mods` → `--mods-dir`。
按目录名排序扫描；**同 id 后扫描的覆盖前面的**，并写一条 `duplicate mod id ...` 诊断。

### 1.3 feature 包的加载闸门

一个 feature 包要被加载，必须同时满足：

1. 它位于 **user mods 根** 之下：`<data_root>/mods` 或 `<install>/mods`（冻结包）；
2. 且 `allow_feature_mods` 为真，**或者**它位于 `<install>/mods` 且目录里有 `.trusted`。

推论：

- 放在 `<repo>/mods` 或 `--mods-dir` 的 feature **永远 disabled**，只适合放 content（角色包）。
- 用户数据目录里的 `.trusted` **无效**；导入 ZIP/目录时 core 会把包里自带的 `.trusted` 剥掉。
- `mods enable <id>` 只清 `mods-state.json` 里的停用记录，**不打开** `allow_feature_mods`。

打开全局开关的两条路：

- 设置 →「扩展管理」→ 勾选「允许功能扩展」→「重新加载扩展」；
- 启动参数 `--allow-feature-mods`（启动时把该键置真并落盘）。

---

## 2. 最小可运行骨架（feature 包）

```
my.hello/
  mod.json
  host/
    __init__.py          # 只导出 create_host
    factory.py           # 唯一装配点
    runtime.py           # 命令与后台逻辑（不 import PySide6）
    settings_page.py     # 设置页（可以用 QtWidgets）
```

### 2.1 `mod.json`

```json
{
  "schema": 1,
  "kind": "feature",
  "id": "my.hello",
  "name": "Hello",
  "name_zh": "打招呼",
  "version": "1.0.0",
  "api_version": "1",
  "core_requires": ">=0.4.0,<1.0.0",
  "author": "你的名字",
  "execution": "host-only",
  "entrypoint": "host/__init__.py:create_host",
  "capabilities": ["speech.present", "settings.read", "settings.write"]
}
```

字段要点（完整规则见 `PETCORE-MOD-FORMAT.md` §2）：

- `id`：`^[a-z0-9][a-z0-9._-]{0,127}$`，不能是 `con/prn/aux/nul/com1..9/lpt1..9`。
- `version`：必须是 `X.Y.Z`；`api_version` 必须字面等于 `"1"`，否则整包不加载。
- `core_requires`：逗号分隔的 `>=`/`<=`/`>`/`<`/`==` + `X.Y.Z`；不满足整包不加载。
  用到 `chat_view` 就至少写 `>=0.4.0`；只在本机 0.5.0 验证过可以写 `>=0.5.0`。
- `name_zh`：超过 128 字符会被截断并记 warning。
- `capabilities`：只能写 `petcore/contracts/capabilities.py` 里的值，否则校验报错。
  它只是声明，**不产生任何运行期权限检查**。
- 未知顶层字段只产生 warning，不阻断加载。
- `summary` / `summary_zh` 可以写，市场展示用；`requires` 会被解析但**不会**自动装依赖。

### 2.2 `host/__init__.py`

```python
from .factory import create_host

__all__ = ["create_host"]
```

### 2.3 `host/factory.py`

```python
from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from .runtime import HelloRuntime
from .settings_page import HelloPage

OWNER = "my.hello"


def create_host(_context):
    return FeatureDefinition(
        owner=OWNER,
        contributions=(
            Contribution(
                kind="menu",
                id="hello",
                data={
                    "label": "打个招呼",
                    "command": "hello",
                    "order": 60,
                    "icon": "chat",
                },
            ),
            Contribution(
                kind="settings_page",
                id="page",
                data={"title": "打招呼", "order": 700, "icon": "chat"},
            ),
        ),
        runtime_factory=lambda context: HelloRuntime(context),
        settings_factory=lambda host: HelloPage(host),
    )
```

- `owner` 必须等于 `mod.json` 的 `id`。
- `settings_factory` 实际收到的是**设置宿主代理**（可以当 app host 用），不是 `FeatureContext`；
  `host.config` 是 `ScopedConfig`，`host.window` 是当前设置窗绑定的那只宠。
- 一个 mod 只有**一个** `settings_factory`。声明多个 `settings_page` 贡献会多次实例化同一个类，
  一般只声明一个。
- 如果设置页要让 runtime 立刻响应改动，用闭包 `holder` 把 runtime 传进去（官方样板
  `reminder.todo` 的写法），不要在设置页里重新创建 runtime。

`create_host(context)` 的 `context` 字段：

```python
context.owner        # "my.hello"
context.core_version # 例如 "0.5.0"
context.api_version  # "1"
context.ports        # HostPorts（§4）
context.host         # 宿主对象；只读使用，别依赖内部结构
context.log(msg, level="info")   # 带 owner 前缀的日志
context.config       # 保留字段，当前 loader 传 {}；不要拿它当配置源
context.events       # 当前为 None；不要依赖
```

### 2.4 `host/runtime.py`（无 Qt）

```python
import logging

OWNER = "my.hello"
log = logging.getLogger(OWNER)


class HelloRuntime:
    def __init__(self, context):
        self.ports = context.ports
        # 菜单项 data.command 指向这里的键；键名对不上就点不动
        self.commands = {"hello": self.hello}
        self._timer = ""

    def start(self):
        scheduler = self.ports.scheduler
        if scheduler is not None:
            self._timer = scheduler.call_every(3_600_000, self._tick, owner=OWNER)

    def stop(self):
        if self._timer and self.ports.scheduler is not None:
            self.ports.scheduler.cancel(self._timer)
        self._timer = ""

    def hello(self, slot=None):
        presentation = self.ports.presentation
        if presentation is None:
            return
        target = presentation.for_slot(slot) if slot is not None else presentation
        target.say("你好呀", force=True)

    def _tick(self):
        log.info("hourly tick")
```

红线：

- **运行时代码不 import PySide6**，也不 import `petcore.net` / `petcore.api` / `petcore.account` /
  `petcore.platform`；这些由架构测试守。
- 需要回 GUI 线程用 `ports.scheduler.call_later(0, cb)`。它是唯一明确允许从任意线程调用的端口。
- `scheduler.call_every/later` 的 `owner=` 是 core 实现（`QtScheduler`）的扩展参数，
  协议只写了两个位置参数；写官方样板这样用没问题，换宿主实现前先确认。
- `Runtime.close()` **永远不会被 core 调用**；清理写在 `stop()` 里。

### 2.5 `host/settings_page.py`

```python
from PySide6.QtWidgets import QLineEdit

from petcore.ui.settings.base import SettingsPage

KEY = "my_hello_text"
DEFAULT = "你好呀"


class HelloPage(SettingsPage):
    title = "打招呼"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._initial = str(host.config.get(KEY, DEFAULT))

        self.group_heading("打招呼")
        card = self.section("招呼语", level=3)
        self.text = QLineEdit(self)
        self.text.setPlaceholderText("点菜单时说的话")
        self.text.setText(self._initial)
        card.add_row(
            "文字",
            self.text,
            "会显示在这只桌宠的气泡里。",
            expand=True,
            search_keys=("招呼", "台词"),
        )
        self.add_note("改动即时生效；点「取消」会恢复到打开设置前的值。")
        self.text.textChanged.connect(self._changed)

    def _changed(self, value):
        self.host.config.set(KEY, str(value))
        self.changed.emit()

    def dirty(self):
        return str(self.host.config.get(KEY, DEFAULT)) != self._initial

    def confirm_save(self):
        self._initial = str(self.host.config.get(KEY, DEFAULT))

    def discard_changes(self):
        self.host.config.set(KEY, self._initial)
        self.text.blockSignals(True)
        self.text.setText(self._initial)
        self.text.blockSignals(False)
        self.changed.emit()

    def keywords(self):
        return ["打招呼", "招呼", "hello"]
```

设置页契约（`petcore/ui/settings/base.py`）：

| 成员 | 作用 |
|---|---|
| `title`（类属性） | 导航标题兜底；`settings_page.data["title"]` 优先 |
| `changed` 信号 | 实时预览；**页面自己 emit** |
| `dirty()` | 决定保存/取消时是否处理这一页；默认 `False` |
| `confirm_save()` | 保存成功后的基线更新；返回 `False` 表示保存失败 |
| `discard_changes()` | 取消时恢复**打开页面时**的值；**记得把控件也刷回去** |
| `dispose()` / `reflow()` | 页面卸载 / 字号变化后重排 |
| `keywords()` | 设置搜索关键词 |
| `group_heading / section / add_note / add_row / add_buttons` | 统一布局 |
| `make_button / make_checkbox / make_spin / make_slider / value_field` | 统一控件工厂 |

**不继承 `SettingsPage` 也能跑**，但会被包成只读占位页并提示「改动不会随设置保存」。
设置页里的 `host.config` 是 `ScopedConfig`：已知的全局键去全局配置，已知的每宠键去该宠配置，
**未登记键（扩展偏好）统一去全局配置**——这是为了让共享的 feature runtime 能看到设置页写下的值。

---

## 3. 接线点：今天真正生效的只有三个

`CONTRIBUTION_KINDS` 有 10 个取值，但真正有消费点的只有 **`menu`、`settings_page`、`chat_view`**。

| kind | 真实行为 |
|---|---|
| `menu` | `FeatureManager.menu_contributions()` → `MenuModel`；`runtime.commands` 执行 |
| `settings_page` | `FeatureManager.settings_pages()` → `ExtensionsPage` 页内导航 |
| `chat_view` | `FeatureManager.chat_views()` + `view_factories` → 对话窗一页 |
| `command` | **没有消费点**；命令表是 `runtime.commands` |
| `hotkey` | **没有消费点**；全局热键是 core 的 Win32 注册 |
| `card` | **没有消费点**；灵动岛是 core 窗口 |
| `install_hook` | **没有消费点** |
| `phrases / sound / theme / seasonal` | loader 会读成 `ContentEntry`，但 `content/collect.py` 只消费 `character` |

### 3.1 菜单

```python
Contribution(kind="menu", id="hello", data={
    "label": "打个招呼",
    "command": "hello",     # 缺省等于 contribution id
    "order": 60,            # 只是排序，惯例 0–1000，默认 500；没有硬钳制
    "icon": "chat",         # core 图标名；拼错回落成圆点，不报错
    "checked": lambda: bool(...),   # 可选：勾选状态回调
})
```

- 菜单项 id 在 core 内是 `<owner>:<contribution id>`。
- 默认出现在右键菜单「扩展 → `<manifest name_zh/name>` → 你的项」；用户可以在
  设置 →「菜单」里拖到根/其它子菜单、改名、换图标、隐藏。
- 点击时 core 取 `data.command`（缺省等于贡献 id），再查 `runtime.commands`；缺失只记 warning。
- 命令**在 GUI 线程调用**。只有显式声明 `slot` 形参才会收到发起菜单的桌宠槽位：
  `def hello(self, slot=None): ...`。
  `*args` / `**kwargs` 不会收到 `slot`——`_invoke_feature_command` 检查的是签名里有没有 `slot`。
- 命令抛异常：core 记日志并继续（菜单点击不会弹错误气泡；热键路径会提示「扩展执行失败」）。

### 3.2 设置页

```python
Contribution(kind="settings_page", id="page", data={
    "title": "打招呼", "order": 700, "icon": "chat",
})
```

- 第三方扩展的设置页统一进「扩展管理」的页内导航，**不新增侧边栏页**。
- `data` 真正生效的键：`title`、`order`、`icon`、`host_page`。
- `host_page`（`interaction`/`menu`/`about`）只有 `CORE_FEATURE_IDS` 里的内置特性生效；
  第三方写了仍进扩展管理。
- 保存/取消/释放由 `ExtensionsPage` 委托页面；取消时设置对话框会恢复整份配置快照，
  但**页面控件**只有在你的 `discard_changes()` 里刷新才会回去。
- 设置搜索自动索引页面内所有可见文字、按钮、下拉项、`QTreeWidget` 项；没有可见文字的
  行用 `register_search_terms(widget, "关键词")` 或 `add_row(..., search_keys=(...))` 登记。
- 有菜单贡献的**内置/示例**包必须有设置页（测试强制）；第三方扩展这是约定，不强制。

### 3.3 对话窗里的一页（`chat_view`，0.4.0+）

```python
# factory.py
def build_view():
    from .my_view import MyView      # Qt 只在真正切过去时导入
    return MyView()

Contribution(kind="chat_view", id="mine", data={
    "label": "我的面板",   # 必填
    "icon": "globe",       # 对话窗图标名
    "order": 100,
})
FeatureDefinition(..., view_factories={"mine": build_view})
```

`view_factories` 的键是 **contribution id**；工厂**不接受参数**，要拿端口就在 `create_host`
里闭包捕获。

精确生命周期（按这个写，不要猜）：

| 时机 | core 做什么 |
|---|---|
| 第一次切到该页 | GUI 线程调 `factory()`（每扇窗口一次）→ `_apply_view_dark(widget)`，即 `set_dark(当前值)` → `activate()` |
| 切走 | `deactivate()`；切回来再 `activate()` |
| 关闭对话窗 | 先 `deactivate()`，再只对**当前显示的那一页** `shutdown()`；窗口只是隐藏，控件不销毁 |
| 重新打开对话窗 | `showEvent` 对当前视图再调一次 `activate()` |
| 同一只宠的同一 owner | 只有一扇窗口；`(owner, pet_slot)` 是窗口键 |
| 新装/修改了 `chat_view` | **必须重启应用**：窗口的视图清单在创建时固定，关闭只是隐藏，重载扩展不会重建它 |

其他保证：

- 四个方法全部可选，core 用 `getattr` 探测并吞掉异常；工厂抛异常或返回非 `QWidget`
  → 显示错误占位，对话窗其余功能不受影响。
- `set_dark` 在工厂之后一定会先被调用一次；不要假设它只在用户点月亮时来。
- `deactivate()` 可能没有配对的 `activate()`，实现要幂等。
- 视图被放进 `QFrame#chat-view-host`，左/右/下内缩 12px（原生子窗口不吃圆角裁剪）。
- 视图拿不到 core 的窗口对象；不要 reparent 出去；不要自己建标题栏/关闭按钮。
- 视图不做默认页：每次开对话窗都停在 AI 对话。
- core 不替视图上色：视图里的控件用系统 palette，要跟深色一致就在 `set_dark(bool)` 里自己设。
- 菜单一键切过去：`ok = self.ports.chat.show_view(OWNER, "mine", pet_slot=slot)`。
  `show_view` 会**复用该宠最近使用的那扇对话窗**并切页；该宠还没有窗口时才按调用方 owner 新建。
  在 GUI 线程调用时，视图 id 没注册（扩展没装/没声明/工厂缺失）返回 `False`，**不开任何窗口**。
  非 GUI 线程调用也安全（内部排队），但返回值只表示「请求已提交」，不做存在性判断。

---

## 4. 端口清单与线程

`HostPorts` 声明 27 个字段，`build_ports()` 实际注入 **22** 个：

| 端口 | 用途 / 关键方法 | 线程 |
|---|---|---|
| `presentation` | `say` / `say_sequence` / `show_image` / `notify` / `play_animation` / `play_event` / `begin_scene` / `for_slot` | GUI 优先；实现会从后台线程排队 |
| `scheduler` | `call_later` / `call_every` / `cancel` / `cancel_owner` | **任意线程**，回调回 GUI |
| `audio` | `play(path, volume)` / `set_volume` | GUI |
| `storage` | `data_dir(owner)` / `config_dir` / `data_root` | 阻塞（会建目录） |
| `credentials` | `get/set/delete(owner, key)` | 阻塞 |
| `resources` | `bundle_path(*parts)` 随包只读资源 | 只读 |
| `config` | `get(key, default)`；`set` 只给菜单勾选/命令切换 | `set` 立即落盘 |
| `pets` | `pets()/primary()/active()`；句柄有 `body_rect/move_by/set_velocity/say/play_animation/animations/config` | GUI |
| `interaction` | `on_click` / `on_drop` / `on_tick(16ms)` / `cancel_owner` | GUI（回调在 GUI） |
| `quiet` | `quiet_active` / `set_quiet` / `subscribe` | GUI |
| `reminders` | `deliver(owner, slot, key, target, title, text, guard, on_click)` | GUI |
| `http` | `request(HttpRequest)` / `get_json(...)` | **阻塞**，自己开线程 |
| `api` | `services()` / `stream_chat(...)`；SSE/代理/错误分类在 core | 回调在请求线程 |
| `chat` | `open/append/token/done/error/cancel/record/show_view/on_submit/register_channel` | 可后台调用，排队到 GUI |
| `quick_chat` | 桌宠旁的快速对话气泡 | 可后台调用 |
| `screen` | `capture/capture_region/foreground_window/sample_foreground/capture_foreground/shrink_for_vision` | GUI |
| `screen_ask` | 截图动作条与问答窗：`on_action/on_ask` | 回调在**后台线程** |
| `speech` | `providers/preflight/synthesize/speak/stop/set_secret/register_yield` | `synthesize` 阻塞；`speak` 异步 |
| `memory` | `remember/recall/render_prompt/stats/clear/export` | GUI |
| `webhook` | `start(owner, port, path, token, handler)`；只绑 127.0.0.1 | handler 在服务线程 |
| `system` | `screens/platform_name/idle_seconds` | GUI |
| `worker` | `start(owner, argv, env)` | 启动子进程（异步） |

**声明了但运行期恒为 `None`**：`commands`、`menu`、`windows`、`input`、`capabilities`。
不要照着 dataclass 写代码；先 `if ports.http is not None:`。

### 4.1 线程铁律

1. Qt 控件、窗口、气泡、菜单、托盘只在 GUI 线程碰。
2. 回 GUI 用 `ports.scheduler.call_later(0, cb)`；这是唯一明确跨线程安全的端口。
3. `http.request/get_json`、`speech.synthesize`、`config.set`、`storage.data_dir` 都是**同步阻塞**，
   放自己的后台线程。
4. `screen_ask.on_ask/on_action` 的回调本来就在后台线程：只碰纯数据，结果用 scheduler 送回去。
5. `stop()` 里先 `scheduler.cancel_owner(OWNER)` / 取消自己的句柄，再清状态；core 还会替你撤
   `speech` / `reminders` / `interaction` 的 owner 订阅。
6. `Runtime.close()` 不会被调用，清理写在 `stop()`。

---

## 5. 配置、数据与资源

### 5.1 偏好：`config`，不是状态数据库

```python
ports.config.get("my_hello_text", "你好呀")     # 读
ports.config.set("my_hello_text", "早上好")     # 只给菜单勾选/命令切换这类一次性动作
```

- 第三方键自动落 `config.json` 的 `extra` 区块，**全局共享**（feature runtime 是共享的）。
- 键名 `^[a-z0-9][a-z0-9._-]{0,127}$`；单值序列化后 ≤4KB，整块 ≤64KB。
- 命中密钥样式的值（`sk-…`、`Bearer …`、32 位以上十六进制）会被拒绝。
- `config.set` 会立即落盘；**不要把运行时状态、计时、缓存写进 config**。
- 要按宠隔离，不要靠 config：用 `storage.data_dir(OWNER)/slot-<slot>.json`，或按 slot 分键存数据。
- 密钥只进 `ports.credentials`，不要写 config。

### 5.2 业务数据：`storage.data_dir(owner)`

```python
from pathlib import Path
import json, os, tempfile

def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
```

- 落点：`<data_root>/feature-data/<owner>/`。
- 建议带 `{"schema_version": 1, ...}`；升级时自己迁移，损坏文件不要静默覆盖。
- **卸载不会删这个目录**；在设置页给用户一个明确的「清除数据」入口。

### 5.3 包内资源与 Qt 模块

- 包内文件用 `Path(__file__).resolve().parent.parent / "assets"` 定位，不要假设当前工作目录。
- 随包只读资源（模型/素材）用 `ports.resources.bundle_path(...)`。
- 冻结包里**有**：QtCore / QtGui / QtWidgets / QtNetwork / QtMultimedia / QtSvg，
  以及 `PySide6.QtWebView`（Edge WebView2 后端）。
- 冻结包里**没有** `PySide6.QtWebEngine*`（整套 Chromium 没打）。
- 用 `importlib.util.find_spec("PySide6.QtWebView")` 自检；不可用就不要声明依赖它的入口。
- `QWebView` 是原生子窗口，不吃 Qt 的圆角/浮层；放布局时留内缩。

---

## 6. 自建窗口与小游戏类扩展

独立窗口不是端口，但事实上能造。官方先例：`examples/petcore-mods/fun.divination/host/runtime.py`
在命令里惰性导入 `QDialog`、`show()`/`raise_()`；`tool.ai-browser` 则用 `chat_view` 占对话窗一页。

### 6.1 三条 UI 路线

| 路线 | 适合 | 代价 |
|---|---|---|
| 自建 `QDialog/QMainWindow` | 棋盘、卡牌、实时玩法、带画布的游戏 | 置顶/跟随/多宠归属/关闭清理/几何持久化全自己写 |
| `chat_view` | 助手式面板、网页、状态页、轻量互动 | 不是独立窗口；只在对话窗里；改代码要重启 |
| 设置页 | 规则、难度、音量、存档管理 | 内容在 `QScrollArea`，默认阅读宽 860；别放棋盘 |

### 6.2 自建窗口的生命周期清单

- Qt 代码单独成模块（`game_window.py`），只在 GUI 线程的命令里惰性导入。
- runtime 持住窗口引用；关闭时清引用，避免第二次点菜单开出两扇窗。
- `show()` 后 `raise_()` / `activateWindow()`；没有引用会被 Python 回收或压到别的窗口后面。
- `closeEvent` / `hideEvent` 停定时器；`runtime.stop()` 再兜底关窗。
- 多宠：命令收到 `slot`；决定窗口是「全局一扇」还是「每宠一扇」，并存到对应 key。
- 几何/置顶/跟随：core 不替你管；要跟随桌宠得自己监听位置（没有公开的移动信号端口）。
- 别在 `closeEvent` 里做长 IO；先停表，再异步保存。

### 6.3 游戏循环

- 回合制/棋盘：不需要帧循环；按钮、信号、`QTimer.singleShot` 足够。
- 实时玩法：自己窗口里的 `QTimer(33ms)`，或 `ports.interaction.on_tick(OWNER, cb, 16)`。
  两者都在 GUI 线程；只做轻量状态推进与 `update()`。
- **不要用 `on_tick` 驱动一个看不见的游戏**：窗口隐藏/关闭就停表；`stop()` 里取消。
- 解码、读盘、网络、语音合成都下后台线程；结果用 `scheduler.call_later` 回 GUI。
- 与桌宠同进程：死循环会卡住 16ms 待机节拍，原生崩溃会带走整个应用。

---

## 7. 本地开发闭环

### 7.1 命令

```bash
# 0) 静态校验（目录或 zip 都行）
.venv/Scripts/python -m petcore mods validate ./my.hello

# 1) 装进用户数据目录（<data_root>/mods/my.hello）
.venv/Scripts/python -m petcore mods install ./my.hello
# 覆盖同 id：--force

# 2) 打开功能扩展开关（二选一）
.venv/Scripts/python -m petcore --allow-feature-mods
# 或：设置 → 扩展管理 → 勾选「允许功能扩展」

# 3) 启动 / 在扩展管理点「重新加载扩展」
.venv/Scripts/python -m petcore

# 4) 排错
.venv/Scripts/python -m petcore mods list
.venv/Scripts/python -m petcore mods doctor
# 日志：<data_root>/logs/petcore.log，搜 feature / mod diagnostic

# 5) 启停 / 卸载
.venv/Scripts/python -m petcore mods disable my.hello
.venv/Scripts/python -m petcore mods enable my.hello
.venv/Scripts/python -m petcore mods remove my.hello     # 注意是 remove，不是 uninstall
```

`mods` 子命令没有 `uninstall`；`remove` 只处理 `<data_root>/mods/<id>`。
安装器投放的包（`<install>/mods`）要在设置 →「扩展管理」里卸载。

### 7.2 安装包形态的机器

安装包里的 `dsh-pet-core.exe` 是窗口化程序：`mods` 子命令仍能跑，但控制台输出会被丢弃。
这类用户/作者走 GUI：

1. 设置 →「扩展管理」→「导入 ZIP」或「导入目录」；
2. 同一页勾选「允许功能扩展」；
3. 点「重新加载扩展」；
4. 出问题看同页「诊断」区，或 `<data_root>/logs/petcore.log`。

### 7.3 改代码后到底要重启什么

| 你改了什么 | 生效方式 |
|---|---|
| `runtime.py` / 命令 / 菜单贡献 | 「重新加载扩展」即可（会清包目录下的 `sys.modules`） |
| `settings_page.py` | 关闭设置窗再打开（窗口在关闭时销毁，下次重建页面；必要时重启） |
| `chat_view` / 视图工厂 | **重启应用**（对话窗的视图清单在创建时固定，关闭只是隐藏） |
| `mod.json` | 重新加载；`id/kind/api_version/entrypoint` 变了要重装 |
| 角色/媒体包 | 重新加载或重启；见 `PETCORE-CHARACTER-PACKS.md` |

### 7.4 排错对照表

| 现象 / 报错 | 原因 | 修法 |
|---|---|---|
| 扩展管理里看不到包 | 没装到 `<data_root>/mods`；manifest 校验失败 | `mods validate`；`mods list` 看 `!` 诊断 |
| 包在那儿但「已停用」/ 菜单没项 | feature 在 `<repo>/mods` 或 `--mods-dir`；没开 `allow_feature_mods`；被 `mods disable` | 装到数据目录；开开关；`mods enable` |
| `feature mods disabled` | 同上 | 同上 |
| `worker feature is not supported yet` | `execution: "worker"` | 改 `host-only`；`mods list` 仍可能显示 enabled |
| `entrypoint not found` | 路径写错 | 只 warning；运行时 import 会失败并留诊断 |
| `create_host must return FeatureDefinition` | 工厂返回了别的对象 | 返回 `FeatureDefinition(...)` |
| `api_version must be '1'` | 写了别的版本 | 改成 `"1"` |
| `core_requires not satisfied` | 范围与 core 版本不符 | 放宽范围 |
| `invalid id` | 大写/空格/保留名 | 改成合法 id |
| `path escapes pack` | 资源/入口跑出包外 | 改包内相对路径 |
| `executable file in content pack` | content 包混进了 `.py/.dll/.exe…` | content 只放媒体与 JSON |
| `duplicate mod id ... overridden by ...` | 同 id 在多个目录 | 删旧副本 |
| `already installed ... (use --force to replace)` | 同 id 已存在 | `mods remove` 或 `--force` |
| `multiple mod.json roots found in archive` | zip 有多层包 | 只留一层 wrapper |
| 点菜单没反应 | `data.command` 与 `runtime.commands` 键不一致 | 对齐两个名字 |
| 设置搜不到 | 行里没有可见文字也没登记搜索词 | `search_keys=` / `register_search_terms` |
| 设置取消后控件没回滚 | 没实现 `discard_changes()` | 回写配置 + 刷新控件 |
| `chat_view` 标题栏没有按钮 | `label` 空 / 没给 `view_factories` / 装了但没重启 | 补齐并重启应用 |
| 切走以后还在跑 | 视图没实现 `deactivate()` | 停加载、停定时器 |
| 自造窗口不出现/被压住 | 没持引用 / 没 `raise_()` | 持引用，显示后抬升 |
| 设置页里塞 webview 变成一条缝 | 设置页在 `QScrollArea`，阅读宽 860 | 浏览器/大画布自造窗口 |
| 打包后功能消失 | 用了宿主没打包的 Qt 模块 | `find_spec` 自检 + 降级 |

---

## 8. 打包与分发

```text
my.hello.zip
└── my.hello/           ← 只允许一层 wrapper 目录
    ├── mod.json
    └── host/…
```

- ZIP 安装会拒绝绝对路径、盘符、`..`、NTFS 保留名、大小写重复、符号链接条目。
- 安装成功后写 `<mod>/install.json`（逐文件 sha256）；卸载按它逐文件删，未登记文件保留。
  没有 `install.json` 的包（安装器投放）卸载时整目录删。
- 本地导入会**剥掉**包里自带的 `.trusted`；`.trusted` 只在 `<install>/mods` 下有效。
- 市场安装的包同样落 `<data_root>/mods/<id>`，仍受 `allow_feature_mods` 约束；
  索引/校验/回滚见 [`PETCORE-MARKET.md`](PETCORE-MARKET.md)。
- 没有运行前签名校验：安装只记录 sha256 用于卸载，不用于加载前验证。
- 第三方扩展不要改 `packaging/petcore.iss`（那是官方随附包的勾选项）。

---

## 9. 角色包（content）

目录、manifest、动画分类、`body_box`、`move_strides` 的权威说明在
[`PETCORE-CHARACTER-PACKS.md`](PETCORE-CHARACTER-PACKS.md)。这里只放最短路径：

```text
源目录/
  manifest.json      {"name": "...", "body_box": [x1, y1, x2, y2], "author": "..."}
  videos/            idle/ move/ click/ events/<组>/ …
```

```bash
.venv/Scripts/python -m petcore pack-character ./my-character --out ./dist/character.my --id my
.venv/Scripts/python -m petcore mods install ./dist/character.my
```

- 产物是**目录**，不是 zip；要分发就自己压包。
- 角色包**不能**含 `.py/.pyc/.dll/.so/.dylib/.exe/.js/.bat/.cmd/.ps1`。
- `body_box` 按 alpha≥128 量；不写就退化成整张画布，贴边/落位会偏。
- 文件夹名映射：`idle/`、`turn/`、`move/`、`click/`、`drag/`、`sleep/`、`work/`、
  `events/<组>/`；其余按 `DEFAULT/SINGLE` 处理；媒体只认 `.webm` / `.gif`。
- 没有热加载：装完要「重新加载扩展」或重启。

---

## 10. 内容块：现在别用

`mod.json` 允许声明 `sounds` / `themes` / `phrases` / `seasonal`，loader 也会把它们变成
`ContentEntry`；但 `petcore/content/collect.py` **只消费 `kind == "character"`**。
今天写这四类等于写了一个不会被看见的包。等接线落地后再更新本节。

---

## 11. 红线与提交前 checklist

| 规则 | 谁守 |
|---|---|
| 扩展不许 `import petcore.net / petcore.api / petcore.account / petcore.platform` | `tests/test_petcore_architecture.py` |
| 扩展不许 `import petcore.ui.*`（`settings_page.py` 或 `petcore.ui.settings.*` 除外） | 同上 |
| 有菜单贡献的内置/示例包必须有设置页 | `tests/test_petcore_builtin_mods.py` / `test_petcore_example_mods.py` |
| `mod.json` 字段/路径/可执行文件扫描 | `petcore/mods/manifest.py`（`mods validate`） |
| core 内置菜单项必须有设置落点 | `tests/test_petcore_settings_coverage.py` |
| engine 无 Qt、contracts 只用标准库 | `tests/test_petcore_architecture.py` |

**没有自动化守卫、只能靠约定**的两条：

- 运行时代码不 `import PySide6`（架构测试只查 `petcore.net/api/account/platform` 与 `petcore.ui`；
  直接 import PySide6 目前只有 `tests/test_petcore_ai_browser_mod.py` 对它自己的模块做了 grep）。
- `capabilities` 不做运行期强制。

提交前：

- [ ] `mods validate` 干净；`mods install` 后 `mods list` 是 enabled 且无 `!`。
- [ ] 菜单能点、设置页能存能取消；`dirty()` 改过为真、保存后归零。
- [ ] 每个 `menu` 的 `command` 都在 `runtime.commands`；每个 `chat_view` 都有工厂。
- [ ] 运行时无 `PySide6`、无 `petcore.net/api/account/platform`、无 `petcore.ui.*`（设置页除外）。
- [ ] 所有定时器走 `scheduler` 或自己的窗口；`stop()` 能全部取消。
- [ ] 数据在 `storage.data_dir(owner)`，原子写；config 只放偏好。
- [ ] 日志里没有密钥、Cookie、完整 URL/查询串。
- [ ] 自造窗口：持引用、抬升、关闭停表、`stop()` 关窗。

---

## 12. 相关文档

| 你要查什么 | 看哪份 |
|---|---|
| `mod.json` 每个字段、每条报错、能力模型 | [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md) |
| 给 AI 的自包含说明 + 可粘贴提示词 | [`PETCORE-MOD-AI-GUIDE.md`](PETCORE-MOD-AI-GUIDE.md) |
| 角色包规格、动画分类、帧序列采纳 | [`PETCORE-CHARACTER-PACKS.md`](PETCORE-CHARACTER-PACKS.md) |
| 12 个可抄的官方样板 | [`../examples/petcore-mods/README.md`](../examples/petcore-mods/README.md) |
| 市场索引、下载、回滚 | [`PETCORE-MARKET.md`](PETCORE-MARKET.md) |
| core 内部分层与启动路径 | [`PETCORE-ARCHITECTURE.md`](PETCORE-ARCHITECTURE.md) |
| 一张能点开的全局图 | [`PETCORE-ARCHITECTURE-MAP.html`](PETCORE-ARCHITECTURE-MAP.html) |
