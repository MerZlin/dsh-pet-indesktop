# 给 AI 的 petcore 扩展开发说明（`api_version = 1`）

> 这份文件是**自包含**的：把整篇丢给任何 AI（或人），它就能写出能装、能跑的 petcore
> 扩展，不需要先读仓库。字段级细节在 [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md)，
> 图文流程在 [`PETCORE-MOD-DEVELOPMENT.md`](PETCORE-MOD-DEVELOPMENT.md)。
>
> 适用版本：core **0.4.0+**（`chat_view` 从 0.4.0 开始有）。
> 写扩展时请把 `mod.json` 的 `core_requires` 写成 `">=0.4.0,<1.0.0"`。

---

## 0. 先做三个判断

1. **只是加素材**（角色动画、音效、对话台词、皮肤）→ 做 **content 包**，没有 Python。
2. **要加功能**（菜单项、设置页、对话窗里的一页、调 API/读文件）→ 做 **feature 包**，
   入口是一个 Python 函数，返回 `FeatureDefinition`。
3. 你要是想做「独立窗口的小工具」也可以，但**不要**自己再写一个聊天窗——
   想占对话窗的一页用 `chat_view`（见 §4.3）。

**不要假设**：`HostPorts` 里声明了但没注入的端口（§5 有清单）、安装包里没有的 Qt 模块（§6）、
不存在的贡献类型。这三件是 AI 写 petcore 扩展最常犯的错。

---

## 1. 包放哪、怎么装

| 位置 | 谁能加载 | 说明 |
|---|---|---|
| `<数据目录>/mods/<id>/` | feature + content | 用户数据目录；Windows 下是 `%APPDATA%\dsh-pet-core\mods` |
| `<安装目录>/mods/<id>/` | feature + content（冻结包） | 安装器投放的官方包；同目录需要 `.trusted` 才免开全局开关 |
| `examples/petcore-mods/<id>/` | 源码树开发用 | 用 CLI 装进数据目录后再测 |

安装/重装：

```bash
python -m petcore mods install examples/petcore-mods/<id> --force   # 目录或 .zip 都行
```

feature 包还需要全局开关 **「允许功能扩展」**（设置 → 扩展管理；CLI 没有对应子命令），
或者包里自带 `.trusted` 且位于上面两个根目录之一。装完点「重新加载扩展」。

日志：`<数据目录>/logs/petcore.log`，搜 `feature`、`mod diagnostic`。
数据目录默认 `%APPDATA%\dsh-pet-core`（源码运行时是仓库根的 `data/` 或 `--root` 指定处）。

---

## 2. 最小骨架（feature 包）

```
my.hello/
  mod.json
  host/
    __init__.py        # from .factory import create_host
    factory.py         # 唯一装配点，返回 FeatureDefinition
    runtime.py         # 命令与后台逻辑（**不要 import PySide6**）
    settings_page.py   # 可选：设置页（可以用 QtWidgets）
```

`mod.json`：

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
  "author": "you",
  "execution": "host-only",
  "entrypoint": "host/__init__.py:create_host",
  "capabilities": ["speech.present", "settings.read", "settings.write"]
}
```

`host/factory.py`：

```python
from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition

from .runtime import HelloRuntime

OWNER = "my.hello"


def create_host(context):
    holder = {}

    def runtime_factory(ctx):
        holder["runtime"] = HelloRuntime(ctx)
        return holder["runtime"]

    return FeatureDefinition(
        owner=OWNER,
        contributions=(
            Contribution(
                kind="menu",
                id="hello",
                data={"label": "打个招呼", "command": "hello", "order": 60, "icon": "chat"},
            ),
        ),
        runtime_factory=runtime_factory,
    )
```

`host/runtime.py`：

```python
class HelloRuntime:
    def __init__(self, context):
        self.ports = context.ports
        self.commands = {"hello": self.hello}   # id 必须和 menu contribution 的 command 一致

    def start(self): ...
    def stop(self): ...

    def hello(self, slot=None):
        self.ports.presentation.say("你好呀", force=True)
```

`capabilities` 只是**声明 + 用户授权**，不是沙箱；写进清单是为了让用户知道你要什么。

允许的取值只有这些（写别的会让清单校验报错）：
`character.read`、`animation.request`、`speech.present`、`sound.play`、
`notification.present`、`settings.read`、`settings.write`、`network.request`、
`files.read`、`filesystem.user_data`、`screenshot.capture`、`process.spawn`。

**`create_host(context)` 的 `context` 有这些字段**（视图工厂要用的都在这里）：

```python
context.owner        # "my.hello"
context.core_version # 例如 "0.4.0"（可用来做特性开关）
context.api_version  # "1"
context.ports        # HostPorts：presentation/scheduler/config/storage/chat/...（§5）
context.config       # 包自己的配置快照 dict（不是宿主配置）
context.host         # 宿主对象（只读用，别依赖内部结构）
context.log(msg)     # 记一条带 owner 前缀的日志
```

`view_factories` 里的可调用对象**不接受参数**，所以要拿端口就在 `create_host` 里闭包捕获：

```python
def create_host(context):
    ports = context.ports
    return FeatureDefinition(
        owner=OWNER,
        contributions=(...),
        view_factories={"mine": lambda: MyView(ports)},   # 闭包传进去
    )
```

---

## 3. 可用的贡献类型

`CONTRIBUTION_KINDS` 的全部取值（写别的会直接 `ValueError`）：

| kind | 用途 | `data` 关键字段 |
|---|---|---|
| `menu` | 右键菜单项 | `label`、`command`、`order`、`icon`、`section` |
| `settings_page` | 设置页（Core 托管、可搜索） | `title`、`order`、`icon`、`host_page` |
| `chat_view` | **对话窗里的一页**（§4.3） | `label`、`icon`、`order` |
| `phrases` / `sound` / `theme` / `seasonal` | 内容贡献 | 见 `mod.json` 的 `phrases`/`sounds`/`themes` 块 |
| `command` / `hotkey` / `card` / `install_hook` | 已声明，**目前没有消费者** | — |

内容贡献更推荐写在 `mod.json` 的对应块里（`sounds`/`themes`/`phrases`/`seasonal`），
由 loader 统一抽取。

---

## 4. 三个真正的接线点

### 4.1 菜单

```python
Contribution(kind="menu", id="open", data={
    "label": "打开面板", "command": "open", "order": 70, "icon": "chat",
})
```

- 动作 id 在 Core 内是 `<owner>:<contribution id>`；没有手工布局过的项自动进「扩展」子菜单。
- `command` 必须存在于 runtime 的 `commands` 字典；缺失只记诊断，不炸菜单。
- 命令回调在 **GUI 线程**调用，参数是 `slot`（发起菜单的那只桌宠的槽位），
  写法：`def open(self, slot=None)`。Core 会按签名决定传不传。

### 4.2 设置页

```python
from petcore.ui.settings.base import SettingsPage, make_button, make_checkbox
from petcore.ui.settings.base import register_search_terms

class MyPage(SettingsPage):
    title = "打招呼"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._initial = {"my_hello_text": host.config.get("my_hello_text")}
        card = self.section("招呼语", level=3)
        self.text = QLineEdit(self)
        card.add_row("招呼文字", self.text, "点菜单时说的话。", search_keys=("招呼", "台词"))
        self.text.textChanged.connect(self._changed)

    def _changed(self, *_):
        self.host.config.set("my_hello_text", self.text.text())
        self.changed.emit()

    def dirty(self): return self.host.config.get("my_hello_text") != self._initial["my_hello_text"]
    def confirm_save(self): self._initial["my_hello_text"] = self.host.config.get("my_hello_text")
    def discard_changes(self): self.host.config.set("my_hello_text", self._initial["my_hello_text"])

    def keywords(self): return ["打招呼", "hello"]
```

- 声明 `Contribution(kind="settings_page", id="page", data={"title": "打招呼", "order": 610})`
  并且 `FeatureDefinition.settings_factory=lambda host: MyPage(host)`。
- **设置搜索是自动的**：页标题、行的可见文字、按钮文字、下拉项都会进索引。
  没有可见文字的行（纯图标按钮、动态路径）必须 `register_search_terms(widget, "关键词")`
  或 `add_row(..., search_keys=(...))`，否则用户搜不到。
- `host_page` 可以把你的设置内容内嵌到 Core 设置页（`interaction` / `menu` / `about`）：
  `Contribution(kind="settings_page", id="page", data={"title": "打招呼", "host_page": "interaction"})`，
  只有 core 内置扩展（`CORE_FEATURE_IDS`）会生效，第三方包写了仍进「扩展管理」。
- **导航标题取 contribution 的 `data["title"]`**，类属性 `title` 只是后备；两处写一样的即可。
- `SettingsPage` 上常用的东西：
  `section(标题, level=3)` 造卡片、`card.add_row(标签, 控件, 说明, expand=True, search_keys=(...))`
  加一行（控件可以是 `QLineEdit`/`QPlainTextEdit`/`QComboBox`/自定义 QWidget）、
  `card.add_buttons([...])`、`self.add_note("…")`、`self.changed` 信号、`self.host.config`。
- **写盘语义是「即时写」**：用户改一下就 `host.config.set(...)`（惯例，见示例）。
  `dirty()/confirm_save()/discard_changes()` 负责「取消」时回到**打开页面时**的值；
  `discard_changes` 记得把控件内容也刷回去（示例只回写配置是简化写法）。

### 4.3 对话窗里加一页（`chat_view`）

```python
# factory.py
def build_view():
    from .my_view import MyView     # Qt 只在真正切过去时导入
    return MyView()

Contributions += (Contribution(
    kind="chat_view", id="mine",
    data={"label": "我的面板", "icon": "globe", "order": 100},
),)
FeatureDefinition(..., view_factories={"mine": build_view})
```

```python
# my_view.py
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

class MyView(QWidget):
    def activate(self):        # 切到这一页：恢复轮询/继续加载
        ...
    def deactivate(self):      # 切走：停加载、停定时器（别在后台烧 CPU）
        ...
    def set_dark(self, dark):  # 对话窗深色开关变化
        ...
    def shutdown(self):        # 对话窗关闭：释放页面
        ...
```

Core 的保证与**精确时序**（按这个写，不要自己猜）：

| 时机 | core 做什么 |
|---|---|
| 第一次切到该页 | 在 **GUI 线程**调 `factory()`（每扇对话窗只调一次）；紧接着 `set_dark(当前深色值)`；然后 `activate()` |
| 切走 | `deactivate()`（切回来会再 `activate()`） |
| 对话窗被关闭 | 只给**当前显示的那一页**发 `shutdown()` |
| 重新打开对话窗 | 页面还在，`activate()` 会被再调一次（视图必须能在 `shutdown()` 之后重新 `activate()`） |
| 同一只桌宠开第二扇对话窗 | 工厂会被**再次调用**，得到**另一个实例**——不要用模块级单例共享一个控件 |

- 四个方法**全部可选**，core 用 `getattr` 探测并吞掉异常；工厂抛异常或返回非 `QWidget`
  → 显示错误占位、**对话窗其余功能不受影响**。
- `set_dark` 初始一定会被调用一次（工厂之后），所以别假设它只在用户点月亮时才来。
- `deactivate()` 可能**没有**配对的 `activate()`，实现要幂等。
- 视图会被放进 `QFrame#chat-view-host`，**左/右/下内缩 12px**（原生子窗口不吃圆角裁剪，
  这段留白是给 14px 圆角让位）。视图拿不到 core 的窗口对象，也不要 reparent 出去。
- `shutdown()` 之后别假设控件一定被销毁；再次 `activate()` 要能把状态恢复回来。
- **core 不替你的控件上色**：视图里的 QWidget 吃的是系统 palette（Windows 深色模式下会是深底浅字），
  对话窗自己的 QSS 不会套到你的控件上。要跟主题一致就在 `set_dark(bool)` 里自己设颜色——
  它一定会先被调用一次（工厂之后），所以把「浅色/深色两套色」写在那里就够：
  ```python
  def set_dark(self, dark):
      bg, fg = ("#16181d", "#e6e8ec") if dark else ("#ffffff", "#20242b")
      self.setStyleSheet(f"background: {bg}; color: {fg};")
  ```

想让菜单一键切到自己的页：

```python
ok = self.ports.chat.show_view(OWNER, "mine", pet_slot=slot)
```

`show_view` 的语义：**复用该桌宠正在用的那扇对话窗**并切到这一页；该桌宠还没有对话窗时，
会以调用方 owner 新建一扇。视图 id 没注册过 → 返回 `False` 且**不开任何窗口**。
它在非 GUI 线程调用也安全（内部排队），返回值只表示「已登记并已请求」。

**重载扩展**会重新跑 `create_host`：已经开着的对话窗仍握着旧实例，改完代码请重开对话窗。

---

## 5. 端口白名单（**只看这一节就够**）

`ports` 上**有实现**的：`presentation`、`scheduler`、`audio`、`credentials`、`storage`、
`resources`、`config`、`pets`、`interaction`、`quiet`、`api`、`worker`、`chat`、
`quick_chat`、`screen`、`screen_ask`、`system`、`http`、`speech`、`memory`、`webhook`。

`HostPorts` 里**声明了但运行时恒为 `None`**：`windows`、`menu`、`commands`、`input`、
`capabilities`。别照着 dataclass 写代码。

常用几招：

| 想做什么 | 用哪个 |
|---|---|
| 说一句话 / 放动画 / 显示图片 / 通知 | `presentation.say/play_animation/show_image/notify` |
| 回 GUI 线程 | `scheduler.call_later(ms, fn)`（后台线程里绝不碰 Qt 控件） |
| 发 HTTP 请求 | `http` 端口（不是 `requests`） |
| 读写配置 | `config.get/set`（写扩展自己的键，见 §7） |
| 存自己的数据 | `storage.data_dir(OWNER)` |
| 往对话窗写消息 | `chat.open/append/token/reasoning/done/error/show_view` |
| 语音合成 | `speech` |
| 让桌宠做动作 | `pets` / `presentation.play_animation` |

`stop()` 里要撤销自己注册的东西，可用的 `cancel_owner(owner)` 有三处：
`scheduler.cancel_owner(owner)`（定时器）、`speech.cancel_owner(owner)`、`reminders.cancel_owner(owner)`；
`interaction` 的取消由 core 在停用扩展时统一做。其它端口没有这个方法，别硬调。

---

## 6. 线程、Qt 与打包边界

1. **Qt 只在 GUI 线程碰**。运行时模块（`runtime.py` 这类）**不要 `import PySide6`**：
   官方样板和 `tests/test_petcore_ai_browser_mod.py` 会 grep 这一点。
2. 后台线程要回调 UI：`ports.scheduler.call_later(0, fn)` 或走 `chat` 端口（它自己排队）。
3. **宿主里有** `PySide6.QtWebView`（Edge WebView2 后端，`Qt6WebView.dll` +
   `plugins/webview/qtwebview_webview2.dll` 随包）；
   **宿主里没有** `PySide6.QtWebEngine*`（整个 200MB 的 Chromium 没打包）。
   用 `importlib.util.find_spec("PySide6.QtWebView")` 自检，别假设。
4. WebView2 需要系统的 **Microsoft Edge WebView2 Runtime**（Win11 自带）。
   缺失时给用户一个「打开微软下载页」+「重试」，不要直接崩。
5. `QWebView` 是原生子窗口：它盖不住也裁不到 Qt 的圆角/浮层。放它进布局时留内缩，
   不要指望 QSS 的 `border-radius` 能裁它。

---

## 7. 配置与数据

- 扩展自己的键请加前缀（`my_hello_text`），避免和 Core/其它扩展撞车。
- `order` 取值 0–1000，小的在前；相同 order 按 owner/id 排序。菜单默认 500。
- 图标名是**字符串**：`chat_view` 用对话窗图标表（`add/pin/minimize/maximize/restore/exit/
  sidebar/send/stop/copy/attach/clear/remove/more/retry/mic/globe/moon/sun`），
  菜单项用右键菜单图标表（`chat` 等）。写不认识的名字不会抛错，会回落成一个圆点——所以
  「按钮没图标」先怀疑名字拼错。
- `.trusted` **只对「用户数据目录」和「安装目录」里的包生效**，用来给官方投放的包免开全局开关；
  开发期的包不要带 `.trusted`，否则你会测不到「没开开关」的那条路径。
- 落在 `<数据目录>/feature-data/<owner>/` 的数据不会随卸载删除（用户可能想保留）。
- 平台：设置页里能写的东西，用户期望「取消」能回滚——实现
  `dirty/confirm_save/discard_changes` 三个方法（见 §4.2）。

---

## 8. 本地开发闭环

```bash
# 1) 装进数据目录
python -m petcore mods install examples/petcore-mods/my.hello --force

# 2) 打开「允许功能扩展」：设置 → 扩展管理 → 勾选 → 点「重新加载扩展」
#    （已经在跑：重载即可，不必重启）

# 3) 排错
#    日志：<数据目录>/logs/petcore.log，搜 "feature" / "mod diagnostic"
#    清单问题：python -m petcore mods list

# 4) 卸载（按 install.json 逐文件删；feature-data 保留）
python -m petcore mods uninstall my.hello
```

设置页调试也可以直接用源码跑：`python -m petcore --settings`。

---

## 9. 提交前 checklist

- [ ] `mod.json` 通过校验：`id` 合法、`version` 是 `X.Y.Z`、`api_version` 是 `"1"`、
      `core_requires` 覆盖你要用的 API（`chat_view` 需要 `>=0.4.0`）。
- [ ] 每个 `menu` 贡献的 `command` 都在 `commands` 里；每个 `chat_view` 都在
      `view_factories` 里有工厂；`settings_page` 有 `settings_factory`。
- [ ] 运行时模块不 `import PySide6`；Qt 只在设置页/视图模块里。
- [ ] 视图实现了 `deactivate()`（至少停加载），`shutdown()` 有就更好。
- [ ] 每个没有可见文字的设置行都登记了搜索词。
- [ ] 写的配置键都有前缀；`dirty/confirm_save/discard_changes` 行为正确。
- [ ] 卸载后不残留崩溃路径：`stop()` 里取消定时器、`ports.*.cancel_owner(OWNER)`。

---

## 10. 错误对照表

| 现象 | 原因 | 修法 |
|---|---|---|
| 启动后菜单里没有我的项 | feature 包未启用 / 没开「允许功能扩展」 / 缺 `.trusted` | 装上并在扩展管理里重载；看日志 `feature mods disabled` |
| 点菜单没反应 | `command` 名和 `data.command` 不一致 | 对照 `runtime.commands` 的键 |
| 突然静默退出 | 扩展里调了 `sys.exit()`/`SystemExit` | 用 `raise` 或直接 `return` |
| 设置搜不到 | 行里没有可见文字，也没登记搜索词 | `register_search_terms(...)` / `search_keys=...` |
| 标题栏没有我的按钮 | `chat_view` 的 `label` 为空 / 没有 `view_factories` / 宿主缺 QtWebView | 补齐两者；`find_spec` 自检后决定是否声明 |
| 切走以后还在跑 | 视图没实现 `deactivate()` | 停定时器、`stop()` 加载 |
| 界面卡住 | 在 GUI 线程做了网络/磁盘 IO | 丢后台线程 + `scheduler.call_later` |
| 面板读不到配置 | 视图工厂没拿到 `ports`（工厂是无参的） | 在 `create_host` 里闭包捕获 `context.ports` 再传给视图 |
| 第二次开对话窗行为怪 | 用了模块级单例控件 | 每扇窗口一个实例，状态放实例上 |
| 关窗后再打开是白页 | `shutdown()` 之后没能在 `activate()` 里恢复 | `activate()` 里按需重建/重载 |
| 打包后功能消失 | 用了宿主没打包的模块 | 只能 `find_spec` 自检 + 降级 |

---

## 11. 直接把这段丢给你的 AI

```text
你要为一个叫 petcore 的 PySide6 桌宠程序写扩展，格式约束如下（api_version=1）：

1. 包结构：mods/<id>/mod.json + host/{__init__,factory,runtime}.py。
2. mod.json 必需字段：schema=1、kind="feature"、id（^[a-z0-9][a-z0-9._-]{0,127}$）、
   name、version（X.Y.Z）、api_version="1"、execution="host-only"、
   entrypoint="host/__init__.py:create_host"、core_requires=">=0.4.0,<1.0.0"。
3. factory.py 必须导出 create_host(context) -> FeatureDefinition(owner, contributions, ...)。
4. 可用的 contribution kind：menu / settings_page / chat_view / phrases / sound / theme / seasonal。
   menu 的 data 用 {label, command, order, icon}；settings_page 用 {title, order}；
   chat_view 用 {label, icon, order} 且必须在 FeatureDefinition.view_factories 里给
   {contribution_id: callable -> QWidget}。
5. 端口白名单（有实现）：presentation、scheduler、audio、credentials、storage、resources、
   config、pets、interaction、quiet、api、worker、chat、quick_chat、screen、screen_ask、
   system、http、speech、memory、webhook。
   恒为 None（别用）：windows、menu、commands、input、capabilities。
6. 线程：Qt 只能在 GUI 线程碰；runtime.py 不许 import PySide6；后台线程回 UI 用
   ports.scheduler.call_later(0, fn)。
7. chat_view 的 QWidget 可选实现 activate()/deactivate()/set_dark(bool)/shutdown()；
   工厂在 GUI 线程、首次切到该页时被调用一次；异常会被 core 隔离。
8. 宿主有 PySide6.QtWebView（Edge WebView2），没有 PySide6.QtWebEngine*；
   用 importlib.util.find_spec 自检后再决定是否声明依赖它的功能。
9. 设置页继承 petcore.ui.settings.base.SettingsPage，实现 dirty/confirm_save/
   discard_changes；没有可见文字的行要 register_search_terms。
10. 不要把任何东西写进 core 的私有模块（petcore.net/api/account/platform），
    不要自己再造一个聊天窗口。

请按以上约束生成完整、可安装的扩展文件，并在最后给出安装与自测命令。
```
