# API 页新增「本地模型指南」只读子页 PR 报告

> **基线**：`f62b51aa`（工作区保留既有 L201/L202/feiyu 未提交改动）
> **分支**：当前工作分支　**日期**：2026-11-21
> **范围**：3 个实现文件（含 1 个新增）、1 个新增测试文件、5 份文档（含本报告）
> **关联**：[`PETCORE-LOG.md`](PETCORE-LOG.md)（`L203`）、[`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)、[`LOCAL-MODELS.md`](LOCAL-MODELS.md)

## 一、核心特性

在 **设置 → API 与账户**（`ApiSettingsPage`）内复用既有 `SettingsTabContainer` 增加第二个任务分页 **「本地模型指南」**（`local_guide`），把原有的服务管理表单收进第一个分页 **「服务配置」**（`services`）。用户可直接在页内查看本地对话模型（GGUF / `llama.cpp` / Ollama / LM Studio / vLLM）与本地语音生成模型（GPT-SoVITS `api_v2` / OpenAI 兼容 / 通用 JSON TTS）的启动命令、前置条件与桌宠逐项设置填法，也可一键复制整份带本机真实程序/数据路径的提示指南交给任意 AI 助手协同排障。

**红线 / 不变量**：
1. 零新增持久化配置键，`ApiSettingsPage` 原有表单、`dirty()`、`confirm_save()`、`discard_changes()` 与钥匙串语义保持不变；
2. 指南文案采用纯 Python 模块随包分发，运行时不读取 `docs/` 目录、不引入网络依赖；
3. 严禁出现外部 AI 工具品牌词与静态个人绝对路径，保持中立提示词口吻与运行时路径解析。

## 二、修改文件说明

> 注：本批实现/测试仅触及下表 4 个文件；工作区其余文件属既有 L201/L202/feiyu 改动，本批未触碰。

| 文件 | 增删（相对基线） | 改动意图 |
|---|---|---|
| `petcore/ui/settings/local_model_guide.py` | `[NEW]` 156 行 | 随包纯文本指南模板 `LOCAL_MODEL_GUIDE_TEMPLATE`、`LOCAL_MODEL_GUIDE_KEYWORDS` 与路径渲染函数 `render_local_model_guide()`（使用显式占位替换，不受示例代码大括号影响） |
| `petcore/ui/settings/page_api.py` | +108 / −11 | 将原服务表单收进 `tab("services", "服务配置")`（提取 `_build_services_panel()` 保持浅嵌套），新增 `tab("local_guide", "本地模型指南")`、只读引导框、一键复制按钮、状态提示、`keywords()` 与动态 `stack` 宽度策略 `_sync_tab_width_policy()` |
| `petcore/ui/settings/style.py` | +12 / −2（本批 +6 / −0） | 为 `QPlainTextEdit#apiLocalGuidePrompt` 增加内边距与聚焦蓝框 `:focus` 样式 |
| `tests/test_petcore_api_local_guide.py` | `[NEW]` 166 行 | 覆盖分页键名/标签、本机路径与启动关键参数完整性、一键复制剪贴板与零脏状态、搜索自动切页、720×500 窄窗无横向滚动及切回服务页按钮可达性 |
| `docs/SETTINGS-CHANGE-GATES.md` / `docs/INDEX.md` / `docs/PETCORE-LOG.md` / `docs/PETCORE-LOG-INDEX.md` | 同步追加 | 登记 `L203` 与设置变更记录、注册本报告 |
| `docs/PR-REPORT-API-LOCAL-MODEL-GUIDE-2026-11-21.md` | `[NEW]` | 本交付报告 |

## 三、实现要点

1. **窄窗分页宽度隔离**：`services` 分页的左侧服务列表 + 右侧表单天生较宽（720×500 需依赖横向滚动条保证「保存服务」可达，`AUD-26`）。若直接套用统一尺寸策略，隐藏的 `services` 表单会撑宽 `local_guide`。通过在 `currentKeyChanged` 钩子中按当前分页切换 `container.stack` 的水平 `QSizePolicy`（`services` 用 `MinimumExpanding`、`local_guide` 用 `Ignored`），做到两页各自满足 720×500 可达性与零横向溢出约束。
2. **模板渲染安全**：使用显式 `.replace()` 替代 `str.format()` 替换 4 个路径占位符，避免正文中出现 `{text, voice, speed}` 协议示例时触发 `KeyError`。

## 四、性能分析

**方法（可复现）**：`.\.venv\Scripts\python.exe -c "import time; from pathlib import Path; from petcore.ui.settings.local_model_guide import render_local_model_guide; ..."`
环境：Windows / Python 3.12.10，`N = 10,000` 次调用取均值：

| 指标 | 实测 | 归属 |
|---|---|---|
| `render_local_model_guide(...)`（渲染 4,601 字符） | **18.913 µs/call** | 构造 `ApiSettingsPage` 时单次触发 |

**结论**：
1. **稳态开销**：零新增定时器、轮询或常驻后台工作；桌宠正常运行期间不参与任何 tick 计算。
2. **新增路径成本与触发频率**：仅在用户打开设置弹窗构造 `ApiSettingsPage` 时执行一次约 `18.9 µs` 的字符串替换与控件构建；点击「复制指南给 AI」时执行一次剪贴板写入。
3. **系统调用 / 网络 / 线程 / 内存**：无网络请求、无磁盘读写、无新增线程；模块常驻内存仅为一个 4.6 KB 的只读字符串模板。

## 五、实机运行记录

1. **Windows 原生 Qt 字体设置弹窗实机渲染确认**（使用隔离临时目录模拟数据与用户主目录，关闭屏幕显示属性 `WA_DontShowOnScreen` 下抓图）：
   - `1100×760` 亮色模式（`api-local-guide-light-1100.png`）：`服务配置 | 本地模型指南` 顶部分页清晰，白底卡片、说明折行、带内边距只读文本框与蓝色「复制指南给 AI」按钮排版对齐，无多余空分隔行，`hbar_max = 0`。
   - `1100×760` 深色模式（`api-local-guide-dark-1100.png`）：深色底与文本框对比度清晰，点击复制按钮后左下角同步出现 `已复制 4685 个字符到剪贴板，可直接粘贴给 AI 协助启动与配置。` 提示，`hbar_max = 0`。
   - `720×500` 窄窗亮色模式（`api-local-guide-light-720.png`）：在最小窗口尺寸下 `hbar_max = 0`，控件和正文均完整收在卡片安全区内并自动折行，无文字裁切或溢出。
2. **无法自动验证的能力**：
   - macOS 与 Linux 原生字体和窗口管理器下该子页的布局效果未在对应宿主机运行验证。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.\.venv\Scripts\python.exe -m ruff check petcore tests` | `All checks passed!`（`exit 0`） |
| 格式检查 | `git diff --check` | 无报错输出（`exit 0`） |
| 聚焦 + 文档/架构门（沙盒） | `$env:NO_PROXY="127.0.0.1,localhost"; $env:QT_QPA_PLATFORM="offscreen"; .\.venv\Scripts\python.exe -m pytest -q tests/test_petcore_api_local_guide.py tests/test_petcore_api_presets.py tests/test_petcore_manual_page.py tests/test_petcore_docs.py tests/test_petcore_architecture.py tests/test_architecture.py tests/test_pr_report_discipline.py` | **`456 passed in 14.38s`**（`exit 0`）；最终文档回填后专项重跑 `test_pr_report_discipline.py` + `test_petcore_docs.py` + `test_petcore_architecture.py` + `test_architecture.py` 全绿 |
| 既有 API/设置窗门（非沙盒） | `$env:NO_PROXY="127.0.0.1,localhost"; $env:QT_QPA_PLATFORM="offscreen"; .\.venv\Scripts\python.exe -m pytest -q tests/test_petcore_api.py tests/test_petcore_settings.py tests/test_petcore_settings_coverage.py` | **`102 passed in 28.56s`**（`exit 0`；先在沙盒中复现 `GeneralPage` 读取主目录 `.dsh` 触发 `WinError 5`，随后按非沙盒规则跑通全部 102 例） |

> 注：本批仅为 `petcore/ui/settings/` 内的孤立只读 UI 子页与文案扩展，未改动持久化配置契约、数据迁移、生命周期或跨平台后端，因此未重跑约 12,000 例的遗留 `pet/` 全量套件（符合 `AGENTS.md` 聚焦验证规则）。
