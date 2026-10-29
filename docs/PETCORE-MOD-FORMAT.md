# petcore mod 格式

> 基线：`feat/petcore-vpet-architecture`，2026-10-20。首版实现 content 与 feature 两类包；feature 仅在内置演示与显式开关下加载。

> 这是**字段级参考**（每个键、每条报错、每类边界）。要动手建包、装到机器上跑起来，先看 [`PETCORE-MOD-DEVELOPMENT.md`](PETCORE-MOD-DEVELOPMENT.md)：里面有可复制的最小骨架、安装/重载/排错的完整闭环，以及角色包该放哪。

## 1. 目录

```
mods/<id>/
  mod.json              必需
  ...                   包内资源
```

`mods/<id>/mod.json`：

```json
{
  "schema": 1,
  "id": "character.shenshen",
  "kind": "content",
  "name": "Shenshen",
  "version": "1.0.0",
  "api_version": "1",
  "core_requires": ">=0.1.0,<1.0.0",
  "author": "example",
  "character": {
    "media": "videos",
    "canvas": [640, 360],
    "feet_y": 330,
    "body_box": [212, 60, 428, 330],
    "head_box": [185, 60, 436, 195],
    "move_strides": "videos/move_strides.json",
    "text_clips": "videos/text_clips.json"
  }
}
```

feature 包额外字段：

```json
{
  "kind": "feature",
  "execution": "host-only",
  "entrypoint": "host/__init__.py:create_host",
  "capabilities": ["sound.play"],
  "requires": ["character.shenshen"]
}
```

## 2. 字段

| 字段 | 必需 | 规则 |
|---|---|---|
| `schema` | 是 | 必须为 `1` |
| `id` | 是 | `^[a-z0-9][a-z0-9._-]{0,127}$`，全局唯一 |
| `kind` | 是 | `content` 或 `feature` |
| `name` | 是 | 展示名（英文/原名） |
| `name_zh` | 否 | 中文展示名（≤128；缺失/非法回退 `name`） |
| `version` | 是 | `X.Y.Z` |
| `api_version` | 是 | 首版只接受 `"1"` |
| `core_requires` | 否 | 逗号分隔的 `>=`/`<=`/`>`/`<`/`==` 约束 |
| `author` | 否 | 文本 |
| `execution` | feature 必需 | `host-only` 或 `worker` |
| `entrypoint` | feature 必需 | 包内相对路径 `path.py:function`；也支持包入口 `host/__init__.py:create_host`（loader 以包方式加载，允许相对 import） |
| `capabilities` | 否 | 必须属于 `contracts/capabilities.py` 的清单 |
| `character` | content 可选 | 角色块，见 `PETCORE-CHARACTER-PACKS.md` |
| `sounds`/`themes`/`phrases`/`seasonal` | 否 | `{id: 相对路径}` 的内容贡献 |

未知顶层字段产生诊断但不阻断加载。

## 3. 校验

- 路径：所有声明的路径必须解析在包内；拒绝绝对路径、`..` 逃逸、解析到包外的符号链接。
- content 包不得包含可执行文件：`.py .pyc .dll .so .dylib .exe .js .bat .cmd .ps1`。
- feature 包必须提供包内入口；绝对路径与包外路径一律拒绝。
- `api_version` 大版本不匹配 → 该包停用并给出诊断，不影响其他包。
- 单个包的任何异常都转成 `ModDiagnostic`，绝不阻断 core 启动。

## 4. 发现与启停

- 扫描顺序：用户数据目录 `mods/` → 安装目录 `mods/`（仅冻结包）→ 仓库 `mods/`；按
  `(id, path)` 排序，结果确定。
- 同 id 后加载者覆盖前者，并记录冲突诊断。
- `mods-state.json` 只保存停用集合；文件缺失按“全部启用”。
- 停用不移动文件、不删数据；卸载只删除用户目录下的包目录，绝不动安装目录/仓库
  `mods/`。
- feature 包只在 `allow_feature_mods` 为真、且位于用户数据目录或安装目录 `mods/`
  时加载；安装器投放的包由同目录 `.trusted` 逐包授权。

## 5. 贡献与合并

content 贡献类型：`character`、`sound`、`theme`、`phrases`、`seasonal`。

合并规则：

- 同 id 的角色包：后加载者覆盖同一动画键，其余键保留；
- 音效/主题/台词：同 key last-wins；
- 冲突写诊断，不静默。

### 5.1 菜单贡献

feature 包通过 `Contribution(kind="menu")` 注册菜单项，`data` 支持：

```json
{"label": "打个招呼", "command": "hello", "section": "extensions", "order": 55, "icon": "chat"}
```

- 动作 id 在 Core 内统一为 `<owner>:<contribution>`，避免跨包冲突。
- 未在用户菜单布局中出现的贡献项会**自动进入「扩展」子菜单**；用户可在设置 →「菜单」里把它拖到根或其它子菜单、改名、换图标、隐藏。
- 卸载后该项从菜单消失但布局节点保留为未知；重装即可恢复，编辑器也提供清理入口。
- `command` 必须存在于该包 runtime 的 `commands` 字典；缺失时点击只记诊断，不影响菜单构建。

## 6. 安装

```
python -m petcore mods install <目录或.zip> [--force]
python -m petcore mods list
python -m petcore mods validate <路径>
python -m petcore mods enable <id>
python -m petcore mods disable <id>
python -m petcore mods remove <id>
python -m petcore mods doctor
```

`--root <数据目录>` 可以写在 `mods` 前面（`petcore --root D:/pets mods list`）或后面；
前置时 `main()` 先解析全局选项再统一下发根目录。`--portable` / `--instance` 同样可前置。

安装流程：校验 manifest → staging 目录 → 校验内容摘要 → 原子改名到 `mods/<id>`；目标已存在时拒绝，`--force` 才替换（旧目录先移入 `.trash-*`）。ZIP 安装拒绝 zip-slip。

### 6.1 授信标记 `.trusted`

功能包默认需要用户打开「允许功能扩展」（`allow_feature_mods`）。Windows 安装向导
投放的官方扩展走**逐包授权**：装到安装目录 `{app}\mods\<id>`，目录里放一个
`.trusted` 空文件即视为已授信（`petcore/mods/trust.py`），与全局开关并列；不勾选
安装就不会有目录，卸载随 `{app}` 一起删除。这类包在扩展管理里归「加装扩展」，
标「随安装包」，**也可以在设置里单独卸载**（只删包目录，个人数据保留）。用户自己
导入的包仍装到数据目录 `mods/`，同样归「加装扩展」，可单独卸载。

### 6.2 市场安装

`petcore market install` 与设置页「插件市场」走同一套 `petcore/market/` 模块：索引
元数据、下载、SHA-256 校验、备份、原子替换与回滚。市场安装的包同样落用户数据目录
`mods/<id>`，feature 包仍受 `allow_feature_mods` 约束，不会因为来自市场就自动授信。
索引格式、来源缓存、回滚与生效语义见 [`PETCORE-MARKET.md`](PETCORE-MARKET.md)。

## 7. 能力与边界

能力是**声明 + 用户授权**，不是沙箱。首版不提供：

- 第三方代码的默认执行；
- Python 模块热卸载；
- worker 监管（协议已定义，实现留给 P1/P3）；
- 签名与沙箱；在线更新与多源下载见 [`PETCORE-MARKET.md`](PETCORE-MARKET.md)。

2026-10-27 实测补充（`tool.ai-browser` 探针，详见
[`PR-REPORT-AI-BROWSER-MOD-2026-10-27.md`](PR-REPORT-AI-BROWSER-MOD-2026-10-27.md)）：

- **`capabilities` 不构成授权闸门**：`network.request` / `process.spawn`
  既不产生端口，也没有运行期强制；扩展用 `subprocess` 拉外部程序不受任何检查。
- **窗口**：没有窗口端口，但扩展在 GUI 线程的命令里可以自己 `QMainWindow`/`QDialog`
  （官方样板 `fun.divination` 已如此）。置顶、跟随桌宠、多宠归属、关闭清理都得自己实现。
- **宿主能力差异**：某个 Qt 模块是否可用取决于宿主打包（安装包 `_internal/PySide6` 里没有
  `QtWebEngine*`），扩展必须自检并降级；`importlib.util.find_spec` 是约定写法。
- **ZIP 安装**：CLI 与 GUI 共用 `install()` 的同一套校验；CLI 的 `mods install` 直接接受 `.zip`
  （见 §6），导入时会剥掉包里自带的 `.trusted`。

## 8. 内置 feature 与端口（P2）

- 内置包位于 `petcore/builtin/features/<id>/`，格式与用户 feature 包相同；由 `FeatureManager.load_builtin_features()` 扫描加载，**受信任**，不要求 `allow_feature_mods`。
- 内置包只保留与 core 交互强相关的 7 个：`fun.self-talk`、`fun.click-talk`、`tool.quick-launch`、`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info`。P8 起这 7 个是 **Core 内置特性**（`CORE_FEATURE_IDS`，group=`core`）：无条件加载，不参与安装/停用/卸载，设置页按 `host_page` 内嵌到 Core 页，不再出现在扩展管理导航。
- 其余 `fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music` 是**可导入样板**，位于 `examples/petcore-mods/`；用 `petcore mods install <目录>` 安装到用户数据目录，并开启 `allow_feature_mods`。安装后由 `FeatureManager` 从用户 mods 目录加载，菜单贡献默认进入「扩展」子菜单。Windows 安装向导勾选安装时落 `{app}\mods\<id>`，归「加装扩展」。
- 设置页不再各自占侧边栏：第三方 mod 的 `settings_page` 进入「扩展管理」页内导航；Core 内置特性用 `Contribution.data["host_page"]` 声明归属（`interaction` / `menu` / `about`），由对应 Core 页内嵌，见 §9。
- `HostPorts` 新增：
  - `pets`：只读宠物句柄（body_rect / velocity / move_by / show / hide / say / play_animation / play_event）；
  - `presentation`：`say` / `show_image` / `play_animation` / `play_event` / `notify`；
  - `interaction`：`on_click`、`on_drop`、`on_tick`、`cancel_owner`；`on_drop` 回调收到的是
    **已投喂的文件路径列表**（Core 先做投喂动画与统计，再通知监听者；`file_feeding_enabled`
    关闭时不会有 drop 事件）；
  - `http`：普通取数端口（见 §11）；
  - `quiet`：进程级免打扰。
- `FeatureContext.host` 暴露宿主对象，mod 可读取共享配置；跨域仍走端口/事件，不直接 import `pet.*`。
- 每个有菜单贡献的内置 mod 必须提供 `settings_page`；`tests/test_petcore_builtin_mods.py` 守住。
- 内置包当前清单：`fun.self-talk`、`fun.click-talk`、`tool.quick-launch`、`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info`。
- 示例可导入包：`examples/petcore-mods/{fun.divination,fun.festival,reminder.todo,voice.chime,media.music,tool.weather,tool.file-reader}`；`examples/petcore-mods/README.md` 是安装与端口样板说明。


## 9. Core 内置特性与 `host_page`（P8）

内置包的 `settings_page` 贡献可以多写一个 `host_page` 字段，声明它的设置内容应该
内嵌到哪个 Core 设置页，而不是出现在「扩展管理」：

```json
{"kind": "settings_page", "id": "page", "data": {
  "title": "点击台词", "order": 610, "host_page": "interaction"
}}
```

| `host_page` | Core 页 | 当前使用方 |
|---|---|---|
| `interaction` | 互动与物理 | `fun.click-talk`、`fun.self-talk`、`tool.quiet-mode` |
| `menu` | 菜单 | `tool.quick-launch` |
| `about` | 关于与诊断 | `tool.links`、`tool.network-info`、`tool.memory-trim` |

规则：

1. `CORE_FEATURE_IDS`（`petcore/mods/feature.py`）里的包 group 固定为 `core`：
   无条件加载、不可停用/卸载、不进扩展导航。
2. 有 `host_page` 的 `settings_page` 由 `FeatureManager.embedded_pages(host_page)`
   返回，Core 页负责 `prepare_embedded()` 与生命周期（保存/取消/释放）。
3. 第三方 mod 不要写 `host_page`：它们仍应只出现在「扩展管理 → 加装扩展」。
4. 新增 Core 内置特性时必须同时补：`CORE_FEATURE_IDS`、`host_page`、
   `tests/test_petcore_builtin_mods.py` 的映射断言。

## 10. P3 端口：对话、截图与 AI 扩展示例

P3 新增两条 mod 可见端口，AI 执行放在 mod，传输与窗口留在 Core：

| 端口 | 能力 | 谁实现 |
|---|---|---|
| `HostPorts.chat`（`ChatPort`） | `open/append/token/done/error/close/cancel/is_open/history` + `on_submit/on_stop` | `petcore/ui/chat/controller.py` |
| `HostPorts.screen`（`ScreenPort`） | `capture(pet_slot=...) -> ScreenCapture(png, width, height, screen_name)` | `petcore/platform/screenshot.py` |
| `HostPorts.api`（扩展） | `stream_chat(service_id, messages, *, model, temperature, timeout, on_delta, cancel)` | `petcore/api/chat.py` |

约定：

1. **mod 不碰 Qt**：聊天窗口由 `ChatPort` 驱动；`token/done/error` 可以从后台线程调用，
   controller 用 Qt 信号排队到 GUI 线程。`petcore/chat/` 保持 Qt-free（架构守卫）。
2. **传输只在 core**：SSE、代理兜底、取消与错误分类都在 `petcore/api/chat.py`；
   扩展不得 import `petcore.net`/`petcore.api`（`test_mods_use_ports_not_core_transport`）。
3. **截图不落盘**：`ScreenCapture.png` 是内存字节；转 data URL 由调用方自己做
   （示例用标准库 `base64`），Core 不写临时文件、不记录正文。
4. **流式回调线程**：`on_delta` 在请求线程里执行，只做入队/信号，不能直接改控件。
5. **停止语义**：`ChatPort.on_stop` 收到停止后置位自己的 `threading.Event`，
   下一次 `stream_chat` 检查取消并返回 `cancelled=True`。

### 参考实现：`examples/petcore-mods/ai.chat`

```text
mod.json           kind=feature, execution=host-only,
                   capabilities=[network.request, screenshot.capture,
                                 speech.present, settings.read, settings.write]
host/__init__.py   create_host：只做 FeatureDefinition 组装
host/constants.py  OWNER / 历史上限 / 看看屏幕冷却
host/prompts.py    默认人格、识屏/截图话术、消息组装纯函数
host/runtime.py    三条命令 open / look_screen / screen_capture；
                   注册 chat.on_submit / on_stop / screen_ask.on_ask；
                   只经 ports.config 读配置、ports.scheduler 回 GUI 线程（无 Qt）
host/settings_page.py  设置页（受信 UI，允许 QtWidgets）
```

`runtime.py` 不得 import PySide6；设置页是唯一允许 Qt 的运行位置。

安装：`petcore mods install examples/petcore-mods/ai.chat`（或安装向导勾选，默认不勾）。
没有安装时，右键菜单与三条热键会气泡提示「AI 扩展未安装」，不会静默无响应。

## 11. P4 端口：通用取数 `HttpPort`（2026-10-22）

天气、歌词这类「普通 HTTP GET」原先没有入口：mod 只能拿 `ApiPort`（LLM 专用），
而网络库按架构守卫禁止出现在 mod 里。P4 增加 `HostPorts.http`：

| 端口 | 方法 | 说明 |
|---|---|---|
| `HttpPort` | `request(HttpRequest) -> HttpResponse` | 唯一取数入口；失败抛 `HttpError(reason)` |
| `HttpPort` | `get_json(url, *, timeout, headers, params, prefer_direct)` | 便利方法；解码失败统一 `protocol` |

`HttpRequest` 关键字段：

- `prefer_direct`：`False` 先走系统代理，`True` 先直连（国内站点口径）。
- `retry_statuses`：允许换路由的服务器明确答复，默认 `(500, 502, 504)`；404/429/503
  与带 `Retry-After` 的响应**不换路由**。
- `params`：查询串键值对，百分号编码由 Core 做（mod 不需要 `urllib.parse`）。
- `max_bytes`：响应体硬上限，默认 4 MB；超出部分截断，由调用方按协议判无效。

错误只暴露 `HttpError.reason`（`auth/rate_limit/network/timeout/protocol`）、
`status`、`retry_after`；异常链与响应体不外泄（响应体可能回显密钥）。

**硬规则**：

1. 两条路由共享同一个 `timeout` 预算，最多各试一次；显式路由请求**不改写**进程级
   「固定直连」结论（天气是国内直连、海外代理，一次失败不能污染其它域）。
2. mod 不得 import `petcore.net`/`petcore.api`/`petcore.account`；`urllib` 等网络库
   仍然只允许出现在这三个包（`tests/test_petcore_architecture.py`）。
3. 新增取数功能时先想清楚路由：国内源 `prefer_direct=True`，海外源用默认值。

### 参考实现：`examples/petcore-mods/tool.weather`

```text
mod.json               capabilities=[network.request, speech.present]
host/logic.py          城市名/地区码/两源解析/WMO 码表/失败文案（纯逻辑）
host/client.py         两源取数：CN 直连 → open-meteo 代理，6s/请求 + 15s 总预算
host/runtime.py        命令 show：后台线程 → 气泡；运行时无 Qt
host/settings_page.py  启用、城市列表、当前城市、立即查看
```

### 参考实现：`examples/petcore-mods/tool.file-reader`

```text
mod.json               capabilities=[files.read, network.request, speech.present]
host/reader.py         后缀白名单、20000 文件/2s 扫描预算、200000 字符有界读取
host/runtime.py        interaction.on_drop → 后台 stream_chat → 气泡 + ChatPort.record
host/settings_page.py  启用、进度间隔（5–120s）、是否写入聊天记录
```

`files.read` 是 P4 新增的能力声明（读取用户拖入的任意文本文件）；它同样只是声明，
不是沙箱。文件解读**只读**：不修改、不移动、不删除被拖入的文件，路径不写日志正文。

## 12. P4 端口：语音合成 `SpeechPort`（2026-10-22）

`HostPorts.speech` 是 mod 说话的唯一入口。合成、缓存、失败兜底、密钥全在 core；
mod 只决定「说什么、什么时候说、用哪个后端」。

| 方法 | 用途 |
|---|---|
| `providers()` | 后端列表（`id/label/online/fields/available/reason`）；缺依赖或许可证时 `available=False` |
| `preflight(provider="")` | 无网预检：解析后端链、把失效音色换默认，返回最终 provider id |
| `synthesize(request)` | **阻塞**合成（缓存命中直接返回）；预缓存放后台线程时用 |
| `speak(request, on_done=, on_error=)` | 后台合成 + 共享音频通道播放，返回 token |
| `stop(token="")` / `playing()` | 取消尚未开始的播放 / 是否在播 |
| `set_secret(provider, field, value)` / `secret_status(provider)` | 密钥只进钥匙串（owner `speech.<provider>`） |

`SpeechRequest` 字段：`text/provider/voice/rate/pitch/volume/cache_key`。
`provider` 留空 = core 配置的 `speech_backend`；`cache_key` 留空 = core 按 provider +
音色生成（**换音色必须换缓存键**）。

错误：`SpeechError.reason ∈ unavailable/network/timeout/auth/rate_limit/protocol/cache`。
界面只读 reason，不解析供应商异常文本。

`petcore --check` 的 `speech` 字段（backend/fallback/cache_dir/providers）是
「冻结包是否真的带 edge-tts/winrt/pycaw」的第一证据；provider 缺依赖或许可证时
会写进 `reason`。

### 动画循环

唱歌/报时这类「持续一段时间的动作」用：

```python
ports.presentation.play_animation("悠闲哼歌", loop=True)   # 一直播
ports.presentation.release_loop()                          # 当前这圈播完回行为链
```

探头、击飞、拖拽接管时 Core 会自动解除循环；`play_animation` 返回 `False` 表示角色
没有这个素材，mod 应安静降级（不要自己找替代素材名）。

### 参考实现

- `examples/petcore-mods/voice.chime` 1.1.0：日程/台词/预缓存/免打扰 + 设置页（含四家后端与密钥状态）。
- `examples/petcore-mods/media.music` 1.2.0：三源识别（SMTC / 音频会话 / 播放器窗口标题）+ 歌词（三源打分，`HostPorts.http`）+ 唱跳循环 + 播放器启动。

## 13. 卸载、分组与音乐识别（P4-b2，2026-10-23）

### 13.1 卸载

- **顺序**：先撤贡献（`FeatureManager.stop_owner` → `menu_model.unregister_owner` →
  清 `sys.modules` 中位于包目录下的模块），再删文件，最后 `reload_mods()`。
- **删除口径**：
  - 用户导入包有 `install.json`：按 `files` 清单逐文件删除；`__pycache__`/`*.pyc`
    视为生成物一并删；**未登记文件保留**并在消息里报数量；
  - 安装器投放包（无 `install.json`，带 `.trusted`）：整目录删除，仍校验
    `mods_root/<id>` 边界与 `mod.json` 存在；
  - 个人数据（`feature-data/<id>`、配置键、钥匙串）一律不动。
- **状态**：卸载会清 `mods-state.json` 的 `disabled` 条目，重装不继承停用。
- **UI**：`.trusted` 包也能在「扩展管理 → 加装扩展」卸载；删除前询问未保存的扩展设置页
  （保存 / 放弃 / 取消）。安装器卸载时 `[UninstallDelete]` 清 `{app}\mods`。

### 13.2 设置页与菜单分组

- mod 设置页：`self.group_heading("<name_zh>")` 一个 H2，功能组用
  `self.section("…", level=3)` 作为 H3；Core 内嵌页（`host_page`）保持原 H2 分组。
- 右键「扩展」：`MenuModel._populate_extensions()` 按 `owner` 聚合，每个 mod 一个
  二级子菜单；`CORE_FEATURE_IDS` 不进入该菜单。单菜单项的 mod 也建子菜单。
  `extensions_hub.children` **只表达可见性**，不许平铺渲染；旧 layout 里被
  `_append_mod_actions()` 塞进 hub 的 Core 项（links / 快捷启动 / 网络信息 /
  回收内存 / 免打扰）由 `migrate_p10_menu()` 幂等搬回模板位置。

### 13.3 音乐识别：三源判定与识别范围

> 2026-10-26 重写（`media.music` 1.2.0）。旧版只有 SMTC 一条源，下面这张表是
> **唯一判定真源**；改判定必须先改这里与 `host/recognizer.py` 的用例。

**三个来源各管一件事**

| 来源 | 模块 | 回答什么 | 何时采 |
|---|---|---|---|
| 系统媒体会话（SMTC） | `host/now_playing.py` | 在放哪一首、时间轴、传输控制 | 每秒恒定 |
| WASAPI 音频会话 | `host/audio_sessions.py` | **这个进程是否真的在出声**（Active / 静音 / 峰值） | 每秒恒定（实测 ~2ms） |
| 播放器窗口标题 | `host/window_source.py` + `host/title_clean.py` | 不注册 SMTC 的播放器**在放哪一首** | 仅当 SMTC 没有"在播"会话 |

**判定表**

| 证据组合 | 判定 |
|---|---|
| SMTC 有被接受会话 + 该进程音频 Active 且未静音 | 在播 · 来源=SMTC · 置信度=有音频证据 |
| SMTC 有被接受会话 + 该进程音频 Inactive / 被静音 | 暂停（曲目与歌词保留，宽限后解除唱歌循环） |
| SMTC 有被接受会话 + 该进程**不在**音频会话表（蓝牙 / 独占模式 / 虚拟声卡） | 按 SMTC 的播放状态判，置信度=按系统状态推断 |
| SMTC 有被接受会话但**处于暂停** + 另一台已登记播放器窗口可解析且其音频 Active | 在播 · 来源=窗口标题（「在播」的证据优先于「暂停」的证据）；理由里写明"系统媒体会话处于暂停，以窗口标题源为准" |
| SMTC 无被接受会话 + 已登记播放器窗口标题可解析 + 该进程音频 Active | 在播 · 来源=窗口标题 |
| SMTC 无被接受会话 + 已登记播放器窗口标题可由进程但音频不活跃 | 暂停（开着没在放时标题停在上一首，不许当在播） |
| SMTC 无被接受会话 + 已登记播放器进程音频 Active、标题解析不出歌名 | 在播但无曲目（只唱歌、不取词；气泡对**同一台播放器**只说明一次「这首歌我认不出来」） |
| 只有任意进程（游戏 / 语音 / 未知应用）音频 Active | **不在播**（不猜） |
| SMTC 采样抛错 / 超时 | 读失败：保留上一拍状态，不清歌词、不计缺席 |

- **识别范围**（`music_filter_enabled`，默认 false）：开启后只放行识别名命中集合的
  会话，**对三个来源统一生效**；识别名来源是 `music_player_paths` 的 exe 文件名
  （去 `.exe`、小写）+ `music_filter_extra_ids`（逗号/顿号分隔，AUMID 与 exe 名
  不一致时用）。被挡下时 `reasons` 必须**点名播放器**，不许静默为空。
- **否决表**（浏览器默认关闭 / 系统提示类应用）仍然生效，且是第二道闸。
- **app_id 归一必须先取 basename**：有些 Win32 播放器把 app_id 报成完整安装路径，
  只做 `endswith(".exe")` 会让"静音 / Inactive"这条证据永远匹配不上，还会把安装路径
  带进气泡与诊断文本（`recognizer.exe_name` / `normalized_exe`）。
- **窗口标题源允许的进程** = `host/players.py:KNOWN_MUSIC_EXES`（foobar2000 / AIMP /
  Winamp / MusicBee / iTunes + 四家已支持启动的客户端）∪ 用户配置的路径 ∪ 额外识别名。
  **故意不含 VLC / mpv / PotPlayer**：那些以放视频为主，收进来会把看视频当成听歌。
- **标题格式串**：`music_title_format`（默认空）只给窗口标题源用；空 = 不猜歌名/歌手
  顺序（整条同时作歌名与歌手参与搜索，显示只显示一次）。旧壳的
  `foobar_title_format` 由 `config._LEGACY_KEY_ALIASES` 迁移过来。
- **诊断**：设置页「识别诊断」与 `scripts/music_probe.py` 用同一份
  `host/diagnostics.py` 渲染三源采样 + 判定 + 理由；只含 app_id / 可执行名 / 曲名 /
  状态与数值，不含 URL、路径、代理与歌词正文。
- **音乐识别的开关**：`music_enabled`（总开关）、`music_sing_enabled`（唱歌动画）、
  `music_lyric_enabled`（歌词）、`music_browser_media_enabled`（浏览器会话参与）。
  音频会话与窗口标题源**没有独立开关**：它们是可由应用稳定推断的实现参数，
  不可用（非 Windows / 缺组件）时自动降级为只看 SMTC。
- **取词与采样节拍**（2026-10-29）：取词只有一条常驻 `lyric-fetch` 线程（单槽队列，
  新请求取消在飞请求，并发 ≤1），失败按**歌名**记 60s 负缓存；SMTC 管理器与会话列表
  按线程缓存（列表 2s 刷新一次），暂停时整桌面窗口枚举 ≥2s 一次；`music_enabled`
  关闭时不唤醒采样线程。改采样/取词路径前先看 `host/runtime.py` 的
  `LyricFetchWorker` 与 `host/now_playing.py` 的 `_ThreadCache`。


## 14. P4-c1 端口扩展：窗口采样与配置写入（2026-10-23）

### 14.1 `ScreenPort` 的窗口采样

```python
class ScreenPort(Protocol):
    def capture(self, *, pet_slot=None) -> ScreenCapture: ...
    def capture_region(self, *, pet_slot=None, ai_available=True) -> RegionPick: ...
    def foreground_app(self) -> str: ...
    def foreground_window(self) -> WindowInfo | None: ...
    def sample_foreground(self, *, expected_hwnd: int = 0) -> WindowSample | None: ...
    def capture_foreground(self, *, expected_hwnd: int = 0) -> ScreenCapture | None: ...
    def shrink_for_vision(self, png: bytes, *, max_edge: int = 1024) -> bytes: ...
```

- `WindowInfo(hwnd, process, title, rect)`：只读元数据，`rect` 是屏幕物理像素
  `(x, y, w, h)`，取不到时为 `None`。
- `WindowSample(hwnd, dh_hash, width, height, process, title, rect)`：只要「画面变了
  没有」时的轻量采样，**没有 PNG**。实现上缩到 192 边再哈希，实测比整抓省 30 ms。
- `capture_foreground()`：返回窗口画面（内存 PNG，长边 ≤768）与 `dh_hash`；
  `expected_hwnd` 非 0 时在抓图前后各复核一次前台窗口，切走了返回 `None`。最小化、
  cloaked 的 UWP 窗口、无可用矩形、抓图失败同样返回 `None`。**不写磁盘、日志不记
  标题正文**。
- Windows 上窗口抓图走 GDI `BitBlt`/`StretchBlt` 按物理矩形直接抓；mod 不需要、
  也不允许自己做 DPI 换算。`ScreenCapture` 的 `hwnd/dh_hash` 有默认值，旧代码按
  位置构造不受影响。
- `shrink_for_vision()`：把要发给多模态模型的 PNG 按**物理像素**长边封顶
  （默认 `VISION_MAX_EDGE = 1024`），保证同一内容在 100%/150%/200% 缩放下像素预算
  一致。空数据/解码失败/本来不超限时原样返回。**本地 OCR 与「复制图像」必须用
  原图**，只有模型输入这一步调它。
- 能力仍是 `screenshot.capture`（窗口采样不额外要能力）；能力是声明，不是沙箱。

### 14.2 `SystemPort.idle_seconds()`

```python
class SystemPort(Protocol):
    def screens(self) -> tuple[Any, ...]: ...
    def platform_name(self) -> str: ...
    def idle_seconds(self) -> float: ...
```

`idle_seconds()` = 距离最后一次键鼠输入的秒数；非 Windows 或读取失败返回 0
（= 不判定闲置）。`build_ports()` 现在会装配 `SystemAdapter`。

### 14.3.1 扩展偏好键落在 `extra` 区块（2026-10-26 起）

`config.json` 顶层只允许 `petcore.config.DEFAULTS` 里的键——这是「配置里永不出现
API Key」的机制。第三方扩展的偏好键**照旧用** `host.config.set(key, value)`，
但它们会落进受管的 `extra` 区块，`get/set` 对调用方透明：

```json
{ "schema": 1, "...": "...", "extra": { "mymod.enabled": true } }
```

约束（越界即被丢弃并记 warning）：键名匹配 `^[a-z0-9][a-z0-9._-]{0,127}$`；
单值序列化后 ≤4KB、整块 ≤64KB；命中密钥样式（`sk-…`、`Bearer …`、32 位以上十六进制）
的值**拒绝写入**。大块状态仍请用 `storage.data_dir(owner)` 自建版本化 JSON。

### 14.3.2 `.trusted` 只在安装目录生效（2026-10-26 起）

`mods.install.install()` 在落盘前剥掉包里自带的 `.trusted`，且
`trust.is_trusted()` 只认 `{app}\mods`（`paths.install_mods_dir()`）下的标记。
也就是说：**包内容不能宣布自己被信任**；本地导入的 feature 包必须靠全局
`allow_feature_mods` 或安装器投放的标记。

### 14.3 `ConfigPort.set()`

```python
class ConfigPort(Protocol):
    def get(self, key, default=None): ...
    def set(self, key, value) -> None: ...
```

`set` **只给菜单勾选/命令切换**这类用户刚按下的一次性动作使用（Core 写全局配置
并立即落盘，与 Core 菜单项语义一致）；运行时不许把它当状态存储反复写，设置页仍走
Core 托管的 `host.config`（ScopedConfig，支持实时预览与取消回滚）。

### 14.4 参考实现：`ai.chat` 的主动识屏

- `host/proactive.py`：`match_whitelist()` / `dwell_satisfied()` / `ProactiveLimiter`
  （状态文件 `storage.data_dir("ai.chat")/proactive_state.json`）/ `ProactiveWatcher`
  （`scheduler.call_every(8000)`，配置每 tick 现读；稳态用 `sample_foreground()`，
  变化后才 `capture_foreground()`）。
- 命令：`proactive_toggle`；菜单贡献 `id=proactive`（`checked` 读配置）；
  模板节点 id 为 `ai.chat:proactive`。
- 全文行为口径见 SPEC §26。

## 15. 联动类 mod 的接缝（`ai.agent` 参考实现）

联动（“Agent 干活时桌宠跟着动”）不需要 Core 认识任何具体 Agent。Core 只提供三个
通用端口，剩下全在 mod 里：

| 端口 | 用途 |
|---|---|
| `StoragePort.data_root()` | 定位事件目录：`<data_root>/agent-events/`、`<data_root>.parent/dsh-pet-bridge/` |
| `PresentationPort.visible()` | 隐藏态不切动画（零功耗语义，同主分支） |
| `PetHandle.animations()` | 取当前角色的 act 名，联动动作池按名字挑、缺名按关键词回退 |

### 15.1 事件文件契约

```
<data_root>/agent-events/<agent>.jsonl          # 任何 Agent 都能追加
<data_root>.parent/dsh-pet-bridge/dsh*.jsonl    # DSH 桥接插件（多实例各一份）
```

每行一个 JSON 对象；字段见 [`AGENT_LINK_PROTOCOL.md`](AGENT_LINK_PROTOCOL.md) §2。
`state` 优先，`event` 次之，表外事件忽略；`inst` 用于多写者分片。

### 15.2 参考实现：`examples/petcore-mods/ai.agent/host/`

- `protocol.py`：`parse_line()` / `normalize_state()` / `tool_label()` /
  `activity_text()`，纯解析、无 Qt。
- `tailer.py`：`ByteTailer`（字节偏移、半行缓冲、轮转重读、单次 64KB）与
  `GlobTailer`（`dsh*.jsonl` 多文件、按 mtime 最多 8 个）。
- `links.py`：`LinkEngine`，六态 → 动作/气泡/音效；时钟与延后调用可注入，
  可脱离事件循环测试。
- `runtime.py`：`AgentLinkRuntime`（`start/stop/close`），唯一轮询线程 1.5 s，
  配置每轮现读；解析结果经 `scheduler.call_later(0, …)` 回 GUI 线程。
- `settings_page.py`：一个 H2 + 五个 H3；控件改动即时写全局配置，`discard_changes()`
  从打开时快照回滚。

### 15.3 写作者红线

- 只读事件文件，**不写**任何 Agent 配置、不注入 hooks。
- 单条事件/单行超限即丢弃；坏 JSON、未知事件名静默忽略。
- 日志只记 agent / 状态 / 文件名，不记事件正文。
- 运行时（`runtime/links/tailer/protocol`）不 import PySide6；只有设置页可以碰 Qt。

## 16. P4-c3 提醒与播放生命周期端口（API 1 增量）

需要这些端口的扩展声明 `core_requires >=0.2.0,<1.0.0`；Core0.2.0随提醒扩展批次启用。
不要直接导入旧 `pet` 业务或把定时提醒规则写入Core。

- `ports.pets.pets()`枚举存活宠物，`PetHandle.config.get()`读取对应配置；运行状态写入`storage.data_dir(owner)`的版本化数据文件，不写偏好配置。
- `ports.presentation.for_slot(slot)`在创建时绑定目标；目标关闭后返回未展示，不会转到另一宠物。`say()`/`notify()`返回真实交付是否成功；通知支持`on_click`，桌面系统可能不提供通知逐条ID。
- 自动提醒用`ports.reminders.deliver(owner, slot, key, target, title, text, guard=..., on_click=...)`；结果为`bubble`、`notification`、`queued`或`unavailable`。静默期间有界暂存、恢复后每宠一份跨扩展摘要，不补播语音。队列是进程内暂存，不保证崩溃重启后的交付。
- `speech.speak(request, owner=..., slot=..., guard=..., on_done=..., on_finished=..., on_error=...)`返回token。`on_done`保持旧的合成完成/已请求播放语义；`on_finished`只在真实媒体播放结束触发。不能以`on_done`调度揭签等需要播放完成的动作。
- `speech.stop(token)`、`cancel_owner(owner)`、`cancel_slot(slot)`分别撤销作业；取消阻塞合成不强杀线程，保留其所有权直至真正返回，避免并发替代线程。
- `speech.register_yield(owner, predicate)`返回撤销函数；`speech.should_yield(moment, requester=owner)`在发声前询问规则。规则必须按当前日程计算，不能依赖其他定时器是否已经执行。
- `guard`与延迟回调必须绑定本runtime和目标宠物的生命周期；停用扩展/关闭宠物/退出统一取消交付与语音，mod自身仍负责取消自己的调度器句柄和交互订阅。

实现与本批证据见[L058](PETCORE-LOG.md)及[B审查报告](PR-REPORT-PETCORE-P4C3-B-2026-10-24.md)。


## 17. 提醒域 v2 数据与设置契约（Core 0.2.0 / API 1）

| 扩展 | 版本 | 设置归属/默认 | 独立运行数据 |
|---|---|---|---|
| reminder.todo | 1.1.0 | 此宠物：`todo_reminder_enabled=true`、`todo_reminder_lead_minutes=5`（0–60）、`todo_reminder_speak=false` | `todo-slot-N-v2.json`；首次迁移另留`todo-slot-N-legacy-v1.json` |
| fun.festival | 1.1.0 | 此宠物：手动`festival_enabled=true`、自动`festival_reminder_enabled=false`、`festival_reminder_speak=false`、`festival_birthday=""` | `festival-slot-N-v2.json`；日高水位/已触发时点 |
| fun.divination | 1.1.0 | 此宠物：`divination_enabled=true`、`divination_speak=false`、`divination_hint=true` | 进程共享`daily-v2.json`；旧`daily.json`原件与`daily-legacy-v1.json`备份保留 |
| voice.chime | 1.1.1 | 保持既有报时设置归属/默认 | 仅适配共享语音与节日让位 |

数据在`ports.storage.data_dir(owner)`下，文件内部`schema_version=2`（包清单则为`schema=1`，市场索引为`schema_version=1`），不能把包清单schema与状态文件格式混用。原子写入采用同目录临时文件、flush/fsync/os.replace；失败保留原文件和内存状态，损坏文件不被悄悄覆盖。新数据不覆盖旧格式，回滚程序不删除v2；旧程序看不到新日程/轮次，不能保证新旧版本同时运行时的相互同步。

### 设置准入与交互

三页均属于扩展管理页内导航，不新增侧边栏域。稳定前缀`todo_*` / `festival_*` / `divination_*`，中英文搜索别名由各页`keywords()`和行文案提供，所有新增设置均是本宠物偏好而非一次性状态。Windows/macOS/Linux无不同业务开关，外部语音/系统通知按宿主能力降级；本轮只验证Windows。

节日分类`festival_reminder_cn/solar_terms/west`默认true；模式`festival_reminder_mode=times`，次数`festival_reminder_count=2`（1–6），自定义`festival_reminder_times=09:00`；文案`festival_reminder_show_quote=true`，两类`festival_custom_quotes_cn/west`为空。自动关闭时依赖控件禁用；次数/自定义时点互斥。生日严格MM-DD，只存月日；02-29不在平年挪到其他日期，独立于分类、仍受自动总开关。无未接通的动画项。

修改即时预览，保存/X落盘，取消/Esc恢复打开时偏好；运行数据不在设置快照内。缺失键按安全默认值恢复，旧手动开关不升级为自动授权。界面复用SettingsPage/SettingsCard，窄内容使用纵向行与自动换行，控件有名字/说明/键盘路径。

### 调度与生命周期

待办按slot+ID+occurrence+lead/due高水位去重；每日跨午夜提前、10分钟宽限、重启及回拨均不重放。一次性过宽限归档；过期条目不集中轰炸，旧无时间文本绝不自动推断日期。

节日沿用现有规则：启动已过首点且当天未提示时补一次，正式时点按当前分钟触发；不积压补发整天时点。生日复用此链。让位规则在报时发声前独立计算，不依赖两定时器先后。

48签按稳定ID/档位权重确定性抽取，一轮不重复；同日全宠共享，展示绑定实际触发slot。等待语音真实结束（不是on_done），再等2.8秒揭签；等待最多6秒，超时取消本token后继续文字。停用/关宠/退出取消任务和本owner语音；不撤销其他扩展语音。有界预热最多两请求，语音关闭不联网；已阻塞的provider调用只协作取消，返回前保留原线程、不启动替代线程。预热通过`petcore.mods.workers.try_start_worker`按owner进行进程级准入，实际卸载后创建新runtime也不能绕过活线程约束；取消Event仍属于每轮业务任务。

详见[C](PR-REPORT-PETCORE-P4C3-C-2026-10-24.md)、[D](PR-REPORT-PETCORE-P4C3-D-2026-10-24.md)、[E联合验收](PR-REPORT-PETCORE-P4C3-E-2026-10-24.md)。

## 18. P4-c3-D1 分发契约（2026-10-24，Core 0.2.0 / API 1）

本节是 T01 冻结的跨模块契约。实现分批落地（T02 传输、T03 解包、T04 授权/事务），
**契约本身不变**；改动签名必须同步本节、[市场文档](PETCORE-MARKET.md)与测试。

### 18.1 预算（`petcore/contracts/archive.py`）

| 项 | 值 | 作用范围 |
|---|---|---|
| `compressed_bytes` | 64 MiB | 市场 feature 包的下载字节，读流中持续计数 |
| `expanded_bytes` | 256 MiB | 解包后实际写入字节 |
| `entries` | 10 000 | ZIP 条目（文件与目录都计） |

- `ArchiveLimits` 不可变，三个字段都必须为正整数（`bool` 拒绝）；`allows()` 等于上限算通过。
- 越界抛 `BudgetExceeded(kind, limit, actual)`，`kind ∈ compressed / expanded / entries`，消息含实测值与上限。
- 只有市场通道用 `MARKET_ARCHIVE_LIMITS`；`mods.install` 的本地目录/ZIP 与角色/content 包保持零预算（`limits=None`）。

### 18.2 传输描述（`petcore/contracts/http.py`）

```text
TransferSpec(urls, expected_size=None, expected_sha256="", max_redirects=3,
             allowed_schemes=("https",), allowed_hosts=(), resume=RESUME_IF_MATCH, timeout=15.0)
TransferReport(url, bytes_read, path="", resumed_from=0, redirects=0)
```

- 默认只允许 `https`；市场下载器只对本机回环（`127.0.0.1` / `::1` / `localhost`）自动放开 `http`，其余主机降级一律拒绝。
- `allowed_hosts` 为空时**只允许同主机跳转**；GitHub Release 的 `github.com → objects.githubusercontent.com` 必须由调用方显式列出目标主机（T02 起有回归锁定）。
- `RESUME_IF_MATCH` 的语义：只有 206 且 `Content-Range` 起点等于本地长度才追加，否则丢弃 `.part` 重下；416 只有在本地长度等于 `expected_size` 或 `bytes */N` 的 N 等于本地长度时才算完成，否则删 `.part` 重下一次（第二次仍 416 即按状态错误失败）。
- 压缩预算在**读流中**按实际读到的字节累计（含已有 `.part`），超限抛 `BudgetExceeded` 并删除 `.part`，不信 `Content-Length`。
- 大小与 SHA-256 的最终判定属于 `petcore/market/verify.py`；传输层只用 `expected_size` 判断续传是否对齐。
- `resumed_from` 只在服务端区间与本地 `.part` 对齐时非零；续传失败后重下为 0。取消时 `.part` **保留**（可续传）。

### 18.3 来源与授信（`petcore/contracts/market.py`）

`PackageOrigin ∈ {market, local-dir, local-zip, bundled}`；`writes_trusted_marker()` 只对 `bundled` 为真。

- 市场/本地安装包内自带的 `.trusted` 一律剔除（防标记走私），成功消息点名「已移除包内 .trusted」。
- `.trusted` 只由安装向导写；`allow_feature_mods` 是独立授权开关，两者不互相替代。

### 18.4 冻结签名

| 位置 | 签名 | 实现批次 |
|---|---|---|
| `petcore/net/market_transport.py` | `fetch_artifact(spec, dest_dir, *, limits=None, progress=None, cancel=None) -> TransferReport` | T02（已落地：逐跳/读流预算） |
| `petcore/mods/install.py` | `open_pack(source, work_root=None, *, limits=None)`、`install(source, root, *, force=False, limits=None)` | T03（已落地：预检 + 计数写入） |
| `petcore/market/install.py` | `install_package(..., allow_feature=False, origin=PackageOrigin.MARKET, limits=MARKET_ARCHIVE_LIMITS, ...)` | T04（已落地：来源授信、交叉校验、锁失败回滚） |

worker 只依赖这些签名，不改契约；主模型改契约必须同批更新本节与测试。
