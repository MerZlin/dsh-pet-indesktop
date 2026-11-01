# PR 报告：对话窗深色模式 + `chat_view` 插件缝 + 网页 AI v2（2026-11-01）

> **基线**：`814fa83d`（上一批：标题栏最大化 / 审批卡卡死 / CPU）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-11-01
> **范围**：33 个文件（实现 18、测试 3、文档 7、打包 3、示例包 6，含 2 个删除）
> **关联**：`docs/PETCORE-LOG.md` L153、`docs/PETCORE-MOD-FORMAT.md` §5.2

## 一、核心特性

两件事，一件是产品功能，一件是架构能力：

1. **AI 对话窗的深色模式**：标题栏多一个圆形按钮（月亮/太阳），点一下整扇窗变深——
   侧栏、标题、消息气泡、输入区、DSH 视图一起变；`chat_dark_mode` 持久化，设置页
   「对话窗外观 → 深色模式」是同一个开关。
2. **`chat_view` 插件缝 + 网页 AI 重写**：扩展现在可以在 AI 对话窗里**拥有一整页**
   （`Contribution(kind="chat_view")` + `view_factories`），窗口 chrome、标题栏按钮、
   圆角、airspace、深色协调与生命周期都由 core 负责。`tool.ai-browser` 从"另开一个
   QWebEngine 窗口"重写成 v2：网页版 AI 直接内嵌在对话窗里，标题栏「网页 AI」按钮
   切换，菜单「AI 网页」等价于 `chat.show_view(...)`。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 深色模式按钮 | `#window-dark-button`，checkable，图标随状态换月亮/太阳 |
| 2 | 深色 QSS | `styles.DARK_OVERRIDES` 追加在浅色表之后（浅色路径字节不变） |
| 3 | `chat_view` 契约 | mod 交 QWidget + 四个可选生命周期方法 |
| 4 | 标题栏视图按钮 | 内联最多 2 个，其余进 `»` 菜单；<880px 图标化 |
| 5 | 视图生命周期 | `activate/deactivate/set_dark/shutdown`，GUI 线程、异常隔离 |
| 6 | `ChatPort.show_view` | 复用用户正在用的那扇对话窗，不另开 |
| 7 | 网页 AI v2 | QtWebView + Edge WebView2，8 站点 + 自定义站点 |
| 8 | 打包 | 显式收集 `Qt6WebView.dll` + `qtwebview_webview2.dll`（+0.31 MB） |
| 9 | 给 AI 的开发说明 | `PETCORE-MOD-AI-GUIDE.md`（自包含 + 可粘贴提示词） |

**红线 / 不变量**

- `view_changed` 仍**只发 ai/dsh**（它驱动会话通道路由）；扩展视图切页不发这条信号。
- 浅色外观**一个字节都不能变**：深色是追加表，默认 `dark=False` 的路径与原实现等价。
- 扩展视图**不许**带走对话窗：工厂抛异常/返回非 QWidget → 错误占位，窗口照常可用。
- 对话窗**不做**扩展视图的默认页：每次开窗回到 AI 对话。
- 用户数据目录里的旧 `tool.ai-browser` 不会被 setup 覆盖（文档里写明手工替换）。

## 二、修改文件说明

### 实现（core）

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/contracts/contributions.py` | +27 / −0 | `CONTRIBUTION_KINDS` 加 `chat_view`；新增 `ChatViewSpec`（id/label/factory 校验） |
| `petcore/contracts/feature.py` | +5 / −1 | `FeatureDefinition.view_factories`（键=contribution id） |
| `petcore/contracts/chat.py` | +8 / −0 | `ChatPort.show_view(owner, view_id, pet_slot=)` 契约 |
| `petcore/mods/feature.py` | +47 / −1 | `FeatureManager.chat_views()`：label/工厂缺失跳过并告警、order 排序、`owner:id` 去重 |
| `petcore/ui/chat/window.py` | +317 / −14 | 动态视图页（懒建、`#chat-view-host` 12px 内缩、错误占位）、标题栏视图按钮 + 溢出菜单、深色按钮与 `set_dark`、图标换墨、抽屉 airspace 隐藏、`deactivate/shutdown`、`view_activated` 信号 |
| `petcore/ui/chat/controller.py` | +84 / −0 | `show_view` + `_recent_state`（复用正在用的窗口）、`dark_toggled` 落盘、`apply_appearance(dark=)`、新建/切换会话强制回 AI 页、`views/dark` 建窗参数 |
| `petcore/ui/chat/styles.py` | +125 / −2 | `DARK_OVERRIDES`（60+ 选择器，覆盖侧栏/标题/气泡/输入区/DSH/滚动条/视图槽位）、`modern_stylesheet(dark=)`；浅色表未改 |
| `petcore/ui/chat/themes.py` | +27 / −1 | `build_modern_custom_overlay_qss(..., dark=)` 深色半透明面板 |
| `petcore/ui/chat/icons.py` | +27 / −0 | `globe` / `moon` / `sun` 三个 16 单位线稿 |
| `petcore/ui/chat/dsh_view.py` | +1 / −2 | 等宽正文去掉写死的 `#4a5260`，改吃 QSS（深色下可读） |
| `petcore/config.py` | +3 / −0 | `chat_dark_mode`（GLOBAL_CONFIG_KEYS + DEFAULTS + normalize） |
| `petcore/__init__.py` | +1 / −1 | 0.3.0 → 0.4.0（新增公开 API：`chat_view` / `show_view`） |
| `petcore/AGENTS.md` | +11 / −0 | 写死 `chat_view` 与深色模式的两条不变量 + AI 指南指针 |

### 实现（示例扩展 `tool.ai-browser` v2）

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `host/web_pane.py` | 新增 460 行 | QtWebView 面板：多站点 `QStackedWidget`、工具条、弹窗 shim、缺口页（下载/重试）、`activate/deactivate/set_dark/shutdown`、`WEBVIEW2_USER_DATA_FOLDER` |
| `host/engine.py` | +126 / −14 | 重写探针：`platform_supported` / `engine_available`（QtWebView）/ `runtime_version`（注册表 + 目录）/ `missing_reason` / `ensure_user_data` |
| `host/sites.py` | +67 / −2 | `parse_custom_sites`（`名称 | URL`，非 http/https 丢弃）、`all_sites` |
| `host/factory.py` | +49 / −23 | 声明 menu / settings_page / **chat_view** + `view_factories`；宿主无 QtWebView 时不贡献视图 |
| `host/runtime.py` | +25 / −61 | 只留 `commands={"open": …}` → `chat.show_view`；删掉 Edge/系统浏览器降级链 |
| `host/settings_page.py` | +63 / −81 | 默认站点 + 自定义站点 + 内核状态；去掉「启用/额外域名」 |
| `host/browser_window.py` | 删除 439 行 | 不再自建顶层浏览器窗口 |
| `host/external.py` | 删除 83 行 | 不再需要 Edge/系统浏览器兜底 |
| `mod.json` | +7 / −5 | 2.0.0；`core_requires >=0.4.0`；capabilities 精简 |

### 其它

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `examples/petcore-mods/ai.chat/host/settings_page.py` | +11 / −0 | 「对话窗外观」加「深色模式」行；`apply_preview(dark=)`；`_on_changed` 落盘 |
| `packaging/petcore.iss` | +56 / −1 | `WebView2Present()` + `UpdateReadyMemo` 提示（不阻塞安装）；更新 `mod_aibrowser` 任务描述 |
| `scripts/build_petcore.ps1` | +15 / −0 | 显式带 `--hidden-import PySide6.QtWebView` + `Qt6WebView.dll` + `qtwebview_webview2.dll`（本仓真正的打包入口） |
| `scripts/build_onedir.ps1` | +19 / −1 | 同上（主分支那条构建链，口径保持一致） |
| `scripts/slim_bundle.py` | +5 / −0 | `REQUIRED_GLOBS` 复核这两个文件 |
| `examples/petcore-mods/README.md` | +1 / −1 | 条目改成 v2 的描述 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_chat_views.py` | 新增 340 行 | 按钮/顺序/懒建/未知 id/工厂异常/非 QWidget/生命周期/`view_changed` 不误发/溢出菜单/深色扇出/收集器排序/`show_view` 复用窗口/`chat_dark_mode` 落盘 |
| `tests/test_petcore_ai_browser_mod.py` | +153 / −41 | 站点表、自定义站点解析（含 `javascript:` 拒绝）、清单与 `chat_view` 贡献（按宿主能力分支）、Qt-free 模块、懒加载、`missing_reason` 分层、`show_view` 四条路径 |
| `tests/test_petcore_ai_mod.py` | +25 / −0 | 深色 checkbox → `chat_dark_mode` + `apply_preview(dark=True)` + 设置搜索命中 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `docs/PETCORE-MOD-AI-GUIDE.md` | 新增 300 行 | **给 AI 的自包含说明书**：结构/manifest/三种接线点/端口白名单/线程与 Qt 边界/排错表/可粘贴提示词 |
| `docs/PETCORE-MOD-FORMAT.md` | +63 / −2 | §5.2 `chat_view` 契约（时序表、可选方法、12px 内缩、不做默认页、不上色）；§7 端口白名单与「有 QtWebView、没有 QtWebEngine」 |
| `docs/PETCORE-MOD-DEVELOPMENT.md` | +71 / −15 | §4 接线点表加 `chat_view`；新增 §4.2 最小骨架；§4.1 边界改写；checklist 10.1 |
| `docs/SETTINGS-CHANGE-GATES.md` | +1 / −0 | 登记 `chat_dark_mode` |
| `docs/PETCORE-LOG.md` / `-INDEX.md` / `INDEX.md` | +67 / +1 / +2 | L153 条目、索引行、AI 指南入场 |

## 三、性能分析

**测量环境**：本机 16 逻辑核，Windows，PySide6 6.12.0，Edge WebView2 `156.0.4314.50`；
`QT_QPA_PLATFORM=windows`（真实窗口，不是 offscreen）。CPU 口径与
`scripts/bench_petcore_idle_cpu.py` 一致：**进程树 CPU 秒 / 墙钟 / 逻辑核数 × 100**
（任务管理器同口径），进程树包含 `msedgewebview2.exe`。

**稳态开销（网页 AI，25s/段，脚本 `.scratch/bench_webview_idle.py`）**

| 阶段 | 总 CPU% | 单核% | 峰值总% | 子进程 | webview 进程 |
|---|---|---|---|---|---|
| 从未打开网页页 | 0.066 | 1.051 | 0.098 | 0 | 0 |
| 打开 DeepSeek 后静置 | 1.283 | 20.534 | 1.280 | 7 | 7 |
| 关窗后（`shutdown()` → about:blank） | 0.093 | 1.483 | 0.098 | 7 | 7 |

- **没打开过 = 零成本**：`web_pane` 模块与 `PySide6.QtWebView` 都不被 import
  （`tests/test_petcore_ai_browser_mod.py::test_view_module_is_never_imported_at_load_time`），
  视图工厂只在第一次切到该页时调用；`children=0` 也证实没有 WebView2 进程。
- **打开着**：1.28% 总 CPU / 20.5% 单核。这是 DeepSeek 页面自身的定时器与动画，
  不是我们的代码在跑；关掉该页（切回 AI）会调用 `deactivate()` 停加载。
- **关窗后回到 0.09%**（打开时的 1/14），但**7 个 `msedgewebview2.exe` 仍空闲驻留**——
  WebView2 的运行时在进程内是单例，真正的回收要等程序退出。这一点写进了 PR 的
  「未验证/风险」，没有拿 0.09% 冒充"完全回收"。

**新增路径成本与触发频率**

| 新路径 | 成本 | 触发频率 |
|---|---|---|
| `chat_views()` 收集 | 遍历已加载 feature 的 contributions（当前 < 20 个），微秒级 | 每次建对话窗一次、每次 `show_view` 一次 |
| 视图工厂 | 一次 Qt 控件构造（网页页首次约 300–800ms，含 WebView2 初始化） | 每扇对话窗、每视图一次 |
| `set_dark()` | 重建一次 QSS 字符串（约 12KB 拼接）+ 重绘 | 用户点按钮/设置变更 |
| 深色 QSS | 追加约 4KB 常量字符串 | 同上；浅色路径不拼接 |

**系统调用 / 网络 / 磁盘 / 线程 / 内存**

- 没有新增线程：WebView2 的进程是运行时自己拉起的，我们只在 GUI 线程建控件。
- 磁盘：WebView2 用户数据目录 `<data>/feature-data/tool.ai-browser/webview2`（登录态所在），
  只在第一次建视图时创建。
- 网络：只有网页自己发请求（用户主动打开站点），core 不发任何包。
- 内存：网页页驻留时主要是 Chromium 渲染进程（本机 7 个子进程）；关窗后进程不退出，
  内存仍占着——这是选择 WebView2 换体积（+0.31 MB vs QtWebEngine +203 MB）的代价，已写进风险。

**打包体积**

| 项 | 数值 |
|---|---|
| `Qt6WebView.dll` | 60 KB |
| `QtWebView.pyd` | 120 KB |
| `plugins/webview/qtwebview_webview2.dll` | 130 KB |
| 合计新增 | **≈ 0.31 MB**（对比：QtWebEngine 全套 203 MB + 会撞 slim 硬闸） |
| setup | 见 §四产物 |

## 四、实机运行记录

**1. 渲染 spike（先验证再写代码）**

`.scratch/webview_spike.py` 用与对话窗同款外形（`FramelessWindowHint|Window` +
`WA_TranslucentBackground` + 7px 外环 + 14px 圆角 shell + 12px 内缩）装 `QWebView`：

```
loaded local html
saved E:\Work\dsh-pet-0\.scratch\webview-spike.png (1350x930)
switching to https://chat.deepseek.com ...
saved E:\Work\dsh-pet-0\.scratch\webview-spike-remote.png (1350x930)
```

截图里 DeepSeek 登录页（含中文、微信扫码块）完整渲染在圆角窗内 → 方案成立。

**2. 真机 E2E（真 ChatWindow + 真 mod + 真 WebView2）**

`.scratch/web_e2e.py`：打开真实 `ChatController` 的对话窗 → `show_view("tool.ai-browser","web")`
→ 加载 DeepSeek → 截图 → 切回 AI → 开深色 → 关窗退出。

```
views: ['tool.ai-browser:web']
show_view: True current: tool.ai-browser:web
saved E:\Work\dsh-pet-0\.scratch\web-e2e.png (1470x1080)
back to: ai
saved E:\Work\dsh-pet-0\.scratch\web-e2e-ai.png (1470x1080)
saved E:\Work\dsh-pet-0\.scratch\web-e2e-dark.png (1470x1080)
dark: True view: tool.ai-browser:web
closing window
quitting
exit 0 after 16.0s
```

截图确认：标题栏顺序 `DSH | 网页 AI(选中) | 侧栏 | 月亮 | 置顶 | 最小化 | 最大化 | 关闭`；
网页页与侧栏共存；深色下侧栏/标题/工具条一起变深、太阳图标点亮；正常退出（无挂死）。

**3. CPU 三态**：见 §三表格（脚本输出原文在 `.scratch/webview-idle.json`）。

**4. 全量门禁**

```
QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py -q
2811 passed, 1 skipped in 258.99s
ruff check pet/ tests/ petcore/ integrations/ examples/ scripts/   → All checks passed!
```

**5. 文档可用性实测（两轮，独立子代理）**

- 第一轮：新子代理只读 `PETCORE-MOD-AI-GUIDE.md` 产出 `my.sticky`（6 文件）→
  `load_manifest` 零 problem、`chat_views()` 收到 `my.sticky:sticky`、真对话窗里出现
  「便签」按钮与页面（`.scratch/doc-mod-check.png`）。它同时列出 **11 处语义空白**
  （最关键是「视图工厂怎么拿到 ports」），全部回写进文档。
- 第二轮（新子代理，仍只读该文档）：产出 `my.notes` → 同样通过校验、收集、渲染
  （`.scratch/doc-mod-check2.png`）；对「ports 从哪来」的回答直接引用了新补的 §2 段落，
  只剩 4 处细节需要猜（`config.get` 缺键语义、`add_row(expand=)` 含义等），已按它的反馈
  补了视图配色与生命周期时序。

**6. 产物**

- setup：`rel-chat-view-2026-11-01/dsh-pet-core-setup.exe`，301,224,113 B，
  SHA-256 `d7dd74c13fae71a5cb12f29f31d1fc734debeb2ced8af8370328fcdb2ef03582`（未签名）。
- onedir：`rel-chat-view-2026-11-01/dsh-pet-core/dsh-pet-core.exe`，15,868,552 B。
- `check.json`：version 0.4.0 / menu_template=json / strict.problems=[] / shenshen 106 动画 / daily 1200 / 配图 24/24。
- 包内确认：`_internal/PySide6/Qt6WebView.dll`、`_internal/PySide6/plugins/webview/qtwebview_webview2.dll`。

## 五、未验证 / 风险

- **macOS 的 WKWebView 后端没有验证**：`platform_supported()` 只在 Windows 返回真，
  其它平台不贡献 `chat_view`（不会出现点不开的按钮）。
- **WebView2 进程不随关窗回收**：关窗后 CPU 回到 0.09%，但 7 个 `msedgewebview2.exe`
  仍在（空闲）；要彻底回收得退出程序。
- **用户数据目录里的旧 `tool.ai-browser`**：setup 不覆盖 `%APPDATA%\dsh-pet-core\mods\`，
  升级后需要手工替换成 v2（或删掉让安装器那份生效）。发布说明里写了这一句。
- **安装器不自动装 WebView2**：只在「准备安装」页提示一句；应用内提供下载页 + 重试。
- **自定义站点不需要重开窗口**（`activate()` 会重建站点菜单），但**新装扩展**要让对话窗
  重新创建才能出现在标题栏——这是"建窗时收集"的直接结果，写进了契约文档。
- 截图/CPU 数据来自本机；不同站点（豆包/元宝）的稳态 CPU 未逐个测量，只测了 DeepSeek。
