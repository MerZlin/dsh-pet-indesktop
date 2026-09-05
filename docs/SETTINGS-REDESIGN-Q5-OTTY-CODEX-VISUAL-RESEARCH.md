# 桌宠设置重构 Q5：Otty 与 Codex 视觉调研

日期：2026-09-01
范围：设置窗口的视觉语言与交互结构，不构成最终视觉规范，不修改产品代码。

## 产品身份与证据边界

本文中的 **Otty** 指 [otty.sh](https://otty.sh/) 的 macOS 终端应用，而非
Otter.ai 或 iotty。Otty 官方文档明确使用 `⌘,` 打开 Settings，并公开了设置
窗口、主题、快捷键和 Advanced 页面截图。Codex 只采用 OpenAI 官方
[Settings 文档](https://learn.chatgpt.com/docs/reference/settings)中的桌面应用界面。

截图反映 2026-09-01 可见的产品版本；两款产品仍在迭代，因此本文提炼模式，
不把具体像素值当成稳定 API。

## 第一方真实截图

### Otty：Appearance / Theme

![Otty 官方设置主题页](https://docs.otty.sh/screenshots/change-theme.png)

来源：[Otty First Launch — Change Theme](https://docs.otty.sh/getting-started/first-launch#_4-change-theme)。
官方页面说明主题可从 Settings → Appearance → Theme 修改，并支持继续调整颜色、
字体和 padding。

### Codex：Appearance

OpenAI 官方 Settings 页面内嵌了真实桌面应用 Appearance 展示，包含内置 Codex
主题与 Catppuccin/Dracula 自定义主题对照：

- [OpenAI Settings — Appearance](https://learn.chatgpt.com/docs/reference/settings#appearance)
- 官方图的替代文本为 “ChatGPT desktop app Appearance settings showing theme
  selection, color controls, and font options”。

官方图明确展示 Theme（Light / Dark / System）、浅色与深色主题配置、Accent、
Background、Foreground、UI font、Code font、Translucent sidebar、Contrast、
pointer cursor，以及 UI/code font size。本文不复制第三方截图或概念重绘。

## Otty 的结构与视觉特点

### 信息结构

- 左侧永久导航使用图标 + 文本：General、Shell、Controls、Editor、Integrations、
  Appearance、Recipes、Key Bindings、Advanced。
- 分类按用户任务和专业工作流组织；Advanced 承接完整配置表，而不是把所有专家
  参数塞进常用页面。
- [Advanced / All Settings](https://docs.otty.sh/customization/advanced-settings)
  提供一个可搜索的完整 key 列表；已有专属控件的 key 会深链回对应页面。
- [Keybindings](https://docs.otty.sh/customization/custom-keybindings) 独立成页，
  支持按命令名或按键搜索、冲突提示和即时重绑。

### 视觉语言

- macOS 工具窗口骨架：交通灯、紧凑左侧栏、右侧工作区和系统字体。
- 深色模式使用接近黑灰的分层表面；边界主要依赖明度差、细描边和阴影，而非
  大面积高饱和色。
- 当前分类以低对比选中背景和单色图标标记；内容区把视觉强调留给当前任务，
  例如主题预览卡片网格。
- 密度偏开发者工具：导航紧凑、信息量高，但页面只在需要时显示复杂编辑器。

## Codex 的结构与视觉特点

### 信息结构

OpenAI 官方 Settings 文档列出 General、Profile、Keyboard shortcuts、
Notifications、Appearance、Pets、Browser、Computer Use、Personalization、
Suggested prompts、Memories 和 Archived chats。它们按用户任务/能力域组织，而非
按内部模块组织。[OpenAI Settings](https://learn.chatgpt.com/docs/reference/settings)

Appearance 将主题模式放在最前，再分别编辑浅色和深色主题；颜色、字体、透明度、
对比度和字号都属于同一视觉任务。官方文档还明确 macOS 使用 `Cmd+,`、Windows
使用 `Ctrl+,`，说明信息结构统一而平台快捷键服从系统约定。

### 视觉语言

- 大圆角浮层卡片位于柔和背景上，表面层级明显但阴影克制。
- 页面留白比 Otty 更宽，设置行由标签、短说明和右侧控件组成；主要阅读方向稳定。
- 黑白灰是主表面，Accent 只承担选中态、开关和关键交互，不把品牌色铺满页面。
- 主题编辑提供即时视觉预览；浅色与深色值并列管理，默认主题仍可保持系统感。
- 默认 UI 字号在官方图中为 14px，与本项目现有设置页的 14px 行标签接近。

## 共同模式与关键差异

| 维度 | Otty | Codex | 可复用结论 |
| --- | --- | --- | --- |
| 导航 | 紧凑图标侧栏 | 稳定能力分类 | 使用一层稳定侧栏 |
| 内容 | 专业工具密度较高 | 留白宽、说明更充分 | 日常项采用 Codex 密度，高级编辑器允许 Otty 密度 |
| 表面 | 深色工具窗、细描边 | 圆角卡片、柔和分层 | 使用低饱和中性色和清晰层级 |
| 控件 | 主题网格、专业编辑器 | 行尾控件、滑杆和颜色 pill | 控件贴近所属设置，不另建悬浮工具条 |
| 高级项 | Advanced + All Settings | 能力域内渐进设置 | 专家项一层折叠并保持全局搜索可达 |
| 平台感 | 明显服从 macOS 窗口 | 内容语言统一、快捷键平台化 | 共享 UX 契约 + 平台适配 |

## 对桌宠设置的建议视觉方向

推荐采用 **Otty 骨架 + Codex 内容语言 + 轻量桌宠品牌层**：

1. **窗口骨架靠近 Otty**：紧凑稳定的图标侧栏、顶部搜索、单一内容区；窗口装饰、
   字体和快捷键交给平台。
2. **页面内容靠近 Codex**：页面标题、短说明、低饱和卡片、行尾控件、14px 基准
   字号和宽松但不过度的垂直节奏。
3. **品牌层只落在关键位置**：侧栏选中态、页头小头像、空状态、成功反馈和
   120–160ms transition；不使用持续动画或大面积角色背景干扰配置阅读。
4. **主题先提供系统/浅色/深色**；菜单高级配色和自定义主题默认折叠，实时预览
   仅影响示例菜单，不让用户通过反复打开右键菜单试错。
5. **跨平台共享语义 token**：spacing、radius、surface、text role、accent role 和
   motion duration 统一；实际字体度量、窗口圆角、阴影和原生菜单由 adapter 调整。

## 不建议照搬的部分

- 不复制 Otty 的高密度 Advanced 全配置表作为默认体验；桌宠用户不应先面对配置
  key。它只适合作为未来的诊断/实验入口。
- 不复制 Codex 的大面积壁纸或开发者代码预览。桌宠设置需要展示菜单、气泡、角色
  尺寸等自身对象的实时预览。
- 不强制三端使用同一窗口交通灯、标题栏高度、系统字体或原生菜单阴影。
- 不让 legacy/modern 两套菜单各自维护视觉 token；它们应消费同一主题模型。

## 待用户决策

1. 是否批准“Otty 骨架 + Codex 内容语言 + 轻量桌宠品牌层”。
2. 是否为菜单外观提供内嵌实时预览。推荐提供，减少跨平台试错。
3. 是否保留高级自定义色。推荐保留但折叠，并提供恢复默认值。
4. 设置页是否允许品牌头像。推荐只在页头或空状态出现，不进入每张设置卡。

## 第一方来源

- [Otty — First Launch](https://docs.otty.sh/getting-started/first-launch)
- [Otty — Advanced / All Settings](https://docs.otty.sh/customization/advanced-settings)
- [Otty — Keybindings](https://docs.otty.sh/customization/custom-keybindings)
- [Otty — Configuration Reference](https://docs.otty.sh/reference/configuration)
- [OpenAI — Settings](https://learn.chatgpt.com/docs/reference/settings)
