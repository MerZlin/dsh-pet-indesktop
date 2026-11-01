# 给 AI 的 petcore 扩展开发说明（`api_version = "1"`）

> **基线**：petcore `0.5.0`、`api_version = "1"`、`schema = 1`（2026-11-01 工作区）。
> 本文是**自包含**的：把整篇丢给任何 AI（或人），它就能写出能装、能跑的 petcore feature 扩展，
> 不需要先读仓库。字段级细节在 [`PETCORE-MOD-FORMAT.md`](PETCORE-MOD-FORMAT.md)，
> 动手流程与排错在 [`PETCORE-MOD-DEVELOPMENT.md`](PETCORE-MOD-DEVELOPMENT.md)。
>
> 如果你只想要一段可以粘贴的提示词，直接跳到 §11。

---

## 0. 先做三个判断

1. **只是加素材**（角色动画、音效、台词、皮肤）→ 做 **content 包**，没有 Python；
   但注意：`sounds/themes/phrases/seasonal` 这四类内容块当前**没有运行期消费方**，
   只有 `character` 块会被真正加载。
2. **要加功能**（菜单项、设置页、对话窗里的一页、调 API/读文件/语音）→ 做 **feature 包**，
   入口是一个 Python 函数，返回 `FeatureDefinition`。
3. **要独立窗口的小工具/小游戏**也可以，但 core 没有窗口端口：自己建 `QDialog`/`QMainWindow`，
   生命周期自己管。**不要自己再造聊天窗**；要占对话窗的一页用 `chat_view`。

**不要假设**（AI 最常犯的四个错）：

- `HostPorts` 里声明了但没注入的端口（§5 有清单）；
- 宿主安装包里没有的 Qt 模块（§6）；
- 不存在的 contribution 类型（§3，只有 3 个真正生效）；
- 把 `capabilities` 当成沙箱或运行期授权。

---

## 1. 包放哪、怎么装

| 位置 | 谁能加载 | 说明 |
|---|---|---|
| `<数据目录>/mods/<id>/` | feature + content | 用户数据目录；本地导入、市场安装、角色包都落这里 |
| `<安装目录>/mods/<id>/` | feature + content（仅冻结包） | 安装器投放；同目录有 `.trusted` 才免开全局开关 |
| `<仓库>/mods/`、`--mods-dir` | **只 content** | feature 永远 disabled，别在这测 feature |
| `examples/petcore-mods/<id>/` | 不是运行位置 | 样板/安装源；用 CLI 装到数据目录后再测 |

数据目录默认：

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\dsh-pet-core` |
| macOS | `~/Library/Application Support/dsh-pet-core` |
| Linux | `~/.config/dsh-pet-core` |
| `--portable` | 冻结包 `<安装目录>/data`；源码树 `<仓库根>/data` |
| `--instance NAME` | `dsh-pet-core-NAME` |
| `--root PATH` | 显式指定，优先级最高 |

> 源码运行**也**默认用 `%APPDATA%\dsh-pet-core`，不是仓库的 `data/`。要隔离测试就传 `--root`。

安装/重装：

```bash
python -m petcore mods validate ./my.hello          # 静态校验
python -m petcore mods install ./my.hello --force   # 目录或 .zip 都行
python -m petcore --allow-feature-mods              # 打开功能扩展开关并落盘
python -m petcore                                   # 启动；已启动就点「重新加载扩展」
```

feature 包还需要全局开关 **「允许功能扩展」**（设置 → 扩展管理），或者位于
`<安装目录>/mods` 且带 `.trusted`。用户数据目录里的 `.trusted` 无效。

日志：`<数据目录>/logs/petcore.log`，搜 `feature`、`mod diagnostic`。

`mods` 子命令只有：`list / validate / install / remove / enable / disable / doctor`。
**没有 `uninstall`**；`remove` 只处理 `<数据目录>/mods/<id>`，安装器投放的包要在设置里卸载。

---

## 2. 最小骨架（feature 包）

```
my.hello/
  mod.json
  host/
    __init__.py        # from .factory import create_host
    factory.py         # 唯一装配点，返回 FeatureDefinition
    runtime.py         # 命令与后台逻辑（**不要 import PySide6**）
    settings_page.py   # 设置页（可以用 QtWidgets）
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
  "author": "you",
  "execution": "host-only",
  "entrypoint": "host/__init__.py:create_host",
  "capabilities": ["speech.present", "settings.read", "settings.write"]
}
```

- `id`：`^[a-z0-9][a-z0-9._-]{0,127}$`，不能是 `con/prn/aux/nul/com1..9/lpt1..9`。
- `version`：`X.Y.Z`；`api_version` 必须字面是 `"1"`，否则整包不加载。
- `core_requires`：逗号分隔的 `>=/<=/>/</==` + `X.Y.Z`；不满足整包不加载。
  用 `chat_view` 至少 `>=0.4.0`；只在本机 0.5.0 验证过可以写 `>=0.5.0`。
- `execution` 只支持 `"host-only"`。写 `"worker"` 合法，但启动时会被跳过并记
  `worker feature is not supported yet`。
- `requires` 会被校验格式，但**不做依赖解析/自动安装**，别依赖它。
- `capabilities` 只能取下列值，写别的校验报错：
  `character.read`、`animation.request`、`speech.present`、`sound.play`、
  `notification.present`、`settings.read`、`settings.write`、`network.request`、
  `files.read`、`filesystem.user_data`、`screenshot.capture`、`process.spawn`。
  它只是**声明**，不是权限闸门。
- 未知顶层字段只 warning，不阻断加载。

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
                data={"label": "打个招呼", "command": "hello", "order": 60, "icon": "chat"},
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

`create_host(context)` 的字段：

```python
context.owner        # "my.hello"
context.core_version # 例如 "0.5.0"
context.api_version  # "1"
context.ports        # HostPorts（§5）
context.host         # 宿主对象；只读使用，别依赖内部结构
context.log(msg, level="info")
context.config       # 保留字段，当前 loader 传 {}；不要当配置源
context.events       # 当前为 None；不要依赖
```

`settings_factory` 收到的是**设置宿主代理**（可当 app host 用），不是 `FeatureContext`；
`host.config` 是 `ScopedConfig`，`host.window` 是当前设置窗绑定的那只宠。
一个 mod 只有**一个** `settings_factory`，一般只声明一个 `settings_page`。

`view_factories` 的工厂**不接受参数**；要拿端口就在 `create_host` 里闭包捕获：

```python
def create_host(context):
    ports = context.ports
    return FeatureDefinition(
        owner=OWNER,
        contributions=(...),
        view_factories={"mine": lambda: MyView(ports)},
    )
```

### 2.4 `host/runtime.py`（无 Qt）

```python
import logging

OWNER = "my.hello"
log = logging.getLogger(OWNER)


class HelloRuntime:
    def __init__(self, context):
        self.ports = context.ports
        self.commands = {"hello": self.hello}   # 键必须和 data.command 一致
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
        self.text.setText(self._initial)
        card.add_row("文字", self.text, "点菜单时说的话。", expand=True,
                     search_keys=("招呼", "台词"))
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

不继承 `SettingsPage` 也能被托管，但会变成只读占位页，提示「改动不会随设置保存」。

---

## 3. 可用的贡献类型

`CONTRIBUTION_KINDS` 有 10 个取值，但**只有 3 个有消费点**：

| kind | 用途 | `data` 真正生效的键 |
|---|---|---|
| `menu` | 右键菜单项 | `label`、`command`、`order`、`icon`、`checked` |
| `settings_page` | 设置页（Core 托管、可搜索） | `title`、`order`、`icon`、`host_page`（仅内置特性） |
| `chat_view` | 对话窗里的一页 | `label`（必填）、`icon`、`order` |
| `command` / `hotkey` / `card` / `install_hook` | **没有消费者** | — |
| `phrases` / `sound` / `theme` / `seasonal` | loader 抽取，运行期不消费 | — |

### 3.1 菜单

```python
Contribution(kind="menu", id="open", data={
    "label": "打开面板",
    "command": "open",     # 缺省等于 contribution id
    "order": 70,           # 只是排序，惯例 0–1000，默认 500
    "icon": "chat",        # core 图标名；拼错回落成圆点，不报错
})
```

- 菜单项 id 在 Core 内是 `<owner>:<contribution id>`；默认进「扩展 → `<manifest 名字>`」。
- `command` 必须存在于 runtime 的 `commands` 字典；缺失只记 warning，菜单不炸。
- 命令在 **GUI 线程**调用；只有显式声明 `slot` 形参才会收到发起菜单的桌宠槽位：
  `def open(self, slot=None): ...`。`*args/**kwargs` 不会收到。
- 命令抛异常：core 记日志并继续。

### 3.2 设置页

- `settings_factory` + `Contribution(kind="settings_page", data={"title": ...})`。
- 第三方页统一进「扩展管理」，不新增侧边栏页；`host_page` 只有 `CORE_FEATURE_IDS` 生效。
- 保存/取消/释放由 Core 委托；取消时 Core 会恢复整份配置快照，但**控件**只有在你的
  `discard_changes()` 里刷新才会回滚。
- 设置搜索自动索引页面内所有可见文字、按钮、下拉项、`QTreeWidget` 项；没有可见文字的
  行用 `register_search_terms(widget, "关键词")` 或 `add_row(..., search_keys=(...))`。
- 扩展偏好键是**全局**的（feature runtime 共享），要按宠隔离请用 `storage.data_dir` 按 slot 存。

### 3.3 对话窗里的一页（`chat_view`，0.4.0+）

```python
# factory.py
def build_view():
    from .my_view import MyView     # Qt 只在真正切过去时导入
    return MyView()

Contribution(kind="chat_view", id="mine", data={
    "label": "我的面板", "icon": "globe", "order": 100,
})
FeatureDefinition(..., view_factories={"mine": build_view})
```

```python
# my_view.py
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

class MyView(QWidget):
    def activate(self):        # 切到这一页：恢复轮询/继续加载
        ...
    def deactivate(self):      # 切走：停加载、停定时器
        ...
    def set_dark(self, dark):  # 对话窗深色开关变化
        ...
    def shutdown(self):        # 对话窗关闭：释放页面
        ...
```

精确时序：

| 时机 | core 做什么 |
|---|---|
| 第一次切到该页 | GUI 线程调 `factory()`（每扇窗口一次）→ `set_dark(当前值)` → `activate()` |
| 切走 | `deactivate()`；切回来再 `activate()` |
| 关闭对话窗 | 先 `deactivate()`，再只对当前页 `shutdown()`；窗口只是隐藏，控件不销毁 |
| 重新打开对话窗 | 对当前视图再调一次 `activate()` |
| 同一只宠的同一 owner | 只有一扇窗口；窗口键是 `(owner, pet_slot)` |
| 新装/修改 `chat_view` | **必须重启应用**：视图清单在窗口创建时固定，关闭只是隐藏 |

其他保证：

- 四个方法全部可选，core 用 `getattr` 探测并吞掉异常；工厂抛异常/返回非 `QWidget`
  → 错误占位，对话窗其余功能不受影响。
- `set_dark` 在工厂之后一定先调用一次。
- `deactivate()` 可能没有配对的 `activate()`，实现要幂等。
- 视图被放进内缩 12px 的槽位（原生子窗口不吃圆角裁剪）；拿不到 core 窗口对象，不要 reparent。
- 视图不做默认页；core 不替视图上色，深色适配写在 `set_dark(bool)`。
- 菜单一键切过去：`self.ports.chat.show_view(OWNER, "mine", pet_slot=slot)`。
  它会复用该宠最近使用的那扇对话窗；GUI 线程下视图 id 没注册返回 `False` 且不开窗口，
  非 GUI 线程下只排队（返回值不做存在性判断）。
- 对话窗图标名：`add/pin/minimize/maximize/restore/exit/sidebar/globe/moon/sun/send/stop/
  copy/attach/clear/remove/more/retry/mic`；未知名字回落成圆点。

---

## 4. 端口白名单

`HostPorts` 声明 27 个字段，**有实现的是 22 个**：

`presentation`、`scheduler`、`audio`、`credentials`、`storage`、`resources`、`config`、
`pets`、`interaction`、`quiet`、`reminders`、`http`、`api`、`chat`、`quick_chat`、`screen`、
`screen_ask`、`system`、`speech`、`memory`、`webhook`、`worker`。

**声明了但运行期恒为 `None`**：`windows`、`menu`、`commands`、`input`、`capabilities`。
别照着 dataclass 写代码；先判空。

常用：

| 想做什么 | 用哪个 |
|---|---|
| 气泡 / 图片 / 通知 / 动画 | `presentation.say / show_image / notify / play_animation / play_event` |
| 回 GUI 线程 | `scheduler.call_later(0, fn)` |
| 发 HTTP 请求 | `http` 端口（不是 `requests`） |
| 读偏好 / 写一次性偏好 | `config.get/ set` |
| 存自己的数据 | `storage.data_dir(OWNER)` |
| 往对话窗写消息 | `chat.open/append/token/done/error/show_view` |
| 语音合成 | `speech`（`synthesize` 阻塞，`speak` 异步） |
| 桌宠动作 / 位置 | `pets` 句柄、`presentation.play_animation` |
| 点击 / 拖文件 / 每帧 | `interaction.on_click / on_drop / on_tick` |
| 定时 | `scheduler.call_later / call_every` |

`stop()` 里可撤销的：

- `scheduler.cancel_owner(OWNER)`；
- `speech.cancel_owner(OWNER)`、`reminders.cancel_owner(OWNER)`；
- `interaction` 的订阅由 core 在停用扩展时统一撤，也可以自己调 `cancel_owner(OWNER)`。
- `scheduler.call_every/later` 的 `owner=` 是 core 实现（`QtScheduler`）的扩展参数，用于
  `cancel_owner`；协议只写了两个位置参数，换宿主实现前先确认。

---

## 5. 线程、Qt 与打包边界

1. **Qt 只在 GUI 线程碰。** 运行时模块（`runtime.py` 这类）不 `import PySide6`；
   需要 Qt 的窗口/视图单独成模块，在 GUI 线程的命令里惰性导入。
2. 后台线程回 UI：`ports.scheduler.call_later(0, fn)`。
3. 同步阻塞、要下后台的：`http.request/get_json`、`speech.synthesize`、`config.set`、
   `storage.data_dir`、任何读写盘。
4. `screen_ask.on_ask/on_action` 回调在**后台线程**：只碰纯数据。
5. `presentation` / `chat` / `quick_chat` 现在可以从后台线程调用（实现会排队到 GUI），
   但不要依赖同步效果；能回 GUI 就回。
6. `Runtime.close()` 不会被调用；清理写在 `stop()`。
7. 宿主冻结包里有 QtCore / QtGui / QtWidgets / QtNetwork / QtMultimedia / QtSvg，
   以及 `PySide6.QtWebView`；**没有** `PySide6.QtWebEngine*`。
   用 `importlib.util.find_spec("PySide6.QtWebView")` 自检，不可用就降级。
8. `QWebView` 需要系统 Microsoft Edge WebView2 Runtime（Win11 自带）；
   它是原生子窗口，不吃 Qt 圆角/浮层。

---

## 6. 配置与数据

- 扩展自己的键请加前缀（`my_hello_text`），避免和 Core/其它扩展撞车。
- 第三方键自动落 `config.json` 的 `extra` 区块，**全局共享**：
  键名 `^[a-z0-9][a-z0-9._-]{0,127}$`；单值 ≤4KB、整块 ≤64KB；
  命中密钥样式的值会被拒绝。
- `config.set` 会立即落盘；不要把运行时状态/计时/缓存写进 config。
- 业务数据放 `<data_dir>/feature-data/<owner>/`，自己写 JSON，**临时文件 + `os.replace`**；
  带 `schema_version`；损坏文件不要静默覆盖。
- 卸载不会删 `feature-data/<owner>`；在设置页给用户「清除数据」入口。
- 密钥只进 `ports.credentials`，不要写 config，也不要写日志。
- `order` 没有硬钳制，只是排序；相同 order 按 owner/id 排序。
- 图标名拼错不会抛错，会回落成圆点——「按钮没图标」先怀疑名字。

---

## 7. 自建窗口（可选）

core 不提供窗口端口。需要独立窗口时：

- Qt 代码单独成模块，只在 GUI 线程的命令里惰性导入；
- runtime 持住窗口引用；`show()` 后 `raise_()` / `activateWindow()`；
- 关闭时停定时器、清引用；`stop()` 兜底关窗；
- 多宠：命令收到 `slot`，决定窗口是全局一扇还是每宠一扇；
- 置顶、跟随、几何持久化、多屏归属都自己写；
- 游戏循环用自己窗口的 `QTimer` 或 `interaction.on_tick`，窗口不可见就停；
- 重活下后台；与桌宠同进程，死循环/原生崩溃会带走整个应用。

---

## 8. 本地开发闭环

```bash
# 1) 校验 + 装进数据目录
python -m petcore mods validate ./my.hello
python -m petcore mods install ./my.hello --force

# 2) 打开功能扩展开关（二选一）
python -m petcore --allow-feature-mods
# 或：设置 → 扩展管理 → 勾选 → 重新加载扩展

# 3) 排错
python -m petcore mods list
python -m petcore mods doctor
# 日志：<数据目录>/logs/petcore.log，搜 feature / mod diagnostic

# 4) 启停 / 卸载（没有 uninstall）
python -m petcore mods disable my.hello
python -m petcore mods enable my.hello
python -m petcore mods remove my.hello
```

改代码后生效方式：

| 改了什么 | 生效方式 |
|---|---|
| `runtime.py` / 菜单 / 命令 | 「重新加载扩展」 |
| `settings_page.py` | 关闭设置窗再打开（窗口在关闭时销毁，下次重建页面；必要时重启） |
| `chat_view` / 视图工厂 | **重启应用** |
| `mod.json` | 重新加载；`id/kind/entrypoint` 变了要重装 |

---

## 9. 错误对照表

| 现象 / 报错 | 原因 | 修法 |
|---|---|---|
| 扩展管理里看不到包 | 没装进数据目录 / manifest 校验失败 | `mods validate`；`mods list` 看 `!` |
| 包在但「已停用」/ 菜单没项 | 放错目录 / 没开 `allow_feature_mods` / 被 disable | 装到数据目录；开开关；`mods enable` |
| `feature mods disabled` | 同上 | 同上 |
| `worker feature is not supported yet` | `execution: "worker"` | 改 `host-only` |
| `entrypoint not found` | 路径写错 | 只 warning；运行时 import 失败并留诊断 |
| `create_host must return FeatureDefinition` | 工厂返回了别的对象 | 返回 `FeatureDefinition(...)` |
| `api_version must be '1'` | 写了别的版本 | 改成 `"1"` |
| `core_requires not satisfied` | 范围与 core 版本不符 | 放宽范围 |
| `invalid id` | 大写/空格/保留名 | 改成合法 id |
| `path escapes pack` | 资源/入口跑出包外 | 改包内相对路径 |
| `executable file in content pack` | content 包混进可执行文件 | content 只放媒体与 JSON |
| `duplicate mod id ...` | 同 id 多目录 | 删旧副本 |
| `already installed ...` | 同 id 已存在 | `mods remove` 或 `--force` |
| `multiple mod.json roots ...` | zip 多层包 | 只留一层 wrapper |
| 点菜单没反应 | `data.command` 与 `commands` 键不一致 | 对齐两个名字 |
| 设置搜不到 | 行没有可见文字也没登记搜索词 | `search_keys=` / `register_search_terms` |
| 设置取消后控件不回滚 | 没实现 `discard_changes()` | 回写配置 + 刷新控件 |
| `chat_view` 没按钮 | `label` 空 / 没给工厂 / 没重启 | 补齐并重启 |
| 切走以后还在跑 | 没实现 `deactivate()` | 停加载、停定时器 |
| 自造窗口不出现/被压住 | 没持引用 / 没 `raise_()` | 持引用、显示后抬升 |
| 设置页里 webview 变一条缝 | 设置页在 `QScrollArea`，阅读宽 860 | 自造窗口 |
| 打包后功能消失 | 用了没打包的 Qt 模块 | `find_spec` 自检 + 降级 |

---

## 10. 提交前 checklist

- [ ] `mods validate` 干净；`mods install` 后 `mods list` 是 enabled 且无 `!`。
- [ ] `id` 合法、`version` 是 `X.Y.Z`、`api_version` 是 `"1"`、`core_requires` 覆盖用到的 API。
- [ ] 每个 `menu` 的 `command` 都在 `commands`；每个 `chat_view` 都有工厂；
      `settings_page` 有 `settings_factory`。
- [ ] 运行时模块不 `import PySide6`；Qt 只在设置页/视图/窗口模块里，且惰性导入。
- [ ] 视图实现 `deactivate()`（至少停加载），`shutdown()` 有就更好。
- [ ] 每个没有可见文字的设置行都登记了搜索词。
- [ ] 配置键有前缀；`dirty/confirm_save/discard_changes` 行为正确。
- [ ] 数据写在 `storage.data_dir(OWNER)`，原子写；卸载后不残留崩溃路径。
- [ ] `stop()` 里取消定时器、`scheduler.cancel_owner(OWNER)`、关窗、停线程。
- [ ] 日志里没有密钥、Cookie、完整 URL/查询串。

---

## 11. 直接把这段丢给你的 AI

```text
你要为一个叫 petcore 的 PySide6 桌宠程序写一个功能扩展（feature mod）。
基线：petcore 0.5.0、api_version "1"、schema 1。请严格按以下约束生成完整、可安装的文件。

1. 包结构：mods/<id>/mod.json + host/{__init__,factory,runtime}.py [+ settings_page.py]。
2. mod.json 必需字段：schema=1、kind="feature"、id（^[a-z0-9][a-z0-9._-]{0,127}$）、
   name、version（X.Y.Z）、api_version="1"、execution="host-only"、
   entrypoint="host/__init__.py:create_host"、core_requires=">=0.4.0,<1.0.0"。
   capabilities 只能取 character.read / animation.request / speech.present / sound.play /
   notification.present / settings.read / settings.write / network.request / files.read /
   filesystem.user_data / screenshot.capture / process.spawn；它只是声明，不是沙箱。
3. factory.py 导出 create_host(context) -> FeatureDefinition(owner=...)，owner 必须等于 mod.json 的 id。
4. 真正生效的 contribution 只有三个：
   - menu：data={label, command, order, icon[, checked]}；command 必须存在于 runtime.commands；
     命令声明 slot 形参才会收到发起菜单的桌宠槽位。
   - settings_page：data={title, order[, icon]}；配 settings_factory=lambda host: Page(host)；
     页面继承 petcore.ui.settings.base.SettingsPage，实现 dirty/confirm_save/discard_changes；
     没有可见文字的行用 add_row(..., search_keys=(...)) 或 register_search_terms 登记。
   - chat_view：data={label, icon, order}；配 FeatureDefinition.view_factories={"<id>": callable -> QWidget}；
     视图可选实现 activate()/deactivate()/set_dark(bool)/shutdown()。
   不要使用 command / hotkey / card / install_hook（没有消费者）。
5. 端口白名单（有实现）：presentation、scheduler、audio、credentials、storage、resources、
   config、pets、interaction、quiet、reminders、http、api、chat、quick_chat、screen、
   screen_ask、system、speech、memory、webhook、worker。
   恒为 None（别用）：windows、menu、commands、input、capabilities。所有端口先判空。
6. 线程：Qt 只能在 GUI 线程碰；runtime.py 不许 import PySide6，也不许 import
   petcore.net / petcore.api / petcore.account / petcore.platform / petcore.ui（设置页除外）；
   后台线程回 UI 用 ports.scheduler.call_later(0, fn)；
   http.request / speech.synthesize / config.set / storage.data_dir 是同步阻塞，放后台线程。
7. 数据：业务数据放 ports.storage.data_dir("<owner>")，临时文件 + os.replace 原子写，带
   schema_version；偏好键放 config（自动落 config.json 的 extra，全局共享，单值 ≤4KB、
   整块 ≤64KB）；不要把运行时状态写进 config；密钥只进 ports.credentials。
8. 自建窗口：core 没有窗口端口。Qt 代码单独成模块，只在 GUI 线程的命令里惰性导入；
   runtime 持引用，show() 后 raise_()/activateWindow()，关闭停表，stop() 兜底关窗。
9. 宿主冻结包有 QtCore/QtGui/QtWidgets/QtNetwork/QtMultimedia/QtSvg 和 PySide6.QtWebView，
   没有 PySide6.QtWebEngine*；需要时用 importlib.util.find_spec 自检并降级。
10. 安装与自测命令（不要写 mods uninstall，也没有 --settings）：
    python -m petcore mods validate ./<id>
    python -m petcore mods install ./<id> --force
    python -m petcore --allow-feature-mods
    python -m petcore mods list
    python -m petcore mods doctor

请输出每个文件的完整内容，并在最后给出安装、启用、自测步骤和已知限制。
```
