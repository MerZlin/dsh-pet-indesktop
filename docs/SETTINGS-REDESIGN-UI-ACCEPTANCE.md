# 设置页逐页 UI 验收

日期：2026-09-03
真实平台：macOS，Qt Cocoa  
窗口矩阵：1100×760 浅色、720×760 浅色、1100×760 深色

截图目录：

- `screenshots/settings-redesign/iteration-2-wide/`
- `screenshots/settings-redesign/iteration-2-compact/`
- `screenshots/settings-redesign/iteration-2-dark-wide/`
- `screenshots/settings-redesign/iteration-3-accessibility/`（720×760、125% 字体、极端长文案）
- `screenshots/settings-redesign/iteration-4-toggle-default/`（1100×760，配置默认显隐态）
- `screenshots/settings-redesign/iteration-4-toggle-expanded/`（1100×760，macOS 可用依赖全部展开）
- `screenshots/settings-redesign/iteration-4-toggle-compact/`（720×760、125% 字体、极端长文案、依赖全部展开）
- `screenshots/settings-redesign/iteration-5-layout-wide/`（1600×1000，宽内容与标题对齐）
- `screenshots/settings-redesign/iteration-5-layout-compact/`（720×760，菜单上下分栏）
- `screenshots/settings-redesign/iteration-5-layout-dark/`（1100×760，菜单与快捷启动深色态）
- `screenshots/settings-redesign/iteration-6-menu-tabs-wide/`（1600×1000，三个菜单页内任务与停用态）
- `screenshots/settings-redesign/iteration-6-menu-tabs-compact/`（720×760，页内 Tab、上下编排与停用态）
- `screenshots/settings-redesign/iteration-6-menu-tabs-dark/`（1100×760，页内 Tab、编排与停用态深色样式）

## 逐页结果

| 页面 | 验收重点 | 结果 |
| --- | --- | --- |
| 常规 | 稀疏页面留白、卡片宽度、macOS 专属 Dock 设置 | 通过 |
| 桌宠 | 长页面滚动、数值控件、开关与下拉对齐 | 通过 |
| 互动 | 禁用态、说明文字换行、窄宽度控件可达 | 通过 |
| 菜单 | 编辑/预览分栏、紧凑上下布局、名称不截断、深色树样式 | 通过 |
| 桌面组件 | 密集开关、下拉与文本框混排 | 通过 |
| AI 与对话 | 内容占满共享页面宽度、长文本输入、连接卡片 | 通过 |
| 自动化与联动 | 多文本框卡片、长说明与开关布局 | 通过 |

ToggleSwitch 专项同时检查关闭后的卡片收缩、分隔线重排、空高级分组隐藏，以及重新开启后的完整恢复。灵动岛还覆盖“显示图标 / 显示信息槽 / 自定义短文本”组合依赖；Windows 主动识屏无法在本机真实截图，仅有 capability fake 的显隐测试。

AI 页另存 `08-AI 与对话-高级展开.png`，确认自定义披露标题、箭头、展开卡片和下游内容在浅色宽屏、浅色紧凑及深色宽屏下保持同一视觉语言。

## 放大字体与极端文案矩阵

用 `scripts/capture_settings_pages.py --width 720 --height 760 --font-scale 1.25 --extreme-copy` 在真实 macOS Cocoa 后端逐页复拍并人工检查。首拍发现菜单模式行与 AI Provider 复合控件横向溢出；修复后七页均满足：标题/说明完整换行、右侧或下置控件位于卡片内、无水平滚动、纵向滚动可达。下拉框中超出单行容量的当前值允许截短，但控件边界与操作按钮不得被裁切。

## 本轮截图驱动修复

- AI 页原先嵌套“页面中的页面”，Cocoa 下内容被压缩成窄列；可见设置组现直接进入共享页面容器。
- 原生 `QToolButton` 披露控件与页面风格不一致；现改为统一的 `SettingsDisclosureHeader`，不使用会导致 Cocoa 崩溃的自绘 `QPainter` 路径。
- 菜单编辑器原生灰色表头和默认列宽导致名称省略；现使用卡片式树样式，菜单项列拉伸、位置列按内容定宽，预览不显示表格表头。
- 截图脚本固定把初始焦点留在侧栏，避免搜索框蓝色焦点环干扰默认视觉层级。
- 设置行标题/说明现关联到实际控件，普通按钮与自定义下拉/披露控件均有可见键盘焦点状态。
- Agent 单事件音效首轮紧凑截图中固定横排贴到右边界；`ResponsiveToggleActionRow` 现于窄宽度改为两层布局，关闭事件时只保留可恢复开关，展开态不再裁切路径和按钮。
- 1600px 窗口下标题与 1240px 菜单内容左沿同步；编排左右面板充分利用宽高，操作按能力分组为下拉菜单。
- 720px 窗口下编排面板转为上下排列，四个命令组保持同排可达；快捷启动改为两行内容项和紧凑空状态，不再保留大块空白列表。
- 子菜单删除/自动清理属于交互状态，使用确认框替身和树模型 remove/insert 测试验收；静态截图不伪造弹窗状态。
- 菜单页的三个同级任务现由页内 Tab 分离，截图确认侧栏仍为原七个能力域；宽屏、紧凑和深色下选中态清楚，隐藏页不会撑宽当前页。
- 默认分组显示为编排树中的“— 分割线”，可被移动或删除；彩蛋关闭后的节点仍停留原位，状态切为“已停用”，预览以不可用样式保留。快捷启动空状态同样由自动化测试覆盖，布局节点不变。

## 未声称的覆盖

Windows 与 Linux 尚未在真实主机逐页截图；当前只有共享 Qt 实现和 capability 测试覆盖，不能据此声称两端视觉已验收。
