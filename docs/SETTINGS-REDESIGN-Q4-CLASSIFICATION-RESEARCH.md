# 桌宠设置重构 Q4：侧栏分类调研

日期：2026-08-31
范围：设置的信息架构（IA）与跨平台归属，不讨论 Q5 视觉风格，不修改产品代码。

## 结论摘要

建议新版设置采用 7 个稳定侧栏入口：

1. 常规
2. 桌宠
3. 互动
4. 菜单
5. 灵动岛
6. AI 与对话（仅含 AI 的构建显示）
7. 联动

一级侧栏按用户要完成的任务分类，不按 Python 模块、配置文件结构或功能加入时间分类。每页再按“常用 → 依赖项 → 高级项”的顺序分组。Windows 专属的主动识屏放入稳定的“联动”页；macOS 的 Dock 图标保留在“常规 / 系统集成”；Linux 当前没有独有设置，不为未来能力预建空分类。

这会修正当前三个最明显的归属问题：

- DeepSeek 余额刷新和峰谷文案从“常规”移到“AI 与对话”。
- Agent 思考文案与提示音从“桌宠行为”移到“联动”。
- 快捷启动不再占用只有一个编辑器的一级页面，改放“菜单 / 快捷启动”。

## 研究方法与证据边界

外部原则只采纳平台或框架所有者发布的第一方资料：Apple Human Interface Guidelines、Microsoft Windows app design guidance、GNOME HIG/libadwaita 文档和 Qt 官方文档。仓库设置项以以下实现为准：

- [`pet/modern_settings_dialog.py`](../pet/modern_settings_dialog.py)：新版设置页、侧栏、分组、搜索与控件。
- [`pet/settings_dialog.py`](../pet/settings_dialog.py)：旧版设置能力与 Windows 主动识屏旧入口。
- [`pet/config.py`](../pet/config.py)：持久化默认值、嵌套配置和平台无关配置契约。
- [`docs/SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md`](SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md)：上一轮 IA 结论与已知 Qt 陷阱。

本文的分类是基于这些代码事实的设计推论，不把参考产品或平台指南当作必须逐像素照搬的模板。

## 第一方指南提炼

### 1. 设置只承载全局、低频、可持久化偏好

- Apple 建议默认值应覆盖大多数用户，减少设置数量；特定任务的选项应尽量放回任务现场，而不是强迫用户离开当前流程进入设置。[Apple HIG — Settings](https://developer.apple.com/design/human-interface-guidelines/settings)
- Microsoft 同样把设置定义为不需要频繁调整的行为、偏好和低频应用信息，并明确日常工作流中的命令不应放进设置页。[Microsoft — Guidelines for app settings](https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings)

对本项目的影响：播放某个动画、切换角色、立即隐藏桌宠、打开应用和测试音效仍是命令；默认播放速度、角色显示大小、快捷启动列表和音效来源才是设置。“试听”“连接测试”“清除记忆”可以作为所属设置旁的辅助动作，但不应成为一级分类。

### 2. 导航按用户任务组织，并保持稳定、浅层、可搜索

- Apple 的 macOS 指南将设置拆为相关设置组成的稳定 pane，并要求始终指示当前 pane、恢复最近查看的 pane；用户依赖稳定位置再次找到设置。[Apple HIG — Settings / macOS](https://developer.apple.com/design/human-interface-guidelines/settings)
- Microsoft 的导航原则是 consistency、simplicity、clarity；应减少一级目的地并避免超过两层的深导航。[Microsoft — Navigation design basics](https://learn.microsoft.com/en-us/windows/apps/design/basics/navigation-basics)
- GNOME 的 `AdwPreferencesDialog` 原生模型是“可搜索的设置对话框 → pages → groups”，`AdwPreferencesPage` 明确用于把设置组汇集成一页。[GNOME libadwaita — PreferencesDialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.PreferencesDialog.html) [GNOME libadwaita — PreferencesPage](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.PreferencesPage.html)

对本项目的影响：保留侧栏和全局搜索，但一级入口只表达长期稳定的用户心智模型；不为单个 SettingRow 创建一级页，也不把平台名称本身做成分类。

### 3. 页内使用“组 → 标题/说明 → 控件”的单列结构

- Microsoft 建议相关设置放在同一 section，采用单列可滚动布局，并限制宽屏内容最大宽度约 1000–1100 px；设置卡片由标题、说明、可选图标和右侧控件构成。[Microsoft — Guidelines for app settings / Layout](https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings)
- GNOME 的 preferences group 表示一组紧密相关的设置；ActionRow 由标题、副标题和尾部控件组成，正适合表达一个偏好及其操作。[GNOME libadwaita — PreferencesGroup](https://gnome.pages.gitlab.gnome.org/libhandy/doc/main/class.PreferencesGroup.html) [GNOME libadwaita — ActionRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ActionRow.html)

对本项目的影响：现有 `SettingsSection → SettingsCard → SettingRow` 层级可以保留。分类重构不需要把所有内容改成树形导航；复杂内容仍在一个页面内按组纵向排列。

### 4. 高级项渐进披露，最多展开一层

- Microsoft 建议用 `SettingsExpander` 收纳不常使用的子选项，避免超过一层嵌套；不可用项需要说明原因。[Microsoft — Guidelines for app settings / SettingsExpander](https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings)
- GNOME 提供 ExpanderRow 在一个首要设置下揭示其他 rows，说明“主设置 + 一层依赖项”是平台认可的偏好结构。[GNOME libadwaita — Boxed Lists](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/boxed-lists.html)

对本项目的影响：碰撞系数、菜单自定义色、模型生成参数、主动识屏自定义频率、独立视觉端点都应作为一层高级或依赖内容，而不是与总开关同等突出。

### 5. 平台一致性是语义一致，不是强制像素一致

- Qt 默认选择最适合当前平台或桌面环境的 `QStyle`，内建控件通过 style 获取平台外观；Qt 的平台抽象还分别承接平台字体、主题、原生对话框和菜单。[Qt — QStyle](https://doc.qt.io/qt-6/qstyle.html) [Qt — Qt Platform Abstraction](https://doc.qt.io/qt-6/qpa.html)
- Qt 明确指出系统控制的样式不一定服从 `QProxyStyle`，macOS 的系统菜单就是例子。因此不能把三端原生菜单行为假设成同一套可覆盖实现。[Qt — QProxyStyle](https://doc.qt.io/qt-6/qproxystyle.html)
- Qt Widgets 使用设备无关像素并自动接入平台 DPI；应用不应自行暴露一套操作系统 DPI 设置。[Qt — High DPI](https://doc.qt.io/qt-6/highdpi.html)
- Apple 建议遵守系统级外观和可访问性设置，避免在应用设置中重复系统已拥有的全局选项。[Apple HIG — Settings](https://developer.apple.com/design/human-interface-guidelines/settings)

对本项目的影响：三端共享分类、标题、配置语义、默认值和依赖关系；字体度量、控件绘制、窗口装饰、系统菜单和 DPI 由平台适配层承担。Q1 已选择的“产品语言一致、平台细节适配”与这些资料一致。

## 当前真实设置项盘点

### 当前新版一级页面

| 当前页面 | 主要内容 | 发现的问题 | 仓库证据 |
| --- | --- | --- | --- |
| 常规 | 启动、窗口、Windows 集成、AI 余额刷新 | “后台服务”实际是 AI 服务状态，不是通用应用行为 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py) |
| 灵动岛 | 胶囊窗开关、内容与视觉 | 9 个相关设置，已形成独立任务域 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py), [`config.py`](../pet/config.py) |
| 桌宠行为 | 动画、拖拽、碰撞、点击、自言自语、Agent 文案和音效 | 页面过长；Agent 联动不是桌宠物理行为 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py) |
| 外观 | 桌宠、菜单、AI 对话、主题色、彩蛋 | 混合了四种作用对象，用户难以预测归属 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py) |
| 快捷启动 | 一个应用列表编辑器 | 一级页信息量不足，且其结果只出现在右键菜单 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py) |
| 主动识屏 | Windows AI 陪伴触发与白名单 | 只在 Windows + AI 构建出现，导致侧栏跨平台漂移 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py), [`settings_dialog.py`](../pet/settings_dialog.py) |
| AI 设置 | 模型、视觉模型、生成参数 | 连接、生成和对话外观被拆到两个一级页 | [`modern_settings_dialog.py`](../pet/modern_settings_dialog.py) |

### 配置域事实

`Config` 已经自然形成若干领域：桌宠窗口与动画、交互与气泡、右键菜单外观、快捷启动、聊天、灵动岛、主动识屏、Agent 联动和碰撞。它们适合成为 IA 的证据，但不应直接等同侧栏，因为配置持久化结构服务代码，而侧栏服务用户。[`config.py`](../pet/config.py)

旧设置页仍暴露开机自启、全屏隐藏、光标隐藏穿透、鼠标穿透、碰撞、点击反馈、自言自语及 Windows 主动识屏。迁移时必须建立同一设置 schema，不能靠两套窗口各自维护分类和保存逻辑。[`settings_dialog.py`](../pet/settings_dialog.py)

## 建议侧栏分类与完整归属

### 1. 常规

用户问题：“应用如何随系统运行，并与桌面环境协作？”

| 页内组 | 设置归属 | 说明 |
| --- | --- | --- |
| 启动 | 开机自启 | 跨平台同一语义，平台适配器执行注册表、LaunchAgent 或 Linux autostart 实现。 |
| 系统集成 | 窗口置顶 | 全局桌面行为，不与动画混放。 |
| 系统集成（macOS） | 显示 Dock 图标 | 仅 macOS 创建；明确标注平台。 |
| 系统集成（Windows） | 全屏时自动隐藏、光标隐藏时自动穿透、直播捕获兼容 | 仅 Windows 创建；均描述桌宠与 Windows 窗口系统的关系。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`settings_dialog.py`](../pet/settings_dialog.py)、[`config.py`](../pet/config.py)。

不归入常规：余额刷新与峰谷文案属于 AI 服务反馈，应移至“AI 与对话”。

### 2. 桌宠

用户问题：“桌宠看起来多大、如何运动、如何被拖动和碰撞？”

| 页内组 | 设置归属 | 披露策略 |
| --- | --- | --- |
| 显示 | 桌宠大小、不透明度 | 常用设置直接显示。 |
| 动画与移动 | 播放速率、动作等待间隔、不移动、音乐自动唱歌 | 常用行为直接显示；“不移动”应与“锁定位置”通过说明区分。 |
| 拖拽与弹射 | 拖动物理、甩出力度、弹弓弹射、锁定位置、Shift+左键拖动 | 子项随总能力启用；避免无效控件仍可编辑。 |
| 多开碰撞 | 碰撞开关、碰撞音效 | 首层显示。 |
| 多开碰撞 / 高级 | 弹性系数、摩擦系数、质量倍率、冲量上限、碰撞音量 | 收入一层高级展开；默认用户不必理解物理参数。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`config.py`](../pet/config.py)。

不归入桌宠：鼠标穿透改变输入命中语义，建议放“互动”；气泡方案属于互动反馈；Agent 状态文案属于“联动”。

### 3. 互动

用户问题：“我点击桌宠时发生什么，桌宠何时以声音或气泡回应？”

| 页内组 | 设置归属 | 披露策略 |
| --- | --- | --- |
| 输入 | 鼠标穿透 | 直接显示并说明恢复入口；Windows 的“光标隐藏时自动穿透”仍属系统集成，因为它是平台自动规则。 |
| 点击反馈 | 点击音效、音效音源、音效音量、试听音效、点击显示余额、点击触发自言自语 | 音源、音量、试听依赖“点击音效”；余额项仅 AI 构建显示。 |
| 自言自语 | 气泡自言自语、气泡方案、显示时间、最短间隔、最长间隔、候选内容、图片目录 | 关闭总开关后隐藏或禁用依赖项并解释。 |
| 台词绑定 | 点击动画台词绑定 | 作为自言自语的深入编辑入口，不扩展为新的侧栏页。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`settings_dialog.py`](../pet/settings_dialog.py)、[`config.py`](../pet/config.py)。

“点击显示余额”虽然数据源属于 AI，但触发方式属于点击。保留在互动页并通过搜索别名“余额 / AI”可发现，比在两页重复同一开关更清晰。

### 4. 菜单

用户问题：“右键菜单如何显示，里面有哪些个性化入口和快捷操作？”

| 页内组 | 设置归属 | 披露策略 |
| --- | --- | --- |
| 外观 | 颜色主题、菜单密度、圆角大小、UI 字体、UI 字号、半透明菜单、表面不透明度 | 主题默认“跟随系统”；透明度依赖半透明开关。 |
| 高级配色 | 浅色背景/文字/悬停色，深色背景/文字/悬停色 | 放一层高级展开；避免 6 个颜色输入压过主要设置。 |
| 快捷启动 | 已配置应用、排序、添加、移除、添加默认浏览器 | 从独立一级页合并，因为结果只属于右键菜单。菜单中可另提供“管理快捷启动”深链到此组。 |
| 彩蛋入口 | 显示彩蛋、入口标题、右侧提示、头像图片、弹窗图片目录 | 依赖总开关；这是菜单首行的内容配置，不是全局外观。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`config.py`](../pet/config.py)。

### 5. 灵动岛

用户问题：“独立胶囊窗口显示什么、长什么样？”

| 页内组 | 设置归属 | 披露策略 |
| --- | --- | --- |
| 基本 | 启用灵动岛 | 页面主开关。 |
| 内容 | 显示图标、显示名称、显示信息槽、显示状态灯、信息槽内容、自定义短文本 | 各子项按对应显示开关渐进披露。 |
| 外观 | 背景风格、图标 | 不与右键菜单主题色混放，因为作用对象不同。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`config.py`](../pet/config.py)。

暂时保留独立一级页：它拥有独立窗口、独立总开关和 8 个子设置，信息量足以形成任务域。若后续删减到 3–4 个简单开关，再考虑并入“常规 / 桌面组件”。

### 6. AI 与对话

用户问题：“桌宠连接哪个模型、如何生成回复、对话窗口如何呈现？”

| 页内组 | 设置归属 | 披露策略 |
| --- | --- | --- |
| 模型与连接 | Provider 名称、API 地址、模型、API Key、System Prompt、连接测试 | 连接测试是该组的辅助动作。凭据继续优先系统安全存储。 |
| 视觉能力 | 视觉模型复用聊天模型、视觉模型、视觉 API 地址、视觉 API Key | 默认复用；独立端点只在关闭复用时显示。 |
| 生成 / 高级 | 请求超时、Temperature、最大输出 Token、跳过 SSL 证书验证 | 一层高级展开；SSL 旁保留风险说明。 |
| 对话窗口 | 对话窗口版本、对话背景、自定义背景图片、图片不透明度、填充方式、消息卡片不透明度 | 从“外观”迁入，让同一用户任务不跨页。 |
| 余额与服务状态 | 余额自动刷新、峰谷提示文案、高峰/空闲自定义文本、峰谷提示颜色 | 从“常规 / 后台服务”迁入；仅支持该能力的构建或 Provider 显示。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`config.py`](../pet/config.py)。

页面仅在 `include_ai` 为真时显示。隐藏整个构建不支持的领域比留下无法使用的空页更诚实；配置 schema 仍保持兼容。

### 7. 联动

用户问题：“桌宠如何响应 Agent 或主动感知其他应用？”

| 页内组 | 设置归属 | 平台策略 |
| --- | --- | --- |
| Agent 文案 | DSH、Claude Code、Cursor、OpenCode 的思考气泡文案 | 跨平台显示；未来应由 Agent 能力清单生成，不在 IA 中写死实现。 |
| Agent 提示音 | 总开关、开始/完成/错误音源、音量、冷却时间 | 跨平台显示；子项依赖总开关。 |
| 主动识屏（Windows） | 开关、dry-run、节奏预设 | 仅 Windows + AI 能力存在时创建此组。 |
| 主动识屏 / 高级频率 | 停留门限、冷却间隔、最小请求间隔、每日请求上限 | 仅自定义节奏时展开。 |
| 主动识屏 / 触发条件 | 闲置要求、闲置秒数、鼠标穿透时识屏、触发前提示、优先独立视觉配置 | 依赖主动识屏总开关。 |
| 主动识屏 / 范围与数据 | 白名单、快捷添加、清除陪伴记忆 | 清除记忆是数据管理动作，紧邻其数据说明。 |

仓库来源：[`modern_settings_dialog.py`](../pet/modern_settings_dialog.py)、[`settings_dialog.py`](../pet/settings_dialog.py)、[`config.py`](../pet/config.py)、[`agent_link.py`](../pet/agent_link.py)。

把主动识屏并入“联动”后，macOS、Windows、Linux 都保留同一个侧栏目的地；Windows 只多一个明确标记的平台组，不再多出整页。若某个构建既没有 Agent 联动也没有主动识屏能力，才隐藏整个“联动”页。

## 平台专属项策略

### 统一不变量

以下内容三端必须一致：

- 侧栏分类名称、顺序和图标语义；只有整个能力域不存在时才隐藏入口。
- 设置 key、默认值、保存语义、依赖关系和搜索关键词。
- 每行的标题、说明、危险级别和是否属于高级项。
- 自动化测试使用同一设置目录模型和能力矩阵。

### 允许的平台适配

以下内容应服从平台：

- 系统字体、控件度量、窗口装饰、原生菜单行为和键盘约定。
- 开机自启、Dock、系统托盘、窗口捕获和全屏检测的具体实现。
- Windows 的 Win32 主动识屏；macOS/Linux 不显示伪开关。
- DPI 和屏幕缩放由 Qt/操作系统处理，不新增应用内 DPI 百分比。

Qt 的 `QStyle`、QPA 和 High DPI 文档分别支持上述视觉、平台服务和缩放边界。[Qt — QStyle](https://doc.qt.io/qt-6/qstyle.html) [Qt — QPA](https://doc.qt.io/qt-6/qpa.html) [Qt — High DPI](https://doc.qt.io/qt-6/highdpi.html)

### 显示、禁用还是隐藏

| 情形 | 策略 | 示例 |
| --- | --- | --- |
| 当前状态暂时不满足，但用户能在本页恢复 | 保留并禁用，说明依赖 | 未开启半透明菜单时的表面不透明度 |
| 当前平台永远不支持，显示会制造错误期待 | 不创建该行 | macOS/Linux 上的直播捕获兼容 |
| 构建未包含整个能力域 | 隐藏整页 | 无 Chat 构建的 AI 与对话 |
| 平台只缺少页内一个能力组 | 保留页，只隐藏该组 | 非 Windows 的联动页不显示主动识屏 |
| 能力存在但运行时暂不可用 | 保留并禁用，展示原因和恢复动作 | 系统安全存储不可用、Provider 未配置 |

“暂时不可用时禁用并解释”来自 Microsoft 设置指南；“永久不存在时不创建”同时延续仓库已记录的 Qt 子控件泄漏经验。[Microsoft — Guidelines for app settings](https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings) [`SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md`](SETTINGS-INFORMATION-ARCHITECTURE-2026-08-27.md)

## 建议的信息架构骨架

```text
桌宠设置
├── 常规
│   ├── 启动
│   └── 系统集成 [macOS/Windows 条件行]
├── 桌宠
│   ├── 显示
│   ├── 动画与移动
│   ├── 拖拽与弹射
│   └── 多开碰撞
├── 互动
│   ├── 输入
│   ├── 点击反馈
│   ├── 自言自语
│   └── 台词绑定
├── 菜单
│   ├── 外观
│   ├── 高级配色
│   ├── 快捷启动
│   └── 彩蛋入口
├── 灵动岛
│   ├── 基本
│   ├── 内容
│   └── 外观
├── AI 与对话 [AI 构建]
│   ├── 模型与连接
│   ├── 视觉能力
│   ├── 生成参数
│   ├── 对话窗口
│   └── 余额与服务状态
└── 联动
    ├── Agent 文案
    ├── Agent 提示音
    └── 主动识屏 [Windows + AI]
```

侧栏仍是一层，页内组不是第二层导航。搜索结果应跳到目标页和具体 SettingRow，并对“旧分类词”设置别名，例如搜索“后台服务”仍命中“AI 与对话 / 余额与服务状态”。

## 与现有 IA 文档的关系

本文保留上一轮已经正确的原则：唯一归属、稳定一级任务域、页面标题—分类—卡片—设置项层级、依赖项渐进显示、平台不支持控件不创建。本文更新的是具体边界：

- “桌宠行为”拆成“桌宠”和“互动”。
- “外观”按作用对象拆到桌宠、菜单、灵动岛、AI 与对话。
- “快捷启动”降为菜单页内组。
- “主动识屏”降为联动页内 Windows 组。
- “Agent 联动”从行为页迁至联动页。

这是一份研究建议，不覆盖原文或构成已批准 ADR；用户确认后再进入 spec 和 ticket 拆分。

## 待用户决策

1. **是否批准 7 个侧栏入口。** 推荐批准；若必须压缩到 6 个，优先把“灵动岛”并入“常规 / 桌面组件”，不要重新合并“桌宠”和“互动”。
2. **“互动”是否采用更具产品感的名称。** 候选为“互动”“气泡与声音”；推荐“互动”，覆盖鼠标穿透、点击、音效和自言自语且更稳定。
3. **灵动岛是否继续作为跨平台产品名称。** 当前能力并非 macOS Dynamic Island 系统功能；若担心误解，可改名“桌面胶囊”，配置 key 无需同步改名。
4. **菜单高级配色是否继续公开。** 推荐保留但默认折叠；若目标是更接近系统设置，可只提供“跟随系统 / 浅色 / 深色”，把自定义色降为实验功能。
5. **设置保存语义。** 当前有“保存并退出”，但 Microsoft 建议设置即时反映且不要求确认；需决定采用即时保存、关闭时自动保存，还是显式 Apply。该决定影响所有页面的交互契约。[Microsoft — Guidelines for app settings](https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings)
6. **高级参数的目标用户。** 推荐将碰撞物理参数、模型生成参数、SSL 跳过和主动识屏自定义频率统一标为“高级”，仅展开一层。
7. **旧设置窗口的退场节奏。** Q2 已决定新版为唯一能力入口、旧版仅短期回退壳；仍需确定旧窗口停止新增设置的版本节点。

## 第一方来源索引

- [Apple Human Interface Guidelines — Settings](https://developer.apple.com/design/human-interface-guidelines/settings)
- [Apple Developer Documentation — Adding a settings interface to your app](https://developer.apple.com/documentation/foundation/adding-a-settings-interface-to-your-app)
- [Microsoft — Guidelines for app settings](https://learn.microsoft.com/en-us/windows/apps/design/app-settings/guidelines-for-app-settings)
- [Microsoft — Navigation design basics](https://learn.microsoft.com/en-us/windows/apps/design/basics/navigation-basics)
- [GNOME HIG — Windows](https://developer.gnome.org/hig/patterns/containers/windows.html)
- [GNOME libadwaita — PreferencesDialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.PreferencesDialog.html)
- [GNOME libadwaita — PreferencesPage](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.PreferencesPage.html)
- [GNOME libadwaita — ActionRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ActionRow.html)
- [GNOME libadwaita — Boxed Lists](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/boxed-lists.html)
- [Qt — QStyle](https://doc.qt.io/qt-6/qstyle.html)
- [Qt — QProxyStyle](https://doc.qt.io/qt-6/qproxystyle.html)
- [Qt — Qt Platform Abstraction](https://doc.qt.io/qt-6/qpa.html)
- [Qt — High DPI](https://doc.qt.io/qt-6/highdpi.html)
