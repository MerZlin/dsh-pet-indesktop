# dsh-pet-indesktop

<p align="center">
  <a href="https://github.com/MerZlin/dsh-pet-indesktop/releases"><img alt="版本" src="https://img.shields.io/github/v/release/MerZlin/dsh-pet-indesktop?label=%E7%89%88%E6%9C%AC&color=blue"></a>
  <a href="https://github.com/MerZlin/dsh-pet-indesktop/releases"><img alt="总下载" src="https://img.shields.io/github/downloads/MerZlin/dsh-pet-indesktop/total?label=%E6%80%BB%E4%B8%8B%E8%BD%BD&color=brightgreen"></a>
  <a href="https://github.com/MerZlin/dsh-pet-indesktop"><img alt="Stars" src="https://img.shields.io/github/stars/MerZlin/dsh-pet-indesktop?style=social"></a>
  <a href="https://github.com/MerZlin/dsh-pet-indesktop/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/MerZlin/dsh-pet-indesktop?color=orange"></a>
  <a href="https://github.com/MerZlin/dsh-pet-indesktop"><img alt="Repo size" src="https://img.shields.io/github/repo-size/MerZlin/dsh-pet-indesktop"></a>
  <a href="https://github.com/MerZlin/dsh-pet-indesktop/issues"><img alt="Issues" src="https://img.shields.io/github/issues/MerZlin/dsh-pet-indesktop"></a>
  <img alt="平台" src="https://img.shields.io/badge/%E5%B9%B3%E5%8F%B0-Windows%20%7C%20macOS%20%7C%20Linux-8A2BE2">
  <img alt="动画素材" src="https://img.shields.io/badge/%E5%8A%A8%E7%94%BB-dynamic%20animations-ff69b4">
</p>

一个基于 **Python + PySide6** 的独立桌面宠物。项目脱离 DSH 运行时，提供透明无边框、置顶、可拖动、角色切换、动画播放、系统托盘和可选 AI 对话能力。

> **当前版本 v4.2.0（2026-09-10 发布）**：自 v4.1.0 以来的功能与修复汇总（PR #57/#64/#65/#68/#70/#71/#72/#73/#76/#79/#80/#82/#85/#86/#87/#90/#91/#93/#94/#96）。
> 亮点：桌宠内存专项治理（长时运行不再单调上涨）、单进程多窗与同角色共享解码链（3 窗 1 进程 1 解码器）、设置与菜单重构（右键菜单可编排、快捷启动、主题即时生效）、边缘探头 / 黄金回旋 / 撞飞彩蛋 / 省电模式、待办提醒、DSH 事件层与探索 Watchdog 控制链、子肥鱼「只退出不删数据」、台词模板 v2 与表达风格、高刷屏流畅度、气泡行尾字裁切修复、DLC 换角色加固。
>
> **开发版 main（未发布，v4.2.1 候选）**：已并入 PR #97（事件汇报概率门 + 事件链路语义对齐 + issue #95：Windows node/pnpm 解析不再写死）、#100 / #102（启动即按配置装配可选服务，issue #99）、#101（边缘探头会话期间禁止位移）、#104 / #105（桥接插件归零外部依赖 + DSH 事件层系统性收尾）、#107（余额多币种盲取首条）、#108（弹射卡顿）、#109（探头/头槌体验 + 气泡分页）、#112（Windows 关机/注销弹 0xc0000142）。本机全量套件 1950 passed / 8 skipped，新增内容见下方 [最近修复与变更记录](#最近修复与变更记录)。
>
> 发布形态为 **onedir 目录打包 + Inno Setup 安装包（`.exe`）+ 便携 zip 绿色版**：安装版与绿色版运行期都不解压、不产生临时缓存，启动快、卸载干净。v4.2.0 完整发布清单见 [`docs/RELEASE-v4.2.0.md`](docs/RELEASE-v4.2.0.md) 与 [GitHub Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)。

## 开发约束与规则

> 本节的约束来自 PR #76 那轮“实测数据驱动的性能 + 结构治理”，此后每个版本都在沿用并加固；**给本项目提交代码前请先读本节以及 [`AGENTS.md`](AGENTS.md)、[`docs/WINDOW_PY_SPLIT_GUIDE.md`](docs/WINDOW_PY_SPLIT_GUIDE.md)**。

### 架构红线（`tests/test_architecture.py`，红了即 CI 失败）

1. **纯逻辑层不依赖 Qt**：`collision.py` / `physics.py` / `collision_codec.py` 禁止 import PySide6。
2. **共享解码链单向依赖**：`decode_fanout.py` 不得反向依赖 `window.py` / `webm_clip.py`，窗口钩子只能通过注入接入。
3. **窗口私有面冻结**：`PetWindow` 的 `win._xxx` 只允许 `window.py` 自身与 `collision_client.py` 访问；`app.py` / `agent_link.py` / `context_menus/` 出现即为违规。
4. **`window.py` 行数预算**：当前预算 `4429` 行（红线常量见 `tests/test_architecture.py`，实测 4429 行，2026-09-12 校准；含 #101/#102 启动装配与探头闸门、#108 弹射飞行守卫、#109 探头旋转/软撞位移、#112 会话结束的 `_closing` 守卫）。预算只随实测校准，**不靠压缩行宽/合并语句硬塞**；确需上调要在 PR 说明理由。
5. **`modern_settings_dialog.py` 行数预算**：当前预算 `2018` 行（实测 1826 行，已拆到 `settings_widgets` / `settings_menu_layout_editor` / `chat/ai_settings_page` / `settings_theme_qss`），再往主对话框塞新页面属于红线。
6. **孤儿簇守卫**：`settings_widgets.py`、`settings_styles*.qss` 等文件不允许“存在但零引用”——要么删除，要么真正接线；防再发由测试守护。

### 文件 / 结构演进规则

- `window.py` 处于 **“只许瘦不许胖”** 的增量拆分公约下：新功能预计超过约 100 行、需改 3 个以上同域方法、或行数预算告警时，先按 `docs/WINDOW_PY_SPLIT_GUIDE.md` 拆控制器。
- 拆分应**机械搬移、不夹带行为变更**；保留薄委托/兼容面；PR 里说明拆分域、迁出字段与共享字段。
- 新增普通顶层配置键必须三处同步登记：**默认值 dict + reload 白名单 + `test_config_schema.py` 快照**；特例键（version / proactive_screen / agent_link / chat）走专门迁移路径，不塞普通白名单。
- 单进程多开的设置作用域：**每窗独立项**（形象/位置/聊天等）存 `config-slot-N.json`；**进程级共享项**（托盘/共享解码/Agent 联动/待办提醒等）以主桌宠 `config.json` 为准，非主窗修改不生效。
- 新增子桌宠只在 slot 无存档时从主配置落种，**已有用户存档一律保留**。

### CI 与测试纪律（`AGENTS.md`“CI cost discipline”）

- 推送前必须过三道本地门：**ruff、全量 pytest、受影响时序测试族高负载复跑 3 遍**；缝合/脚本化改动后必须重跑 ruff。
- 新测试涉及真实线程/Qt 事件循环时，必须**事件同步 + 宽预算**；禁止固定 sleep 猜时序、禁止赌目录枚举顺序、禁止用 monotonic 绝对值做回拨算术（CI runner 可能刚开机）。
- 同一族时序测试连续两轮不绿就停止重试，按既有先例**隔离出主套件**，不要在 PR 门禁里赌时序。
- 能本地复现的诊断不派付费子代理；派子代理必须给齐已知排除项。

## 目录

- [版本亮点（v4.2.0）](#版本亮点v420)
- [开发约束与规则](#开发约束与规则)
- [项目来源与素材声明](#项目来源与素材声明)
- [当前状态](#当前状态)
- [下载与版本选择](#下载与版本选择)
- [安装教程](#安装教程)
- [快速开始（安装之后）](#快速开始安装之后)
- [功能概览](#功能概览)
- [使用教程](#使用教程)
- [AI 对话使用教程（Chat 版）](#ai-对话使用教程chat-版)
- [动画素材与自定义角色](#动画素材与自定义角色)
- [开发结构](#开发结构)
- [测试与验证](#测试与验证)
- [打包发布](#打包发布)
- [旧版 onefile 缓存清理（仅旧版本需要）](#旧版-onefile-缓存清理仅旧版本需要)
- [配置与安全说明](#配置与安全说明)
- [最近修复与变更记录](#最近修复与变更记录)
- [已知限制](#已知限制)
- [项目文档](#项目文档)
- [许可证与致谢](#许可证与致谢)

---

## 版本亮点（v4.2.0）

v4.2.0 是自 v4.1.0 以来的**开发增量版**，主线是「内存长稳与单进程多开」「设置与菜单可配置化」「边缘交互玩法」「Agent/DSH 生态与桌面兼容」。完整发布说明见 [`docs/RELEASE-v4.2.0.md`](docs/RELEASE-v4.2.0.md)。

**内存与多开**
- **单进程多开**：设置 → 常规 → 多开开启后，「生小肥鱼」在本进程内孵化多只桌宠（独立 slot / 配置 / 会话 / 素材库）。实测 3 窗 1 进程 181–197MB，3.5h 无单调上涨（多进程模式约 270MB/3 只）。
- **同角色共享解码链**：同角色多窗只保留 1 条 ffmpeg 解码链（进程内帧扇出），其余窗口订阅进食。
- **托盘聚合与 slot 落种**：单托盘 + 每窗子菜单；新子肥鱼首占 slot 时从主配置落种，已有存档一律保留；「退出子肥鱼」**只退出、不删除**设置/会话/待办。
- **省电模式**：闲置 30s+ 半帧率呈现并停止后台预热，交互立即回满（解码 CPU 实测 −54.6%）。

**设置与菜单**
- **七能力域设置页** + **右键菜单可编排**：显隐、子菜单、分割线、别名、图标覆盖（内置矢量/本地图片 ≤5MB），左侧编辑、右侧实时预览；损坏配置自动回退安全菜单。
- **快捷启动**编辑与应用列表、菜单主题即时生效、图片目录预览抽屉（3 列瀑布流）、三态响应式（1600/900/720px）。

**玩法与提醒**
- **黄金回旋**（右键入口 + 点击触发/直连两个开关，连点逐圈加速）、**边缘探头**（±45° 贴边、点击拉直）、**撞飞彩蛋**（被撞翻鱼头、落地 5s 重新吸附）、**拖文件模拟吃掉**、动画素材 97 → 106。
- **待办提醒**：右键「待办」面板 + 到期/提前提醒（提前量可设）、10 分钟宽限、一次性项自动归档。
- **气泡**：配图大小 50–300% 可调；台词模板 JSON 导入/导出 + v2 占位符渲染；表达风格统一入口。

**Agent 联动与 DSH**
- **DSH 富事件状态**（thinking / working（带工具名）/ attention / error）、审批与提问气泡（多问题项、interaction_id 多交互并存）、**探索循环 Watchdog 控制气泡**（自动优化 replan / 终止 interrupt / 忽略，GUI 不阻塞）。
- **启动体验**：Harness 复用本机已有实例（不再拉第二个浏览器）、`harness_autostart` 随桌宠起服务、探测链路静默化、macOS Finder Node 解析。

**跨平台、安全与兼容**
- Windows 打字频闪根治（原生 `WS_EX_TRANSPARENT`）、macOS Dock 隐藏彻底生效 + 原生 Dock 菜单、Linux Fcitx 中文输入随包插件；明文 API Key 自动迁入 keyring；直播捕获（OBS/直播姬）气泡兼容；DLC/换角色加固（缺失动画不崩、启动回退默认角色、点击台词支持外部素材目录）。

<details>
<summary><b>历史版本亮点（v4.0.0）</b></summary>

## v4.0.0 版本亮点

v4.0.0 是一次大版本升级：在 v3.1.1 的桌宠基础上，合并了社区贡献者的现代桌面体验重构（PR #11/#12/#13）与性能/主动陪伴体系（PR #7），并完成多轮用户反馈修复与新增功能。以下是本版新增能力的总览：

**桌面体验重构（现代双 UI）**

- **新版右键菜单**：紧凑七组布局 + 线性图标、顶部彩蛋入口、半透明表面、可跟随系统/浅色/深色的主题、UI 字体/字号/密度/圆角可调；「切换菜单模板」可一键回到旧版经典菜单。
- **新版设置对话框**：侧边栏 + 卡片式布局，常规 / 桌宠行为 / 外观 / AI 对话 / 快捷启动 / 主动识屏多个页面；所有改动**关闭即自动保存并立即生效**（含直接点 X）。
- **新版 AI 对话窗口**：现代双栏工作台（左侧会话管理 + 右侧消息画布），自绘标题栏、会话搜索、批量管理、跟随桌宠、背景主题（内置壁纸/自定义图片/裁剪取景）；保留旧版手机式聊天窗可随时切换。
- **彩蛋入口（欧鲸鲸）**：新版菜单首行可配置的趣味入口，点击随机弹出一张图片；弹窗时机、图片回退、多开层叠都已打磨。
- **「生小肥鱼」多开**：从菜单一键孵化第二只独立桌宠，自动避让位置、配置与会话相互隔离。

**性能与主动陪伴（PR #7）**

- **隐藏即零功耗**：桌宠隐藏后暂停动画解码与全部活动定时器（实测隐藏后 CPU ≈ 0%），显示时立即恢复。
- **启动懒加载**：动画素材按需加载与优先级预热，冷启动更快。
- **主动识屏（Windows + Chat 版）**：可选的"主动识屏陪伴"——白名单应用切换、停留时长门限、每日上限与冷却、dry-run 验证模式，截图不落盘。
- **Agent 联动**：内置 DSH 桥接插件与 Claude hooks 安装器，桌宠可感知 AI Agent 干活状态并切换动作/冒泡。

**稳定与修复批次（多轮用户反馈 + 三方审查）**

- 设置保存链路：X 关闭自动保存、保存即生效（不再需要点「保存」）、钥匙串/文件双通道。
- 深色系统全面适配：设置界面、菜单、聊天窗按钮/图标在 Windows 深色模式下不再白底白字。
- 聊天窗渲染修复：无边框圆角窗口（去掉窗外方形背景）、图标颜色跟随界面主题、缩放光标不再卡住。
- 并发与内存：打字机串写会话、菜单泄漏、ChatService 竞态、子进程回收等一批高危修复。

**新增桌宠功能（本版）**

- **锁定位置**：桌宠固定不动、无法拖动（点击互动仍有效）。
- **SHIFT+左键拖动**：开启后必须按住 SHIFT 才能拖动桌宠。
- **不透明度**：桌宠窗口 10%–100% 可调。
- **托盘菜单同步**：鼠标穿透 / 开机自启在设置里改动后，托盘菜单勾选状态实时同步。
- **旧版聊天窗补全**：会话重命名（含深色主题适配）。

</details>

<details>
<summary><b>项目来源与素材声明</b></summary>

## 项目来源与素材声明

本项目改自、源于 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。桌宠的基础交互思路、动画链行为模型和部分资源组织方式来自原项目，感谢原作者的开源贡献。

DeepSeek 余额显示（气泡/小部件思路）参考了 [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)，本项目的实现为桌宠内置的轻量版（菜单「DeepSeek 余额」+ 可选自动刷新，通过 DeepSeek 官方 `/user/balance` 接口查询，详见 [DeepSeek API 查询余额文档](https://api-docs.deepseek.com/zh-cn/api/get-user-balance/)）。

当前动画素材已同步参考项目近期更新后的高清 WebM 资源。项目以 WebM 目录为动画源；`assets/characters` 包含 106 个 WebM 动画文件。GIF 目录仅在构建 GIF 变体时生成。后续新增或替换动画时，请更新 WebM，需要构建 GIF 变体时再生成对应 GIF。


</details>

<details>
<summary><b>当前状态</b></summary>

## 当前状态

- **开发版 main（未发布，v4.2.1 候选，2026-09-12）**：v4.2.0 之后又并入 PR #97（事件汇报概率门 + 事件链路语义对齐 + issue #95：Windows node/pnpm 解析不再写死）、#100 / #102（启动即按配置装配可选服务，issue #99）、#101（边缘探头会话期间禁止位移）、#104（桥接插件归零外部依赖——修「装完 dsh 全 profile 起不来」）、#105（DSH 事件层系统性收尾）、#107（余额多币种）、#108（弹射卡顿）、#109（探头/头槌 + 气泡分页）、#112（Windows 关机/注销弹 `0xc0000142`，issue #111）——详见下方 [最近修复与变更记录](#最近修复与变更记录)。
- **v4.2.0（2026-09-10）**：自 v4.1.0 以来的功能与修复汇总，含 PR #57/#64/#65/#68/#70/#71/#72/#73/#76/#79/#80/#82/#85/#86/#87/#90/#91/#93/#94/#96——桌宠长时运行内存稳定（多进程模式 ~270MB/3 只；单进程多窗 3 窗共 1 进程约 181–197MB，3.5h 无单调上涨）；单进程多窗共享解码（同角色多窗 1 个 ffmpeg 解码链）；设置与菜单重构（右键菜单可编排、快捷启动、主题即时生效）；边缘探头/黄金回旋/撞飞彩蛋/省电模式；待办提醒；DSH 事件层与探索 Watchdog 控制链；子肥鱼「只退出不删数据」；台词模板 v2 与表达风格；打字频闪根治；气泡行尾字裁切修复；DLC 换角色加固。完整清单见 [`docs/RELEASE-v4.2.0.md`](docs/RELEASE-v4.2.0.md)。
- **v4.1.0（累计版）**：自 v4.0.0 以来的功能与修复汇总——多开碰撞、灵动岛、快速对话气泡、自定义 Agent 联动通道、API/Provider 列表、右键菜单 LTR、三平台 CI 等（PR #36/#39/#40/#41/#44/#46/#47/#49/#50/#52/#53/#54/#55/#56/#59/#60）。
- **v4.0.5**：功能版——音效体系升级（点击音效包/Agent 联动音效）、甩出力度档位、弹弓弹射、光标隐藏自动穿透、点击 Q 弹卡顿修复、自启变体独立（PR #33/#34/#35）。
- **v4.0.4**：功能版——余额分档动画、DeepSeek 峰谷提示（可自定义文案与颜色）、后台音乐自动唱歌、点击音效打断、移动动画调整、位置记忆修复、自启残留清理、thinking 专属气泡文案等（PR #29/#30/#31/#32）。
- **v4.0.3**：紧急修复版——修复 Windows 透明像素点击穿透、DSH 桥接插件自动安装 pnpm，以及 Windows 官方包中文乱码（PR #27/#28）。
- **v4.0.2**：v4.0.1 的修复版——自定义点击音效支持 MP3/OGG/FLAC（不再仅限 WAV）、动画边缘毛边与帧率精度修复、右键菜单懒加载与智能避让、设置期间暂停气泡、macOS/Linux 补打包 integrations 资源（DSH 桥接一键安装）、Chat 版显式收集 keyring（API Key 系统安全存储）等（详见下方「最近修复」）。
- **v4.0.1**：v4.0.0 的修复版——修复 Windows「自动隐藏任务栏」下桌宠随任务栏误隐藏（PR #18）与副屏位置开机自启不恢复（PR #16，issue #8），并含主动识屏并发、DSH 桥接安装加固等修复（详见下方「最近修复」）。
- **v4.0.0**：Windows 发布 WebM 两个版本（Chat 版与无 Chat 版），均提供安装包与绿色版；macOS（Apple Silicon）与 Linux（x86_64）由 GitHub Actions 构建发布。
- 安装包免管理员、按当前用户安装，向导中可自由选择安装盘符与目录；卸载后无残留运行缓存。
- 绿色版解压即用、删除即卸载，可放在任意盘符或 U 盘。
- 右键菜单/托盘菜单默认使用**新版现代风格**（可一键切回旧版模板）；设置对话框与 AI 对话窗口均为新版现代双栏布局，旧版手机式聊天窗保留可切换。
- 桌宠隐藏后动画与定时器全部暂停（低功耗），显示时立即恢复。
- 桌宠支持**锁定位置**、**SHIFT+左键拖动**与**不透明度**设置。
- 可选「主动识屏陪伴」（Windows + Chat 版，默认关闭）与 Agent 联动（DSH 桥接 / Claude hooks，默认关闭）。
- WebM 播放速率设置可调，切换动画后仍按当前速率播放；支持相邻非待机动画之间的可选等待间隔。
- 支持可开关的随机自言自语气泡，并优先定位在角色当前可见形象的正上方。
- AI 对话窗口为独立窗口（现代双栏或经典手机式），不改变桌宠主窗口的透明背景、mask、鼠标穿透和动画状态机。


</details>

<details>
<summary><b>下载与版本选择</b></summary>

## 下载与版本选择

正式发布时请以 [Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases) 页面实际上传的文件为准。当前推荐下载的 Windows 产物如下：

| 版本 | 安装包（setup.exe） | 绿色版（zip） | 适合场景 |
|---|---|---|---|
| Chat WebM | `dsh-pet-standalone-webm-chat-setup.exe`（约 128 MB） | `dsh-pet-standalone-webm-chat-portable.zip`（约 156 MB） | WebM 高清播放 + AI 对话，功能完整 |
| 无 Chat WebM | `dsh-pet-standalone-webm-setup.exe`（约 128 MB） | `dsh-pet-standalone-webm-portable.zip`（约 156 MB） | 只想要桌宠本体，不接入 AI |

选择建议：

- **想体验完整功能（含 AI 对话）**：装 Chat 版。
- **只需要桌宠陪伴**：装无 Chat 版，包体更小、启动更轻。
- **不想安装、追求便携**：用绿色版 zip，解压到任意目录双击即用。

> 两个版本使用同一套高清 WebM 素材（106 段动画），只是入口不同：Chat 版会加载聊天子系统，无 Chat 版完全不携带 AI 对话依赖。
>
> 旧版 GIF 超大单文件（约 800 MB，运行时会在 C 盘临时目录解压并可能残留缓存）不再默认发布；确有需要可参考本文档「打包发布」一节自行构建 GIF 变体。
>
> macOS（Apple Silicon）用户：产物为 `dsh-pet-standalone-<webm-chat|webm>-macos-arm64.zip`（onedir .app），由 GitHub Actions 构建，见下方「macOS 使用」。GIF 变体自 v4.0.0 起不再发布，需要请自行构建。
>
> Linux（x86_64）用户：产物为 `dsh-pet-standalone-*-linux-x86_64.zip`（onedir 目录，解压即用），由 GitHub Actions 构建，见下方「Linux 使用」。


</details>

<details>
<summary><b>安装教程</b></summary>

## 安装教程

### 方式一：安装包（setup.exe）安装

1. **下载**：选择 `dsh-pet-standalone-webm-chat-setup.exe`（或无 Chat 版）放到任意位置。
2. **双击运行**：如果出现 Windows SmartScreen 提示，点「更多信息 → 仍要运行」（软件尚未购买代码签名证书）。
3. **选择语言**：向导默认简体中文，也可切换 English，点「下一步」。
4. **选择安装目录**：
   - 默认目录为 `%LOCALAPPDATA%\Programs\dsh-pet-standalone-webm-chat`（当前用户目录，**不需要管理员权限**）；
   - 想装到其他盘符（如 `D:\`、`E:\`），点「浏览」自己选一个目录即可。
5. **附加任务**：可勾选「创建桌面快捷方式」（默认不勾选）。
6. **完成**：勾选「运行 dsh-pet-standalone-webm-chat」会立即启动桌宠。
7. **首次启动**：桌宠出现在屏幕右下角；系统托盘出现常驻图标（右键托盘可打开菜单）。

**常见问题**

- **找不到桌宠了？** 看系统托盘（可能收在「显示隐藏的图标」里），双击托盘图标可显示/隐藏桌宠。
- **想开机自启？** 右键托盘 → 勾选「开机自启」即可（写入当前用户注册表 Run 键，无需管理员）；也可以在「桌宠设置」中开启。
  - **自启不生效怎么办**：① 安全软件/系统优化工具（360、电脑管家、Defender 等）可能拦截或清理未签名程序的自启项——请到其"开机加速/启动项管理"中恢复；② 程序每次启动会自检：若发现"之前开启过但已被清理"，桌宠会气泡提醒；③ macOS 新版系统需在「系统设置 → 通用 → 登录项」中允许桌宠（勾选时也有气泡提示）。
- **配置存在哪里？** 设置与聊天会话保存在各版本独立的数据目录（重装/升级不会丢失）：
  - **Chat 版**：`%APPDATA%\dsh-pet-standalone-webm-chat\`
  - **无 Chat 版**：`%APPDATA%\dsh-pet-standalone-webm\`
  - **源码运行**：`%APPDATA%\dsh-pet-standalone\`

### 方式二：绿色版（zip）免安装

1. 下载 `dsh-pet-standalone-webm-chat-portable.zip`。
2. 解压到任意可写目录（例如 `E:\dsh-pet\`），**保持文件夹内结构完整**。
3. 双击文件夹里的 `dsh-pet-standalone-webm-chat.exe` 即可运行。
4. 删除整个文件夹即完成卸载，不残留任何运行缓存。

> 绿色版与安装版是同一套 onedir 产物，运行行为完全一致；区别只是安装版多了快捷方式与卸载器。

### 卸载

- **安装版**：`设置 → 应用 → 已安装的应用`（或「控制面板 → 程序和功能」）→ 找到 `dsh-pet-standalone (WebM Chat)` → 卸载。
- 卸载程序会删除安装目录与快捷方式；各版本的数据目录（见上方「配置存在哪里」）中的配置与会话默认保留，如需彻底清除可手动删除对应目录。

### 升级

- **安装版**：直接运行新版 setup.exe 覆盖安装即可，配置与聊天会话不受影响。
- **绿色版**：用新版 zip 解压覆盖旧文件夹即可。


</details>

<details>
<summary><b>快速开始（安装之后）</b></summary>

## 快速开始（安装之后）

1. 桌宠默认出现在屏幕右下角，播放待机动画。
2. 右键桌宠打开菜单；**左键点击**触发互动动画，**按住拖动**可移动桌宠。
3. 首次使用建议打开「设置」：右键桌宠 → 桌宠设置（或托盘菜单 → 桌宠设置）。
4. Chat 版额外提供「AI 对话」和「AI 设置」入口；无 Chat 版不会显示。

### 方式三：macOS（Apple Silicon）

1. **获取**：GitHub Actions 页面手动运行 `Build macOS App`（或打 `v*` tag 自动发布），从 Release / Artifacts 下载 `dsh-pet-standalone-webm-chat-macos-arm64.zip`（或无 Chat 版）。
2. **解压**：得到 `dsh-pet-standalone-webm-chat.app`，可拖入「应用程序」文件夹。
3. **首次打开**：应用未签名（ad-hoc codesign），Gatekeeper 会拦截——**右键 .app → 打开**，或终端执行：
   ```bash
   xattr -dr com.apple.quarantine dsh-pet-standalone-webm-chat.app
   ```
4. **数据目录**：`~/Library/Application Support/dsh-pet-standalone-<变体>/`（各变体相互独立，与 Windows 行为一致）。
5. **开机自启**：托盘/右键菜单勾选「开机自启」（按变体生成独立 LaunchAgent）。
6. **启动 DeepSeek Harness**：需安装 Node.js（`brew install node`）；启动器会自动探测 Homebrew/nvm 等路径并回退 `npx @deepseek-ai/dsh`。
7. **关闭 Dock 图标**：在「桌宠设置 → 常规 → 显示 Dock 图标」取消勾选后，Dock 隐藏会彻底生效——隐藏桌宠也不会把 Dock 图标临时唤回；恢复入口是菜单栏托盘图标（显示 / 隐藏、鼠标穿透、桌宠设置）。开启鼠标穿透或关闭 Dock 图标时，桌宠会气泡提示恢复位置。

> Intel Mac：当前 CI 只构建 arm64；Intel 用户请从源码运行（见下），或在 Intel 机器上自行构建。

### 方式四：Linux（x86_64）

1. **获取**：GitHub Actions 页面手动运行 `Build Linux App`（或打 `v*` tag 自动发布），从 Release / Artifacts 下载 `dsh-pet-standalone-webm-chat-linux-x86_64.zip`（或无 Chat 版）。
2. **解压**：得到 `dsh-pet-standalone-webm-chat/` 目录，运行其中的同名二进制：
   ```bash
   unzip dsh-pet-standalone-webm-chat-linux-x86_64.zip
   cd dsh-pet-standalone-webm-chat
   chmod +x dsh-pet-standalone-webm-chat   # 一般无需，zip 已保留可执行权限
   ./dsh-pet-standalone-webm-chat
   ```
3. **首次运行缺库**（PySide6 需要少量系统库，常见发行版需安装）：
   ```bash
   # Debian / Ubuntu / Mint 等（其他发行版请找对应包名）
   sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 \
                    libfontconfig1 libdbus-1-3 fonts-noto-cjk
   ```
   - `fonts-noto-cjk` 用于中文显示（缺失时气泡/聊天中文会显示为方块）。
   - 默认按 X11 运行；Wayland 会话下若透明/置顶异常，可试 `QT_QPA_PLATFORM=xcb ./dsh-pet-standalone-webm-chat`。
4. **数据目录**：`~/.config/dsh-pet-standalone-<变体>/`（各变体相互独立，与 Windows/macOS 行为一致）。
5. **开机自启**：托盘/右键菜单勾选「开机自启」（写入 `~/.config/autostart/` 的 .desktop 文件）。
6. **点击音效**：自动使用系统 `paplay`（PulseAudio）或 `aplay`（ALSA）；两者都没有时静默跳过。
7. **启动 DeepSeek Harness**：需安装 Node.js；启动器会自动探测 PATH 并回退 `npx @deepseek-ai/dsh`。

> 建议在 X11 桌面（GNOME/KDE/Xfce 等）上使用；托盘图标依赖桌面环境的系统托盘支持（GNOME 需安装 AppIndicator 扩展）。

### 从源码运行（开发者）

建议使用 Python 3.10 或更高版本（CI 使用 Python 3.11，Windows 实机开发验证覆盖 Python 3.13），并在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pet
```

Windows 也可以直接双击 `run.bat`。它实际执行的是：

```text
pythonw -m pet
```

源码入口默认包含 Chat 能力；如果只想验证桌宠核心功能，可使用无 Chat 的打包入口或在本地配置中关闭聊天。


</details>

<details>
<summary><b>功能概览</b></summary>

## 功能概览

### 桌宠窗口

- PySide6 透明、无边框、置顶窗口（无边框圆角窗口，窗外无方形背景）。
- 支持点击互动、拖动、拖动惯性、方向转向和系统托盘。
- **锁定位置**：设置开启后桌宠不可拖动（点击互动仍有效）。
- **SHIFT+左键拖动**：设置开启后必须按住 SHIFT 才能拖动桌宠。
- **不透明度**：桌宠窗口透明度 10%–100% 可调，保存立即生效。
- 支持角色切换；角色目录按素材自动发现，不要求把角色写死在代码中。
- 右键菜单默认使用**新版现代菜单**（紧凑分组 + 线性图标 + 半透明表面），可跟随系统/浅色/深色主题，UI 字体/字号/密度/圆角/浅深主题色均可调；「切换菜单模板」可随时切回旧版经典菜单。
- 新版菜单首行彩蛋入口（欧鲸鲸，可配置头像/标题/图片目录）与「快捷启动」均可配置；「生小肥鱼」会启动一只独立的新桌宠（自动避让位置、配置隔离）。
- 右键菜单与托盘菜单提供「启动 DeepSeek Harness」：一键后台拉起 `dsh web`（默认端口 38080，可用环境变量 `DSH_PORT` 覆盖；注：3080 在部分 Windows 上会落入 winnat/Hyper-V 保留段导致无法监听）并自动打开浏览器；启动命令自动适配不同安装方式（PATH 上的 `dsh` → node + npm 全局包 → 官方 `npx @deepseek-ai/dsh`），macOS 同样可用（.app 环境会额外探测 Homebrew/nvm 等常见目录，需装有 Node.js）。若本机已有 dsh web 在运行（38080 或官方默认 3080），直接复用并打开现有实例，不再重复拉起。首次运行时 npx 拉取组件 + dsh 自举（可能出现 npm/pnpm 安装窗口）在网络较慢时需要几分钟属正常现象，桌宠会冒泡提示启动中；想跳过首次下载可提前手动 `npm install -g @deepseek-ai/dsh`。设置中另有「随桌宠启动 dsh 服务」开关：开启后桌宠启动即自动在后台静默拉起 dsh web（只起服务，不开浏览器、不弹窗口），之后点菜单秒开页面。
- 托盘菜单（鼠标穿透 / 开机自启）勾选状态与设置实时同步。

### 新版设置对话框

- 侧边栏 + 卡片式布局：常规 / 桌宠行为 / 外观 / AI 对话 / 快捷启动 / 主动识屏（Windows + Chat 版）。
- **关闭即自动保存并立即生效**：直接点 X、ESC、保存按钮都会落盘并刷新桌宠（此前需要点「保存」）。
- 深色系统下全界面可读：自绘开关、下拉、选项弹窗、颜色块均适配明暗主题。
- 彩蛋入口、菜单外观（颜色/圆角/半透明/字体）、点击音效、余额自动刷新等集中管理。

### 动画播放

- WebM 版：直接播放透明 WebM，默认素材为 640×360、24fps。
- 播放速率可在设置中调整，当前范围为 `1.0x` 到 `2.0x`。
- 动画按 `idle`、`turn`、`move`、`click`、`drag`、`random` 等目录组织。
- 支持相邻非待机动画之间的等待间隔；等待期间只播放待机和转向动画。
- 支持随机自言自语气泡；没有自定义文本时使用内置文本。
- 素材懒加载 + 优先级预热：冷启动更快，隐藏时零解码。

### AI 对话（Chat 版）

- **新版双栏工作台**：左侧会话导航（搜索/重命名/置顶/批量管理）+ 右侧消息画布；**经典手机式窗口**保留可切换。
- 支持 OpenAI Chat Completions 兼容接口；自定义 API 地址、模型、超时、温度和最大输出 token。
- 支持 SSE 流式输出、多轮上下文裁剪、会话 JSON 持久化、停止生成、失败重试。
- 会话按角色隔离；切换角色时不会把旧角色消息带入新角色。
- 聊天窗靠近桌宠显示，并支持选择是否跟随桌宠移动；背景支持内置主题壁纸 / 自定义图片 / 裁剪取景。
- API Key 优先使用系统钥匙串；钥匙串不可用时可按设置选择配置文件回退。
- 纯文本安全显示，不包含完整 Markdown 渲染器。

### 主动识屏陪伴（Windows + Chat 版，默认关闭）

- 白名单应用切换时以桌宠口吻冒泡关怀（截图 + 前台窗口上下文 → 视觉模型）。
- 停留时长门限、闲置判定、冷却间隔、每日上限、免费模型优先、dry-run 验证模式。
- 截图仅在内存中压缩处理并直接发送给视觉模型，不写入本地文件、不保留副本。

### Agent 联动（默认关闭）

- 内置 DSH 桥接插件（`integrations/dsh-pet-bridge`）与 Claude hooks 安装器：感知 AI Agent 状态并切换动作，支持开始干活、过程汇报、任务完成三种气泡反馈，右键 Agent 联动子菜单可独立开关。
- **DSH 富事件状态**：thinking（思考）/ working（干活，带工具名）/ attention（需确认）/ error / idle 多态呈现，多会话按 attention > error > working > thinking > idle 聚合，子代理不抢状态；审批与提问气泡支持多问题项（气泡内多选提交），同一 Agent 的并发审批/提问按 `interaction_id` 互不覆盖。
- **事件汇报概率门（PR #97）**：联动气泡控制从布尔开关升级为**概率门**（0.00–1.00），8 个事件聚合类别各一个滑块（默认 `activity=0.6`、其余 `1.0`）；旧开关/百分比自动迁移，右键菜单保留 0/1 两端快捷入口。概率门只管气泡这一步，检测器与原始记录链不采样。
- **探索循环 Watchdog 控制（PR #91）**：风险分达阈值时发常驻气泡，带「自动优化（replan）/ 终止（interrupt）/ 忽略」；控制请求最长阻塞 30s，按钮回调只收气泡 + 起后台线程、结果经 Qt 信号回主线程（GUI 不阻塞），回执按相位区分文案，会话结束自动收起控制气泡。
- **开机即按配置装配（PR #100 / #102）**：重启后已开启的 Agent 联动与主动识屏直接生效，不再需要手动展开一次菜单或开关一次设置对话框。
- **自定义联动 Agent**：在 `config.json` 的 `agent_link.custom_agents` 里声明任意 Agent（key / 显示名 / 事件文件路径），桌宠即对其 JSONL 事件文件做只读监听，联动行为与内置 Agent 一致——不改代码即可接入任何能写本地文件的 Agent，协议详见 `docs/AGENT_LINK_PROTOCOL.md`。
- **一键启动 DSH**：本机已在跑 dsh web（含官方 3080）时直接复用，不再拉起第二个实例；Windows 上 node/pnpm 解析覆盖 nvm-windows/nvm/fnm/Volta/scoop 等布局（PR #97 修掉「已装 pnpm 仍装不上插件」，issue #95），也可用 `pnpm_bin` 配置键手动指定 pnpm 入口。

### 看看屏幕（Chat 版）

- 右键菜单 →「看看屏幕」：截取当前屏幕（含多显示器）→ 附带前台窗口「程序名 | 标题」上下文 → 发给视觉模型，用人设口吻回应一句（关心/吐槽/好奇），结果以气泡显示。
- **回复会自动同步到 AI 对话当前会话**（一条 `[看看屏幕] 前台窗口：…` 记录 + 一条回复），可继续追问；聊天窗未打开时仅气泡显示、不写入。
- 截图自动压缩（最长边 768px、JPEG 70）后仅在内存中处理并直接发送到你配置的模型服务商，不写入本地截图文件、不保留副本；不发送到本项目自建服务器，请你遵循所配置模型服务商的隐私政策。
- 视觉模型在 AI 设置中配置：可手填模型名/独立端点/独立密钥，或勾选「同聊天模型」复用聊天配置；DeepSeek 聊天模型会自动映射到预览版视觉模型。
- **注意：每次「看看屏幕」都会按一次视觉模型请求计费，消耗对应模型的 token**（截图按像素折算 + 回复输出）；有 4 秒冷却防连点，免费档高峰可能遇到限流（稍后重试即可）。

### DeepSeek 余额（Chat 版）

- 右键菜单或托盘 →「DeepSeek 余额」：查询 DeepSeek 开放平台账户余额，以气泡显示（如"余额 ¥12.34（充值 ¥10.00 / 赠送 ¥2.34）"）。
- 数据来自 DeepSeek 官方 `/user/balance` 接口，使用当前配置的 API Key 鉴权（需使用 DeepSeek 官方端点）；查询结果 30 秒内缓存复用，重复查询秒回。
- 桌宠设置可开启「余额自动刷新」（分钟级，0=关闭），到点自动查询并气泡显示；也可开启「点击显示余额」（与点击自言自语自动排队）。
- 实现参考 [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)（见文首声明）。

### 点击音效

- 点击桌宠触发 Q 弹时播放短促音效（内置合成音，可在桌宠设置中关闭）。
- 可自定义声音：把 `click.wav` 放到桌宠数据目录 `sounds/` 下即可替换内置音效。

### 待办提醒

- 右键菜单 →「待办」面板增删改（带矢量图标），数据按实例落盘（`todo_items[-instance].json`，原子写）。
- 到期提醒 + 提前提醒（`todo_reminder_lead_minutes`，默认提前 5 分钟）、10 分钟宽限；一次性待办到期自动归档；错过宽限的提醒静默盖戳，不会一次轰炸。
- 提醒形式：桌宠可见时用气泡，否则走桌面通知（由 `system_notifications_enabled` 总门控，默认开）；总开关与提前量在「设置 → 桌宠行为 → 待办提醒」。

### 边缘交互与彩蛋玩法

- **黄金回旋**：右键菜单新增「黄金回旋」入口；「设置 → 桌宠行为 → 点击反馈」可开「点击触发黄金回旋」（点击动画播完自动接回旋）与「点击回旋跳过动画」（点击直接回旋、跳过 Q 弹/点击素材）；连点逐圈加速（700ms → ×0.82 → 200ms 下限）。
- **边缘探头**（`edge_probe_enabled`，默认关）：拖到屏幕左右边缘自动进入 ±45° 探头姿态（常驻露出 0.55、点击拉直 0.82、数秒自动退回）；探头激活时点击不播点击音效，**且移动被闸门拦住**（不会被平移出屏幕边缘，PR #101）。
- **撞飞彩蛋**：探头激活状态被撞飞时头部跟随速度方向整帧旋转，低速触碰边界/其它桌宠回正，落地停稳 5 秒后重新吸附。
- **拖文件模拟吃掉**：把文件拖到桌宠身上播放吃动画并记录统计（**不真实删除文件**）。

### 右键菜单编排（可配置菜单）

- 「设置 → 菜单」左侧编辑、右侧**实时预览**：显隐勾选、移动到子菜单/根、新建子菜单、插入/删除分割线、别名（运行时只显别名）、图标覆盖（内置矢量 / none / 本地图片 ≤5MB，contain/cover）、恢复默认名称/图标/布局、删除子菜单（二次确认，子项提升回根）。
- 配置缺失回默认模板；损坏/不支持的 schema 只保留「桌宠设置 / 退出」安全菜单；旧自定义树自动补入新默认项；动作缺条件置灰 + tooltip。
- 同页 Tab 还有「快捷启动」（双行应用列表编辑，无配置时子菜单显示禁用占位「尚未配置快捷项」）与「外观」（主题即时生效、开关从属项「开显关隐」）。

### 省电模式

- 在「设置 → 常规 → 动画与移动」开启「省电模式」后：桌宠一段时间无交互时动画按半帧率呈现（24fps 素材 → 12fps 效果），任何交互立即恢复全帧率；同时停止后台动画预热（不再预载非核心动画的首帧，进一步省 CPU 与内存）。
- 默认关闭；多开时每只各自独立设置。

### 性能与内存治理（PR #76）

- 实测驱动：修复前多开 1-2 小时后每只涨到 220MB+/只且不回落；修复后多进程模式长时运行稳定（3 只合计约 270MB），**单进程多窗模式 3 窗共 1 进程、3.5h 浸泡在 181–197MB 区间震荡、无单调上涨**。
- 解码链收敛：同角色多窗共享一条进程内解码链（`DecodeFanoutHub`），3 窗只启动 1 个 ffmpeg 解码进程；ffmpeg 固定 `-threads 1`，并按 `ffmpeg_recycle_minutes`（默认 10 分钟）在圈边界定期回收，避免子进程内存无限爬升。
- 首帧缓存预算：默认 `first_frame_cache_max_mb=8`（4–64 可配），只保留点击/转向/拖拽等瞬时交互核的 pinned 缓存，idle/move 交给预测式预热与 LRU，避免“每播一段新动画就 +1.76MB 不释放”的慢涨。
- 启动与内存降载：未启用点击/碰撞音效时不拉起 QtMultimedia（省约 38MB）；PIL 只在“看看屏幕/主动识屏”截图路径懒加载。
- 频闪根因修复：全屏自动隐藏排除截图覆盖层/工具窗口；Windows 光标穿透改用原生 `WS_EX_TRANSPARENT`，不再 `setWindowFlag` 重建原生窗口。

### 单进程多开与共享解码

- 正式特性（默认关闭）：在「设置 → 常规 → 多开」开启「单进程多开（省内存）」并重启后，「生小肥鱼」在同一进程内创建新桌宠：多只宠物只占一个进程，且空闲时多只播的是同一份待机素材，由进程内帧扇出（`DecodeFanoutHub`）统一解码——待机时 ffmpeg 解码进程从 N 个减到 1 个，解码 CPU 与内存显著降低；机制与平台无关。
- 每只桌宠的设置存档（含位置、外观）在多开模式间通用，切换开关不会丢配置。
- 失败无感回退：无发布者、断流等任何情况下，消费端都会自动回退本地解码，播放行为与关闭时一致。

**单进程多开下的设置作用域**：

- **每只独立**（右键某只 → 桌宠设置，改哪只影响哪只）：形象/缩放/透明度/置顶、拖拽物理/弹射/音效、自言自语、省电降帧、AI 对话内容与各自会话、位置。各只设置存于各自的 `config-slot-N.json`。
- **进程级互通**（全窗共用一份，随主配置生效）：托盘与灵动岛、DeepSeek 余额查询、更新检查、待办提醒、共享解码链、Agent 联动与主动识屏（dsh 等 agent 只有一条连接，全部窗共享状态）、单进程多开开关本身。
- 注意：进程级开关以**主桌宠（第一只）**的配置为准——请在第一只的设置里修改「单进程多开」，在其它只的设置里改不会生效；首帧缓存预算同理（建议只改主配置 `config.json`）。
- 切换「单进程多开」后需重启生效。

### 内存调节（高级，改 config.json）

以下键暂无设置 UI，编辑数据目录下的 `config.json` 后重启生效：

| 键 | 默认 | 说明 |
|---|---|---|
| `first_frame_cache_max_mb` | 8 | 首帧缓存总预算（MB，4-64）。多开时按进程共享一份预算 |
| `predict_prewarm_lead_ms` | 350 | 预测式预热：动画播到结尾前提前多少毫秒预解码预测的下一段首帧；0=关 |
| `ffmpeg_recycle_minutes` | 10 | ffmpeg 解码子进程在圈边界的定期回收间隔（分钟，2-120）；0=不回收 |


</details>

<details>
<summary><b>使用教程</b></summary>

## 使用教程

### 基本操作

| 操作 | 效果 |
|---|---|
| 左键点击桌宠 | 触发点击互动动画 |
| 按住并拖动 | 移动桌宠；松开后根据拖动方向和速度处理转向、移动或惯性 |
| 按住 SHIFT + 左键拖动 | 开启了「SHIFT+左键拖动」时，这是唯一的拖动方式；未开启时 SHIFT 无特殊含义 |
| 右键桌宠 | 打开带图标的上下文菜单；可切换新旧菜单模板，或选择「生小肥鱼」启动独立的新桌宠 |
| 双击托盘图标 | 显示 / 隐藏桌宠 |
| 右键托盘图标 | 打开设置、AI 对话、开机自启、启动 DeepSeek Harness、退出等菜单 |
| 拖拽桌宠时 | 若开启了聊天窗跟随，聊天窗口会一起移动；默认不跟随 |

### 锁定位置 / SHIFT 拖动 / 不透明度（v4.0.0）

在「桌宠设置 → 桌宠行为 → 拖拽」与「外观 → 桌宠显示」中：

- **锁定位置**：开启后桌宠固定不动，怎么拖都拖不走（点击互动仍然有效）；想再调整位置时关闭即可。
- **SHIFT+左键拖动**：开启后普通拖动被禁用，必须**按住 SHIFT 再左键拖**才能移动桌宠——适合防止误拖，或桌面有别的操作需要普通左键时使用。
- **不透明度**：10%–100%，数值越小桌宠越透明（半透明效果），保存立即生效。

> 三者与「鼠标穿透」的区别：锁定/SHIFT 只是禁止拖动，点击互动（点头、音效、彩蛋）照常；鼠标穿透是桌宠完全不接收鼠标事件（点击会落到下层窗口），需要从托盘或右键菜单关闭。开启「鼠标穿透」时桌宠会气泡提示恢复位置。

### 开机自启

1. 右键系统托盘图标。
2. 勾选菜单中的「开机自启」。
3. 取消勾选即关闭自启；状态直接读写当前用户的注册表 Run 键，无需管理员权限。

### 调整播放速率

1. 右键桌宠（或托盘菜单）→「桌宠设置」。
2. 调整「播放速率」。
3. 点击保存或应用。
4. 播放当前动画或切换到下一段动画，观察节奏是否变化。

速率对当前片段和后续片段均生效；设置范围 `1.0x` 到 `2.0x`。

### 设置动作等待间隔

「动作等待间隔」用于降低连续动作过于密集时的节奏：

1. 在设置中找到「动作等待间隔」。
2. 输入间隔秒数，默认是 `0`。
3. 设为 `0`：保持当前连续播放行为。
4. 设为大于 `0`：相邻的非待机、非转向动画之间等待指定时间；等待期间仍允许待机和转向动画播放。

这个设置只影响动画调度，不会阻塞窗口拖动、点击、设置窗口或聊天窗口。

### 开启自言自语气泡

1. 在「桌宠设置」中勾选「开启自言自语气泡」。
2. 设置「随机间隔最短」和「随机间隔最长」。
3. 在「自言自语内容」中每行填写一条文本。
4. 留空会恢复内置内容，例如：

```text
好女孩……
好模型……
欧鲸鲸……
```

气泡默认显示在角色当前可见形象边界的正上方并水平居中；屏幕上方空间不足时，会自动选择不遮挡角色的候选位置。自言自语窗口不会改变桌宠的透明 mask，也不会阻止桌宠移动。

### 直播捕获兼容模式（Windows）

用哔哩哔哩直播姬 / OBS 做**窗口捕获**时，如果窗口列表里找不到桌宠，是因为桌宠默认是"工具窗口"形态（不占任务栏，捕获软件会过滤掉这类窗口）——这就是同类软件 Bongo Cat 能被捕获而桌宠不能的原因。

解决办法：在「桌宠设置」中勾选**「直播捕获兼容模式」**（Windows），保存后立即生效：

- 桌宠变为普通顶层窗口并显示标题「dsh-pet 桌宠」，直播姬/OBS 的窗口捕获列表即可看到并选中它
- 自言自语与快速对话气泡也会临时变成桌宠主窗的**子内容**：捕获「dsh-pet 桌宠」这一个源即可同时看到桌宠和气泡，不需要为气泡另加窗口源
- 气泡在捕获模式下会在桌宠窗口范围内自动选位（上方空间不足时放侧面/下方），避免被主窗边界裁掉；关闭捕获模式后恢复原本的独立浮出定位
- 代价：任务栏会出现桌宠图标（不开直播时取消勾选即可恢复原样）
- 开启后窗口置顶、鼠标穿透等其余行为不受影响

### 切换角色

1. 打开右键菜单中的角色选择入口。
2. 选择角色后，桌宠会加载对应角色目录中的动画（菜单每次打开都会重新扫描，无需重启）。
3. Chat 版会同步更新聊天窗口的角色名称、头像回退、主题色、有效 system prompt 和会话列表。
4. 角色之间的消息历史相互隔离。

#### 热加载新角色：文件怎么放

除内置角色外，桌宠会自动扫描**外部角色目录**发现新角色。目录名即角色 ID，按下面的树状结构放置即可（目录里含 webm 或 gif 即被识别）：

```text
characters/                          ← 外部角色根目录（见下方两个扫描位置）
└── <新角色ID>/                      ← 目录名 = 菜单里显示的角色 ID，如 mycat
    ├── manifest.json                ← 可选：角色名 / prompt / 主题色 / 动作映射
    └── videos/
        ├── idle/                    ← 待机（可多个）
        │   └── 待机呼吸.webm
        ├── turn/                    ← 转向
        │   └── 东张西望.webm
        ├── move/                    ← 移动
        │   └── 原地踏步.webm
        ├── click/                   ← 点击回应
        │   └── 点击开心.webm
        ├── drag/                    ← 拖拽（可选）
        │   └── 悬空反馈.webm
        └── random/                  ← 随机动作池
            └── 吃零食.webm
```

**两个扫描位置**（exe 同目录优先，其次用户数据目录；用户数据目录跨安装/升级保留）：

| 平台 | exe 同目录 | 用户数据目录 |
|---|---|---|
| Windows | `<安装目录>\characters\` | `%APPDATA%\dsh-pet-standalone\characters\` |
| macOS | `.app/Contents/MacOS/characters/` | `~/Library/Application Support/dsh-pet-standalone/characters/` |
| Linux | 源码运行目录 `characters/` | `~/.config/dsh-pet-standalone/characters/` |

把新角色的 `videos` 放进去后，**右键桌宠重新打开菜单即可看到新角色**，选中即热加载——不需要重新打包或重启程序。透明 WebM 素材的制作方法见[素材生成教学](#素材生成教学从零制作动画素材)。


</details>

<details>
<summary><b>AI 对话使用教程（Chat 版）</b></summary>

## AI 对话使用教程（Chat 版）

### 零基础快速配置（小白照抄版）

不想研究 API 的话，打开「AI 设置」后**只需要填一样东西：API Key**。其他按下面的值照抄即可：

| 设置项 | 填这个 |
|---|---|
| API 地址 | `https://api.deepseek.com` |
| 模型 | `deepseek-v4-flash` |
| API Key | 在 DeepSeek 开放平台创建（步骤见下） |

> 软件首次打开时，API 地址和模型**默认就已经是上面这两个值**（DeepSeek），不用改；只要把 API Key 粘进去就能用。

**如何创建 API Key（5 分钟搞定）：**

1. 打开 DeepSeek 开放平台：<https://platform.deepseek.com>
2. 用手机号注册 / 登录账号
3. 左侧菜单找到 **「API Keys」→「创建 API Key」**
4. 复制生成的 `sk-` 开头的密钥
5. 回到桌宠 → 右键 →「AI 设置」→ 粘贴到 **API Key** 一栏 → 点 **「保存」**
6. 点 **「测试连接」**，看到「连接成功」就完成了，去聊天吧！

**小提示：**

- API Key 只在创建时完整显示**一次**，创建完记得立刻复制保存（丢了就重新建一个，旧的作废）。
- 新注册账号一般会赠送一点测试额度；用完后到开放平台的「充值」页面充值，充多少用多少。
- 如果显示「认证失败（401/403）」，基本就是 Key 复制漏了字符或多了空格，重新粘贴一次。
- 显示「余额不足（402）」就是没额度了，去平台充值即可（网络和配置都是好的）。

### 第一步：配置 API

1. 右键桌宠（或托盘菜单）→「AI 设置」。
2. 新建或选择一个 Provider。
3. 填写兼容接口的 API 地址、模型、超时和生成参数。
4. 填写 API Key，并按提示选择钥匙串或配置文件回退。
5. 使用「连接测试」确认配置可用。

首期协议是 OpenAI Chat Completions 兼容协议。Gemini 等其他服务只有在提供兼容网关或兼容端点时才可使用。

### 常见错误码说明

AI 对话接口返回的常见 HTTP 状态码含义与处理方式（「测试连接」与聊天发送遇到 HTTP 错误时，软件内会显示状态码与原始错误信息）：

| 状态码 | 含义 | 处理方式 |
|---|---|---|
| 401 / 403 | API Key 无效或无权限 | 检查 AI 设置中的 API Key 是否正确、是否过期，或服务商账号权限是否足够 |
| 402 | 账户余额不足（Insufficient Balance） | 到服务商开放平台充值后重试；此错误说明网络与证书均正常，请求已到达服务器 |
| 429 | 请求过于频繁（限流） | 稍等片刻后重试；也可降低对话频率或减少会话历史长度 |
| 5xx | 服务端故障 | 服务商临时问题，稍后重试 |
| 网络连接失败 / 超时 | 无法连接 API 地址 | 检查网络与代理；确认地址可达、超时值足够 |
| SSL CERTIFICATE_VERIFY_FAILED | 证书校验失败 | 开着代理/梯子（证书被拦截）或本地网关 / 自签名证书时，可在 AI 设置中勾选「跳过 SSL 证书验证」 |

> 502/503/504 等 5xx 错误通常不是软件问题；若「测试连接」成功但发送失败，请把服务商返回的原始错误信息发到 Issue 便于排查。

### 第二步：开始对话

1. 右键桌宠（或托盘菜单）→「AI 对话」。
2. 聊天窗第一次打开时会定位在桌宠旁边，并根据桌宠当前可见形象边界和屏幕边界自动避让。
3. 输入区支持多行输入：`Enter` 发送，`Shift+Enter` 换行；生成中按钮变为「停止」。
4. 可在 AI 设置中开启或关闭「跟随桌宠移动」。

聊天窗默认为**新版现代双栏工作台**：左侧是会话导航（新建会话、会话列表、批量管理、跟随桌宠），右侧是消息时间线与输入区，包含：

- 无边框圆角窗口 + 自绘标题栏：角色头像、会话标题与状态（就绪/思考中/生成中）、模型名、收起侧栏、最小化、关闭。
- 会话侧栏：每行会话可切换，⋮ 菜单提供重命名 / 置顶 / 删除；底部有「跟随桌宠」「删除当前会话」「清空当前会话」。
- 消息时间线：用户和桌宠气泡、流式回复、错误与停止状态、复制/重试按钮。
- 输入区：附件（图片/文本拖拽或选择）、Enter 发送 / Shift+Enter 换行、生成中变为「停止」。
- 可在「桌宠设置 → AI 对话外观」中切换回**经典手机式窗口**。

### 配置 system prompt 和角色 prompt

system prompt 的优先级为：

```text
角色用户自定义 prompt > 角色 manifest 中的 prompt > 全局默认 prompt
```

角色可在以下文件中声明聊天配置：

```text
assets/characters/<character_id>/manifest.json
```

可选字段示例：

```json
{
  "chat": {
    "system_prompt": "你是一个温柔的桌面宠物……",
    "theme_color": "#79C7FF",
    "chat_actions": {
      "thinking": "thinking.webm",
      "success": "success.webm",
      "error": "error.webm"
    }
  }
}
```

非法或缺失的 `theme_color` 会回退为默认蓝色；缺少头像资源时，聊天窗使用角色 ID 首字母生成圆形头像。

### 会话管理

- 会话按角色目录保存。
- 会话标题优先取用户自定义标题（每行会话的 **⋮ 菜单 → 重命名**，可备注会话内容），未自定义时取第一条用户消息，无法生成时使用时间标题。
- 新版窗口的会话列表位于**左侧栏**：点击切换会话，每行 ⋮ 菜单提供重命名 / 置顶 / 删除；左侧栏底部有「跟随桌宠」「删除当前会话」「清空当前会话」；顶部的「开启新对话」新建会话，右侧栏头部按钮可收起侧栏。
- 支持会话搜索、批量管理（多选后置顶 / 删除）；删除带确认框（防误删）。
- 可新建、删除当前会话（带确认）或清空消息。
- 生成过程中会限制切换和删除，避免旧请求污染新会话。
- 停止生成时，未完成的半截 assistant 内容不会作为完整消息保存。
- 旧版手机式聊天窗同样支持重命名（铅笔按钮）与新建/删除/清空。

配置与会话目录（目录名按变体分：Chat 版 `dsh-pet-standalone-webm-chat`、无 Chat 版 `dsh-pet-standalone-webm`、源码运行为 `dsh-pet-standalone`）：

| 系统 | 数据目录 |
|---|---|
| Windows | `%APPDATA%/dsh-pet-standalone-<变体>/` |
| macOS | `~/Library/Application Support/dsh-pet-standalone-<变体>/` |
| Linux | `~/.config/dsh-pet-standalone-<变体>/` |

目录中主要包含：

```text
config.json
sessions/<character_id>/<session_id>.json
pet.log
```

配置格式当前为 v3，并兼容历史平铺字段，例如 `chat_api_url`、`chat_api_key`、`chat_model`、`chat_system_prompt` 和 `chat_enabled`。日志不会输出 API Key。


</details>

<details>
<summary><b>动画素材与自定义角色</b></summary>

## 动画素材与自定义角色

### 当前目录结构

```text
assets/
└── characters/
    └── shenshen/
        ├── manifest.json
        └── videos/
            ├── idle/
            ├── turn/
            ├── move/
            ├── click/
            ├── drag/
            └── random/
```

- `assets/characters` 是 WebM 动画源目录，包含 106 个 WebM 动画。
- GIF 目录（`assets/characters_gif`）仅在构建 GIF 变体时生成。
- 没有稳定静态头像时，不强制从 WebM/GIF 截取首帧，以避免启动变慢和打包兼容性问题。

### 素材生成教学（从零制作动画素材）

本项目的动画素材沿用参考项目 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的**三件套流程**：`① 提示词（配方）→ ② 素材生成链（引擎）→ ③ 插件（成品）`。任何人 clone 参考仓库都可以从零生成自己的桌宠素材，本教学按该流程说明。

#### ① 提示词 → 源视频（绿幕规范）

用 AI 视频生成工具（如**可灵、Runway、豆包**等；参考项目素材即由豆包生成），按提示词配方为每个动作生成一段 **10 秒绿幕视频**。配方硬性规范（参考项目的 `prompts/桌面宠物 10 秒动作提示词.md`，本项目自定义角色配方在本地 `assets/prompt/<角色ID>/图像生成提示词.md`）：

- **画面**：16:9；背景纯绿幕色 `#00FF00`，无阴影、杂物、渐变或边框。
- **人物定位固定**：头顶约画幅垂直 20%（按角色头饰可调整为 15%），脚底约 85%；左右边缘约 25% / 75%；不同视频间人物大小、位置、比例完全一致。
- **安全缓冲**：头顶距顶边 ≥15%、脚底距底边 ≥10%、两侧距边缘 ≥10%；任何身体部位/道具/特效不得出画或贴边。
- **禁止平移**：双脚落点恒为画面正中，仅允许原地轴心旋转、原地跳跃；道具与角色组合视觉重心始终居中。
- **首尾帧闭环**：第一帧 = 干净的标准正面站立；第 10 秒结束必须恢复到与第一帧完全一致。
- **道具"无→有→无"**：道具/特效由角色从虚到实渐进生成、结束前由实到虚消散，不得凭空出现或残留。
- **按秒分解**：每个动作的配方按 0–3 / 3–7 / 7–10 秒（或 0–2 / 2–5 / 5–7 / 7–10）分段描述动作节奏，确保生成结果可复现。

一个动作一段视频，按动作名各存一个 mp4（如 `video/吃白饭.mp4`）。

#### ② 源视频 → 透明动画（素材生成链）

参考项目 `scripts/` 提供完整 Python 素材链（依赖：Python 3 + ffmpeg + numpy + scipy），四步：

```sh
cd scripts
# step01：水印遮罩填充（源视频若带水印/角标则先填补为纯绿幕）
python watermark_step01.py
# step02：绿幕抠像 → 透明视频（两条路线二选一）
#   路线 A（默认自动化）：HSV 色相抠像，人人可复现
python chroma_step02.py
#   路线 B（精细手工，推荐）：PR 手工抠像导出带 alpha 的透明 .mov，
#   放入 pr/（文件名与动作一致，如 吃白饭.mov）后导入
python pr_import_step02.py
# step03：归一化 2160×1215 统一站立居中（对齐脚底锚点）
python normalize_step03.py
# step04：转码 640×360 透明播放变体（VP9 alpha 透明 WebM）
python encode_thumbs.py
```

> 参考项目全部 106 个动作均采用**路线 B（PR 手工抠像）**：对含第三方物品/透明边缘复杂的动作，自动 HSV 抠像易残边或误抠；`chroma_step02.py` 保留为自动化兜底。中间产物 step01~04 由脚本生成、不入仓库；`video/` 源视频与 `scripts/` 是成果、入库维护。

#### ③ 透明动画 → 接入本项目

1. 把 step04 产出的 **640×360 透明 WebM** 按分类放入角色目录：

   ```text
   assets/characters/<角色ID>/videos/
   ├── idle/     待机（可多个）
   ├── turn/     转向
   ├── move/     移动
   ├── click/    点击回应
   ├── drag/     拖拽（可选）
   └── random/   随机动作池
   ```

2. 保持几何约定与播放器一致：画布 **640×360**、24fps、**VP9 alpha 透明**；角色脚底对齐画布 y=330（`catalog.py` 中 `FEET_Y=330`、落地偏移 `PAD=30`），这样桌宠窗口的脚底落地对齐才准确。
3. 命名保持稳定、避免重复；可参考 `assets/characters/shenshen/videos/` 现有 106 段动画的组织方式。
4. 如需 GIF 变体，运行 `python scripts/convert_to_gif.py --force --clean` 同步生成。

> 不想重新打包？把做好的透明 WebM 按「切换角色」的外部角色目录结构直接放入 `characters/<角色ID>/videos/`，右键菜单即可热加载新角色。
>
> 快速验证：`python -m pytest -q` 会检查 WebM/GIF 相对路径一一对应；源码运行 `python -m pet` 或重新打包后检查对应分类是否正常播放。

### 重新生成 GIF（仅构建 GIF 变体时需要）

更新 WebM 素材后，在项目根目录执行：

```powershell
python scripts/convert_to_gif.py --force --clean
```

其中：

- `--force`：覆盖已有 GIF。
- `--clean`：删除目标目录中已经不存在对应 WebM 的旧 GIF，防止两套素材残留不一致。

转换前请确认 `imageio-ffmpeg` 已安装。生成后可以用下面的命令检查数量：

```powershell
(Get-ChildItem assets/characters -Recurse -Filter *.webm).Count
(Get-ChildItem assets/characters_gif -Recurse -Filter *.gif).Count
```

两者应当相同；还应检查相对路径是否一一对应。

### 新增角色

1. 在 `assets/characters/<character_id>/videos/` 下按动画类别建立目录。
2. 放入透明 WebM 文件（制作方法见「素材生成教学」），命名保持稳定、避免重复。
3. 如有角色身份信息，在 `<character_id>/manifest.json` 中填写名称、prompt、主题色和动作映射。
4. 如需 GIF 变体，运行 GIF 转换脚本同步生成 GIF。
5. 使用源码运行或重新打包验证角色切换、播放、气泡定位和 Chat 身份区。


</details>

<details>
<summary><b>开发结构</b></summary>

## 开发结构

```text
pet/
├── app.py                    # AppShell + PetInstance：进程级/每窗容器与装配（托盘、多窗共享）
├── config.py                 # 配置读取、迁移和持久化（reload 白名单 + schema 测试）
├── config_domains.py         # 配置域 facade（chat/agent_link/proactive/collision/menu）
├── window.py                 # 桌宠主窗口（组合根；碰撞/平台层/动画链已拆出，受行数预算红线）
├── window_optional_services.py # 窗口可选服务懒装配 mixin（todo/file_eater 等）
├── collision.py              # 碰撞物理核心（纯 Python，无 Qt）
├── collision_client.py       # 窗口侧碰撞客户端（预测/对账/上报节流/squash 冷却）
├── collision_codec.py        # 碰撞 IPC 帧编解码 + 水位去重 + 协议 TypedDict（纯 Python）
├── collision_ipc.py          # 碰撞协调者选举与成员协议（QLocalServer 控制面）
├── collision_debug.py        # 碰撞调试日志
├── decode_fanout.py          # 同角色共享解码链（进程内帧扇出 DecodeFanoutHub，单向依赖约束）
├── frame_cache.py            # 通用字节预算 LRU（webm 元数据缓存等小缓存用）
├── perfstats.py              # 性能打点（PET_PERF_STATS=1 启用，atexit 落盘）
├── predictive_prewarm.py     # 预测式预解码预热（切动画前预拉下一段，默认提前 350ms）
├── platform_win.py           # Windows 平台层（鼠标穿透/全屏判定/PerPixel 输入/WS_EX_TRANSPARENT）
├── platform_mac.py           # macOS 平台层（NSWindow level/激活策略）
├── catalog.py                # 角色和动画素材发现
├── library.py                # 动画库访问（懒加载 + 优先级预热）
├── webm_clip.py              # WebM 播放（reader 线程/软停 re-arm/定期回收/fan-out 钩子）
├── speech_bubble.py          # 气泡绘制与交互
├── speech_bubble_text.py     # 气泡分页/定位纯函数
├── click_sound.py            # 点击音效（ClickSoundPool 单例封装）
├── desktop_notify.py         # 自绘右下角系统通知
├── slot_manager.py           # 多开 slot 文件锁
├── child_pet_cleanup.py      # 子肥鱼清理（关闭非当前 runtime 标记 + 删除 slot 数据）
├── file_eater.py             # 拖拽文件“吃”动画与统计（不真实删除/移动文件）
├── proactive.py              # 主动识屏陪伴（Watcher 编排）
├── proactive_limiter.py      # 主动识屏频控
├── proactive_memory.py       # 主动识屏记忆
├── agent_link.py             # Agent 联动监视器（多 Agent 事件源：CLI/IDE/SQLite 轮询）
├── multi_window_shared.py    # 进程级多窗共享子系统（agent_link/proactive/全屏 watcher）
├── vision.py                 # 视觉模型调用（看看屏幕/主动识屏；PIL 懒加载）
├── harness_launcher.py       # DeepSeek Harness 一键启动
├── instance_launcher.py      # 「生小肥鱼」多开孵化
├── modern_settings_dialog.py # 新版设置主对话框（已拆分瘦身，保留 re-export；受行数预算红线）
├── settings_widgets.py       # 设置控件库（自绘开关/SettingRow/ModernSelect 等）
├── settings_menu_layout_editor.py # 右键菜单布局编辑器
├── settings_theme_qss.py     # 设置主题 QSS（明暗）
├── todo_reminder.py          # 待办提醒调度（气泡/桌面通知，PR72 合入；进程级单例）
├── todo_panel.py             # 待办管理面板（右键菜单「待办提醒」打开）
├── context_menus/            # 新旧菜单模板、图标、彩蛋入口
├── chat/                     # 独立 AI 对话子系统（现代双栏 + 经典手机式）
│   ├── models.py             # 数据模型（ProviderConfig/ChatSession/...）
│   ├── providers.py          # Provider 请求与连接测试
│   ├── service.py            # 对话服务
│   ├── session_store.py      # 会话持久化（异步 writer + 注册表）
│   ├── geometry.py           # 聊天窗跟随定位（双 UI 共享纯函数）
│   ├── utils.py              # 会话标题/时间格式化（双 UI 共享）
│   ├── themes.py             # 聊天窗背景主题
│   ├── widgets.py            # 新版聊天窗
│   ├── legacy_widgets.py     # 经典手机式聊天窗
│   ├── ai_settings_page.py   # AI 设置页（PR #76 从 modern_settings_dialog 拆出）
│   ├── modern_styles.qss / legacy_styles.qss
│   └── ...
└── updater.py                # 检查更新与发布资产解析

integrations/dsh-pet-bridge/  # DSH 桥接插件（Agent 联动）
packaging/
├── pet_entry.py              # Chat 构建入口
├── pet_entry_no_chat.py      # 无 Chat 构建入口
└── dsh-pet.iss               # Inno Setup 通用安装包脚本（/D 参数编译各变体）

scripts/
├── build_onedir.ps1          # Windows onedir 构建 + zip 绿色版打包（本地与 CI 共用入口）
├── build_macos.sh            # macOS .app 构建（本地与 CI 共用入口）
├── build_linux.sh            # Linux onedir 构建（本地与 CI 共用入口）
├── check_bundle_encoding.py  # 产物中文编码自检（issue #26，构建脚本内自动调用）
├── make_icon.py              # 从待机动画提取封面帧生成应用图标（assets/icon.ico）
├── convert_to_gif.py         # WebM → GIF 全量同步脚本
└── cleanup_mei_cache.py      # 检查/清理旧 onefile 版本遗留的 _MEI 缓存（默认预览）

tests/                        # 单元测试、Qt offscreen 测试和构建相关验证
                              # （含 test_architecture.py 架构红线：依赖方向 /
                              #  窗口私有面冻结 / window.py 行数预算 /
                              #  modern_settings_dialog.py 行数预算 / 孤儿簇守卫）
```

**给 window.py 加功能前必读**：[docs/WINDOW_PY_SPLIT_GUIDE.md](docs/WINDOW_PY_SPLIT_GUIDE.md)
——window.py 处于「只许瘦不许胖」的增量拆分公约下（CI 有行数预算红线），
新功能先按公约拆对应控制器再动手。`modern_settings_dialog.py` 同理：
控件库/菜单编辑器/AI 设置页/主题 QSS 已拆出，再往主对话框塞新页面会被行数预算红线拦下。
</details>

<details>
<summary><b>测试与验证</b></summary>

## 测试与验证

在项目根目录执行：

```powershell
pip install -r requirements.txt   # 运行时 + 开发依赖（含 pytest/ruff）
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
python -m compileall pet packaging scripts
```

最近一轮记录（PR #76 合并后的 main，2026-09-06）：

- `pytest`：**1322 passed / 7 skipped**（CI 三平台 windows/ubuntu/macos 全绿；本机如遇 Windows symlink 权限等环境性失败，与改动无关）。
- `ruff`：干净。
- `compileall`：通过。
- 架构红线测试通过：依赖方向 / 窗口私有面冻结 / `window.py` 与 `modern_settings_dialog.py` 行数预算 / 孤儿簇守卫。
- WebM Chat、WebM 无 Chat 两个 onedir 构建均完成启动冒烟验证：进程存活超过 8 秒，系统临时目录与程序目录**均无新增 `_MEI` 缓存**。

如果要验证真实窗口，不要设置 `QT_QPA_PLATFORM=offscreen`，直接运行 `python -m pet` 或打包后的程序，重点检查：

1. 桌宠透明背景、鼠标穿透、拖动和动画播放没有回归。
2. 自言自语气泡位于角色形象正上方，靠近屏幕边缘时不会遮住角色。
3. 动作等待间隔只限制相邻非待机动画，不阻塞待机、转向和窗口操作。
4. WebM 播放速率切换后，当前片段和下一片段节奏都发生变化。
5. 聊天窗为无边框圆角窗口（窗外无方形背景）、位于桌宠可见形象旁边，跟随开关符合设置。
6. 切换会话和角色时，旧消息、旧流式气泡不会串入当前会话。


</details>

<details>
<summary><b>打包发布</b></summary>

## 打包发布

发布流水线：**onedir 构建 → zip 绿色版 → Inno Setup 安装包**。onedir 运行期零解压，不产生 `_MEI` 缓存；安装包免管理员、可选安装目录。

### 1) onedir 构建 + 绿色版 zip

需要 PyInstaller：

```powershell
python -m pip install pyinstaller
```

```powershell
# WebM Chat 版
powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat
# WebM 无 Chat 版
powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm
```

产物位于 `dist-onedir\<name>\`（绿色版目录）与 `<name>-portable.zip`。

> GIF 变体（`gif-chat` / `gif`）需要先运行 `scripts/convert_to_gif.py --force --clean` 生成 GIF 素材，构建时加 `-Gif` 参数；默认发布不含 GIF 版。

macOS / Linux 本地构建与 CI 共用同一份脚本（PyInstaller 打包、中文编码自检都在脚本内完成）：

```bash
# macOS（.app，输出 build/macos/）
bash scripts/build_macos.sh --variants webm-chat,webm
# Linux（onedir，输出 dist/）
bash scripts/build_linux.sh --variants webm-chat,webm
```

### 2) Inno Setup 安装包

本机已安装便携版 ISCC：`E:\tools\InnoSetup6\ISCC.exe`（免管理员）。通用脚本 `packaging\dsh-pet.iss` 用 `/D` 定义编译不同变体：

```powershell
# WebM Chat 版（脚本默认值）
E:\tools\InnoSetup6\ISCC.exe packaging\dsh-pet.iss

# WebM 无 Chat 版
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-webm /DMyAppExeName=dsh-pet-standalone-webm.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-webm "/DMyAppId={{3424d6cc-af3c-4383-8797-ab520b923aa6}}" "/DMyAppDisplay=dsh-pet-standalone (WebM)" packaging\dsh-pet.iss
```

完整命令（含 GIF 变体）与安装包特性见 [`docs/ONEDIR_PACKAGING.md`](docs/ONEDIR_PACKAGING.md)。

打包注意事项：

- 构建前关闭正在运行的同类程序，避免文件被占用。
- Chat 版使用 `packaging/pet_entry.py`，无 Chat 版使用 `packaging/pet_entry_no_chat.py`；无 Chat 入口会排除 `pet.chat` 和 `keyring`，不携带 AI 对话依赖。
- 安装包为按用户安装（`PrivilegesRequired=lowest`），默认目录 `%LOCALAPPDATA%\Programs\...`，向导中可自行选择任意盘符。
- 打包完成后，至少安装/运行一次，检查托盘、角色切换、设置、自言自语和聊天入口。

构建记录和 SHA256 位于：

```text
docs/BUILD_ARTIFACTS-2026-08-22.md
```

### 3) Linux 构建（GitHub Actions）

PyInstaller **不支持交叉编译**，Linux 包必须在 Linux 上构建。推荐直接使用仓库内的工作流 [`.github/workflows/build-linux.yml`](.github/workflows/build-linux.yml)：

1. Actions 页面手动运行 **Build Linux App**（`workflow_dispatch`），或打 `v*` tag 自动触发并发布到 Release。
2. 产物：`dsh-pet-standalone-<变体>-linux-x86_64.zip`（onedir 目录，保留可执行权限）。Linux 发布两个 WebM 变体（`webm-chat` / `webm`）；GIF 变体包体约 800 MB，不发布。

本地构建（在 Linux 机器上）——**以正式脚本为准**（手工 PyInstaller 命令
长期与脚本漂移、会漏菜单模板/QSS 等资源，审查 P2-07）：

```bash
bash scripts/build_linux.sh webm-chat   # 变体：webm-chat / webm / gif-chat / gif
```

> GIF 变体需先运行 `python scripts/convert_to_gif.py --force --clean`。


</details>

<details>
<summary><b>旧版 onefile 缓存清理（仅旧版本需要）</b></summary>

## 旧版 onefile 缓存清理（仅旧版本需要）

旧版单文件 EXE（onefile）运行时会在系统临时目录创建 `_MEI数字` 目录，崩溃或强制结束时可能残留；**当前 onedir 发布版不会再产生该缓存**。程序启动时仍会自动尝试清理超过 24 小时的遗留目录，并跳过当前进程正在使用的运行目录；权限不足或目录被占用时只记录日志，不强制修改 ACL。

也可以使用项目提供的专用脚本检查：脚本默认只预览，不会删除任何目录。确认所有桌宠进程都已退出后，才使用 `--delete`：

```powershell
python scripts/cleanup_mei_cache.py
python scripts/cleanup_mei_cache.py --min-age-hours 0
python scripts/cleanup_mei_cache.py --delete
```

如果某些目录因权限异常仍无法删除，请先退出所有桌宠，再用管理员 PowerShell 运行脚本；脚本不会自动接管目录所有权，避免误操作其他临时文件。


</details>

<details>
<summary><b>配置与安全说明</b></summary>

## 配置与安全说明

- API Key 不会写入日志，也不应放入截图、Issue 或公开配置。
- 默认优先使用系统钥匙串；钥匙串不可用时，设置界面会提示配置文件回退风险。
- 会话文件保存在本地，不实现云端同步。
- OpenAI 兼容接口的错误响应、网络异常和空响应会转换为界面错误状态，并保留用户消息供重试。
- 当前消息按纯文本显示；不要把不可信的模型输出当作 HTML 或脚本执行。


</details>

<details>
<summary><b>最近修复与变更记录（2026-08 起）</b></summary>

## 最近修复与变更记录

> 按时间倒序记录。v4.2.0 及更早版本的完整清单见 [`docs/RELEASE-v4.2.0.md`](docs/RELEASE-v4.2.0.md) 与 [GitHub Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)。

### PR #112（2026-09-12 合并，MerZlin）——Windows 关机/注销不再弹 `0xc0000142`（issue #111）

- **问题**：每次关机/注销必弹「`ffmpeg-win-x86_64-v7.1.exe` - 应用程序无法正常启动 (0xc0000142)」并**阻塞关机流程**；桌宠未运行时不弹。
- **根因**：`0xc0000142` = `STATUS_DLL_INIT_FAILED`。系统先发 `WM_QUERYENDSESSION`，随后拆除本次登录会话（窗口站 / 桌面堆 / CSRSS）；本进程此前对该消息**零感知**，动画链照常运转并随时 `CreateProcess` 新的 ffmpeg 取帧进程（冷首帧预热 / reader 换代 / 元数据探测 / 圈末回收后 fresh spawn —— 日志里 reader 启停每几秒一次），新进程在已拆除的会话里 DLL 初始化失败，系统于是弹窗阻塞关机。
- **修复**：`pet/webm_clip.py` 新增进程级「会话结束」闸门，四条 ffmpeg spawn 路径全部拒绝（`start()` 返回 `False`，**复用既有「启动被拒」降级契约，调用方零改动**；reader 线程 / 首帧解码 / 元数据探测 / exe 探测各自短路，并在 `Popen` 前二次复查收紧并发窗口）；新增 `pet/session_watcher.py` 用应用级原生事件过滤器捕获 `WM_QUERYENDSESSION` / `WM_ENDSESSION`（恒返回 `(False, 0)`，只观测、**不 veto 关机**），Qt 的 `commitDataRequest` / `aboutToQuit` 作次生兜底；会话结束时逐窗冻结（`match_shutdown`）、停素材库全部已建 clip（含圈末软停驻留的）、日志留痕（`收到会话结束通知` / `会话结束：已停止全部 ffmpeg reader`）。
- **验证**：新增 `tests/test_session_end_ffmpeg_guard.py`（25 例，含「未置位对照组照常 spawn」证明门是唯一差异、真实 ctypes `MSG` 投递、tokenize 级 spawn 清单静态护栏）；窗口层 `match_shutdown` / `_resume_activity` 用例；**真机实测**向运行中的窗口投递 `WM_QUERYENDSESSION` 后闸门即时置位，报告者本机复测关机无弹窗；打包产物（onedir + setup.exe）内实跑同一路径复验。
- 设计与残余风险（在飞 `Popen` 无法撤回、`node`/`pnpm` 等非 ffmpeg 派生未纳入）见 [`docs/ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md`](docs/ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md)。

### PR #109（2026-09-12 合并，klxxya）——探头/头槌体验三连修 + 气泡分页避头尾

- **头槌飞行中被碰撞闪一瞬间回正**：碰撞 squash Q 弹的 `paintEvent` 分支没走特效旋转管线（`_sync_mask` 一直在转，画面与轮廓还错位）。squash 分支补上与 `_sync_mask` 一致的旋转路径，仅特效角非零时启用。
- **探头状态被撞不飞、卡出错误身位**：PEEKING 稳态没有 timer 归位，软撞的分离位移把窗口顶偏后停住。探头会话激活且非真实撞击时丢弃分离位移（位置归探头控制器管）；真实撞击仍进入 throw。
- **头槌减速后鱼头固定成探头角度**：防抖阈值 `780` 本为贴地弹跳设计，却把空中低速段一起冻结。跟随阈值分档——触界维持 780，空中降为 150 继续跟随。
- **气泡分页**：长消息末页只剩一个标点、每页固定时长、翻页硬切。改为换行避头尾（闭标点不站行首，`elide`/`paginate` 合并为同一换行助手）、末页孤行重平衡（3+1 → 2+2）、逐页字数自适应停留（`page_dwell_ms`）、翻页淡入淡出 + 圆点页码。
- 新增 4 组共 12 条用例（含「无修复时必红」的 squash 旋转 spy 断言）。

### PR #108（2026-09-12 合并，klxxya）——弹射全程动画切换封死冷首帧解码

- **取证**：`PET_PERF_STATS` + >100ms 帧空窗看门狗抓到 26 次卡顿，其中 **19 次是动画切换命中冷首帧**——GUI 线程同步拉 ffmpeg 解码（冷解码均值 100.7ms / 最差 1.4s，而 60Hz 单帧预算只有 16.7ms）。
- **根因**：预测式预热只覆盖正常播放链；弹射的每次切换都是事件驱动并绕开预测（拖拽交互让路挡住预热、松手现场掷骰、飞行途中照常推进动画链）。
- **修复**：分级策略——松手时拖拽动画继续播完；高速飞行（≥400px/s）固定循环悬空动画（pinned 首帧必热）；低速滚动（<400px/s）**降速同一 tick 即时过渡**并只掷「首帧已热」的目标（冷目标退回必热 idle/turn 池）；起飞时后台预热 idle 首帧（绕过交互让路闸门）；落地停稳回待机。掷骰概率与正常链共用同一 `_roll_next`（30/10/40/20 不变形）。
- 已知边界：飞行途中点击触发的黄金回旋被飞行循环接管，落地后恢复（空中回旋与悬空动画本就互抢旋转管线）。

### PR #107（2026-09-12 合并，klxxya）——余额多币种响应不再盲取首条（issue #106）

- **问题**：`GET /user/balance` 可能返回多条 `balance_infos`（如 CNY + USD）且顺序不保证，旧实现直接取 `infos[0]`；USD 排前面时读到 `0.00`，右键余额在 **¥0.00 与真实值之间来回跳**，灵动岛余额档位动画跟着错。
- **修复**：新增 `_pick_balance_info()`——优先取**有余额**的一条，同分优先 CNY，全为 0 退回首条（保持原行为），非 dict / 非数值条目防御跳过；改动只有 `pet/balance.py` 一个函数 + 调用点一行，新增 5 条用例。
- 未采纳项：issue 里提到的「`¥` 符号写死」未动（不影响 CNY 账户）。

### PR #104 / #105（2026-09-12 合并，klxxya）——桥接插件归零外部依赖 + DSH 事件层系统性收尾

**#104：桥接插件归零外部依赖（事故级）**
- **问题**：桥接插件经 pnpm `link:` 安装，而 `link:` **不安装被链接包自身的依赖**，链接目标又是打包版 `_internal` 副本（不带 node_modules）。#57 给桥接引入的 `@deepseek-ai/dsh-llm` 在打包副本上解析失败 → Cordis 插件树初始化整体抛错，**dsh web / headless / desktop 全 profile 无法启动**（多位用户实机复现）。
- **修复**：恢复「零外部依赖」设计——`index.js` 手写 user-message envelope（与 dsh `createUserMessage` 键序/覆盖语义/深冻结逐项对拍），清空 `dependencies`、删除 `pnpm-lock.yaml`。
- **防线（构建期红线反转）**：`build_onedir.ps1` 从「必须声明依赖」改为「**禁止声明任何依赖**」（含 peer/optional）；`fix_bridge_bundle.py` 新增不依赖 node 的 dist 清单零依赖校验；PR 门禁新增 7 个 bridge 契约测试 + `verify_import.mjs` hermetic 冒烟（无 node_modules 隔离目录 import、动态 import/require 一律禁止、临时目录祖先链污染负向对照）。

**#105：PR57 系统性收尾（先红后绿）**
- **真功能断裂修复**：cordis 审批交互链**从未工作**（bridge 把 `requiresApproval` 嵌在 `payload` 内写盘、pet 门禁查顶层字段，判定恒 False，而测试用手写顶层形状掩盖了断裂）；问题气泡在 mux 断线时**关不掉**（pet 侧升级重建把旧 `call_id` 覆盖成 None、bridge 侧帧不带 callId）；5 个正常事件漏登记导致每次「终止/自动优化」都伴随「请更新/重装 bridge」假提醒；无 pnpm 时卸载**假成功**并留下 `link:` 残留。
- **错误分支与不可达映射**：审批 resolved 的 callId 分支关错 kind（误关问题气泡）；`dsh_state` 认桥接真实写的 `llm_error`（旧表键无生产者，ERROR 态永不可达），删死键。
- **两处产品语义统一**：看门狗计时改**任务级**（边界重建沿用旧锚点，原实现下「启动宽限」实际每轮续期、「连续运行降阈值」常态不可达）；桌宠**隐藏时暂停看门狗**并在恢复时把计时锚点后移隐藏时长（原实现隐藏期提醒被丢弃且「已上报」标志已置位 → 永久丢失）。控制成功后记宽限，不再「刚点完又弹」。
- **死机制清理与门禁**：移除引入起零消费的 `AgentEventRuntime` 分发层与 Judge 机制等 20 项死信号/死键/孤儿方法（逐项 grep 零消费证据）；设置页新增「行为重复检测」组（开关 + 9 阈值）；检测器节流「记账后置」（被概率门抽稀的提醒不再占用 30s 节流槽）。
- 验证：全量 pytest 1895 passed / 8 skipped，`node --test` 7 文件全过 + hermetic 冒烟通过；新增/更新约 30 条用例全部先红后绿。

### PR #97（2026-09-11 合并，Daliuq）——事件汇报概率门 + 事件链路语义对齐 + issue #95

**新功能：事件汇报概率门（`pet/report_gates.py`）**
- 联动气泡从布尔开关统一为**概率门**模型（0.00–1.00），8 个事件聚合类别各一个滑块，默认 `activity=0.6`、其余 `1.0`；旧开关/百分比自动迁移；右键菜单保留 0/1 两端快捷入口，细粒度概率走设置页滑块。
- 概率门只管**气泡这一步**：检测器与原始记录链不采样，也不会把整个功能静音。

**事件链路语义对齐（Bridge → 传输 → 气泡渲染）**
- `rate_limit` 事件 → `model_access`（语义从「限流」扩展为「模型访问失败」：限流/过载/AI 服务错误）；`RateLimitTracker` → `ModelAccessTracker`。
- `errorText` → `errorMessage`（tool/result 与 `llm/retry` 等统一）；`source` → `failureType`（`model_retry_exhausted` / `tool_failed`）。
- 归一化层补 `event` 字段（`agent/status` 别名 → 规范名），修掉模型访问失败**连续计数丢失**；占位符与文案键同步迁移（`{source}`→`{failureType}`、`{errorText}`→`{errorMessage}`、`rate_limit.*`→`model_access.*`）。

**Persona 模板升级**
- 记住上次编辑层 + Agent 层隐藏公共事件；导出模板支持全部 Agent 单独配置脚手架与 `entries` 语义描述；预设键序排齐、占位符迁移；修掉自定义模式 `{text}` 字面量泄漏与双层预设取 global 层的问题。

**issue #95（D​SH 联动装不上插件）**
- Windows node/pnpm 解析不再写死：增强 PATH 补注册表最新 PATH 与各版本管理器真实目录（nvm-windows `%NVM_HOME%\v*`、`%NVM_SYMLINK%`、`%PNPM_HOME%`、`%APPDATA%\npm`、Volta/fnm/scoop/choco/bun/yarn、`%ProgramFiles%\nodejs`），POSIX 补 `PNPM_HOME`/`$NVM_DIR`/fnm/volta/asdf/linuxbrew；nvm 自定义根 `~/nvm`（无点号）与 `%APPDATA%\nvm` 兜底。
- bridge 安装的 pnpm 入口改为多布局发现（`.cmd/.ps1` 包装脚本里的真实 JS 入口、独立 `pnpm.exe`、`DSH_PNPM_BIN`/`pnpm_bin` 配置键）；一键启动 DSH 的全局包根目录在 Windows 上补 nvm-windows 等目录。
- 依赖规格体检（指名报错 + 候选路径）、启动 link 自检、坏依赖路径实修（改写 + 备份 `package.json.bak-*` + pnpm 重试）、未知桥接事件不再误报「更新/重装 bridge」。

**其它修复**
- 菜单布局编辑器 QTimer 崩溃（C++ 析构后回调 → 成员 timer + `shiboken6.isValid()`）；全量测试 access violation（全局 processEvents 冲刷 → 定向 DeferredDelete）；桥接硬失败误报（恢复后未清零）；连续点击动画不从头播放（`_soft_parked` 未清零）；AppShell/共享子系统退出生命周期收口（解释器退出 access violation）。

### PR #100 / #102（2026-09-11 合并，Daliuq / klxxya）——启动即按配置装配可选服务（issue #99）

- **问题**：`AgentLinkManager` 懒创建、监视器真正启动靠 `apply_config()`，而工程里只有「展开 Agent 联动菜单」与「关闭设置对话框」两条路径会触发——重启后已开启的 Agent 联动（DSH/Claude/Cursor/OpenCode 与 `custom_agents`）与主动识屏**开机不生效**，必须手动点一次菜单或设置。
- **修复**：`PetWindow.__init__` 收尾改为 `sync_optional_services()`（其内部仍以 `_install_effect_services()` 收尾，净增 0 行），开机即按配置装配主动识屏 / Agent 联动 / 效果控制器；隐私红线不变（仍受 `proactive_screen.enabled` 与白名单/上限/冷却约束）。
- 回归：`tests/test_feature_gating.py` 两条用例（`dsh` 与 `opencode` 通道各一），断言管理器已创建**且** `monitor._running`，并以 `mgr.shutdown()` 收尾。

### PR #101（2026-09-11 合并，klxxya）——边缘探头会话期间禁止位移

- **问题**：探头会话期间移动动画会被效果闸门降级为待机/转向，但 `_try_move` 仍会建立位移计划——桌宠**挂着探头姿态被平移出屏幕边缘**（自动掷骰与右键「移动」两条路径都会）。
- **修复**：`PetWindow._try_move` 入口加 `_effects_probe_active()` 闸门，探头会话激活时直接返回 False（不建立位移计划）；回归用例同时验证「退出探头后移动恢复可用」的正向对照。
- 说明：该修复使 `window.py` 涨到 4385 行，行数预算按仓库惯例随实测校准（带日期注释）。

### v4.2.0（2026-09-10 发布）

完整清单（含全部小项与修复）见 [`docs/RELEASE-v4.2.0.md`](docs/RELEASE-v4.2.0.md)，GitHub Release 同步。

**新功能**
- **桌宠本体玩法**：右键菜单「黄金回旋」入口（连点逐圈加速）；拖文件模拟吃掉（不真实删除）；动画素材 97 → 106；「回到右下角」先取消探头姿态。
- **边缘交互与省电**：边缘探头（±45° 贴边、露出 0.55、点击拉直、探头时静音）；撞飞彩蛋（被撞翻鱼头、落地 5s 重新吸附）；点击触发黄金回旋 + 直连跳过动画；省电模式（闲置半帧率 + 停预热）；音乐自动唱歌。
- **台词、气泡与提醒**：气泡配图大小可调（50–300%）；图片目录预览抽屉（3 列瀑布流）；台词模板 JSON 导入/导出/复制 + v2 占位符渲染；表达风格统一入口；**待办提醒**（右键「待办」面板 + 提前量/宽限/归档）；会话保存原子化 + 异步写盘。
- **设置与菜单重构（PR #64）**：七能力域侧栏；**右键菜单可编排**（显隐/子菜单/分割线/别名/图标覆盖/实时预览/损坏回退安全菜单）；快捷启动编辑与禁用占位；主题即时生效 + 从属项开显关隐；三态响应式；macOS 原生 Dock 菜单。
- **识屏与灵动岛**：识屏自我识别（模型以第一人称看待画面里的自己）；可选服务懒加载；灵动岛峰谷档位显示「当前档位→下一档切换时间」。
- **Agent 联动与 DSH**：富事件状态（thinking/working/attention/error）；统一事件层与事件队列（三层事件契约）；审批/提问气泡（多问题项、interaction_id 多交互并存）；**探索 Watchdog 控制链**（常驻气泡 + 自动优化/终止/忽略，GUI 不阻塞）；Harness 复用本机实例、`harness_autostart`、启动链路静默化；macOS Node 解析。
- **多开与子肥鱼**：单进程多开（3 窗 1 进程 181–197MB）；同角色共享解码链（1 个 ffmpeg）；托盘聚合与灵动岛聚合；slot 落种；**「退出子肥鱼」只退出不删数据**；多窗提醒只在首个可见窗展示（行为变更）。
- **安全与跨平台（PR #68/#71/#94）**：明文 API Key 自动迁移 keyring；Linux Fcitx 中文输入随包插件；macOS Dock 隐藏彻底生效；**DLC/换角色加固**（缺失动画守卫、启动回退默认角色、点击台词支持外部素材目录）。
- **性能**：高刷屏节拍跟随刷新率（PR #70）；非显示 clip 清空显示槽（修内存慢涨主因）；ffmpeg 常驻循环解码 + `-threads 1` + 圈边界回收；预测式首帧预热；碰撞预测限频（CPU 61%→43%）。

**关键修复**
- Windows 打字时桌宠频闪根治（排除工具窗口/截图覆盖层 + 原生 `WS_EX_TRANSPARENT`）。
- 气泡行尾字被裁切（PR #96）：折行与 label 宽度改用同一套“真正绘制的行”度量 + `ensurePolished()`，女仆模式 30/111 条台词切字问题消失。
- 点「终止」无效：控制归一到根会话；提问气泡占住提醒队列、提醒队列卡死、审批被静默吞掉、审批按钮丢失（含单进程多窗）等整族修复。
- 首次告警必抛 `AttributeError`、`dsh_control` 每次请求必 TypeError、opencode 假完成、429 双提醒、联动气泡时钟域错误、检测器连环换弹。
- 子肥鱼杀不掉/误杀/控制台弹窗/覆盖用户存档整族修复；POSIX 选举死循环（issue #42）；会话并发覆盖与幻影消息；跨 DPI 重建；macOS Dock/Finder node；Linux 输入法。
- CI 原生崩溃根治：`parent=None` 的 manager 被循环 GC 在 worker 线程回收 → 腐化 Qt 事件队列（现 shutdown 过继 QApplication + 停定时器 + 控制 worker 可取消）。

### PR #76（2026-09-06 合并）

**性能与内存**
- 桌宠长时运行内存不再单调上涨：非显示 clip 清空显示槽，修复每播一段动画就滞留约 1.76MB 的慢涨主因；3.5 小时浸泡无泄漏。
- ffmpeg 解码改为常驻循环 + `-threads 1`，消灭每 10s 杀进程重启的 churn；圈边界按 `ffmpeg_recycle_minutes`（默认 10 分钟）定期回收。
- 首帧缓存预算默认降到 8MB（4–64 可配），pinned 集瘦身到点击/转向/拖拽等瞬时交互核；新增预测式首帧预热（默认提前 350ms）。
- 未启用音效时不拉起 QtMultimedia（省约 38MB）；PIL 改为截图功能按需导入。

**单进程多窗与共享解码**
- `PetApp` 拆为进程级 `AppShell` + 每窗 `PetInstance`；「单进程多开」转正为设置页正式开关（默认关闭，重启生效）。
- 同角色共享解码链：进程内 `DecodeFanoutHub` 替代旧 shm broker，3 窗 1 进程 1 解码器；旧 `decode_broker.py`（1464 行）退役删除。
- 新开实例首次占用 slot 时从主配置落种；移除 `DSH_PET_SPAWN_FRESH` 强制重播种，**已有 slot 用户存档一律保留**。

**交互稳定性**
- 全屏自动隐藏排除截图覆盖层/工具窗口，修复打字时桌宠频闪第一触发源。
- Windows 鼠标穿透切换改原生 `WS_EX_TRANSPARENT`，不再 `setWindowFlag` 重建原生窗口，根治第二触发源。

**结构治理 / 代码健康**
- `modern_settings_dialog.py` 从 4811 行拆到 1857 行：控件库/菜单布局编辑器/AI 设置页/主题 QSS 四模块拆出；新增行数预算红线。
- 死代码清理净 -1300+ 行：删除被合并静默回退的孤儿文件簇（`settings_widgets.py` 旧残壳、死 QSS、`decode_broker.py` 等），并新增孤儿簇守卫测试。
- 新增/加固架构红线测试：`decode_fanout` 单向依赖、`window.py` 与 `modern_settings_dialog.py` 行数预算、窗口私有面冻结。
- 文档漂移修正 16 条，重写“全程未触被测对象”的假测试；`AGENTS.md` 新增 CI 成本纪律。

**验证**
- 全量测试 **1322 passed / 7 skipped**；CI 三平台（windows/ubuntu/macos）全绿；ruff 干净。
- 实机浸泡 3.5 小时无泄漏；关键并发/生命周期改动经独立静态审查 + 实机回归双闸门。

### PR #73（2026-09-05 合并）

- **点击音效连续播放修复**：每次 `QSoundEffect` 播放前显式 `stop()`，解决“首次点击有音效、后续点击无声”的问题（源码版/试听均验证）。
- **issue #69 修复**：后台音乐检测从 4s 提速到 1s，并在窗口显示时立即检测，唱歌动画响应更快；减少透明窗口偶发频闪。
- **生小肥鱼继承主配置**：通过“生小肥鱼”孵化的新槽位在首次创建时继承主设置，不再出现新鱼恢复默认配置；已有存档的槽位（用户改过的设置）复用时一律保留，不被主配置覆盖。
- **生小肥鱼大小设置**：新增“继承主肥鱼大小”开关（默认开启）；关闭后可为小肥鱼独立选择大小。
- **生小肥鱼灵动岛设置**：新增“继承灵动岛”开关（默认关闭）；关闭时小肥鱼不会打开自己的灵动岛。
- **一键清除子肥鱼**：设置页新增“一键清除子肥鱼”，可关闭所有运行中的小肥鱼并删除其 slot 配置/会话/待办数据，主肥鱼数据不受影响。
- **右键菜单快捷清除**：「桌宠控制」子菜单新增“清除子肥鱼”入口，点击后走同样的确认与清理流程；旧版菜单也同步提供。
- **“吃垃圾文件”模拟投喂**：把本地文件/文件夹拖到桌宠上会播放吃相关动画并弹出统计气泡；累计次数、文件/文件夹数与总大小写入 `<config_dir>/file_eaten_stats.json`；**不会真实删除、移动或修改文件**。
- **Windows PR test 偶发失败处理**：PR 合并前 Windows 平台一次主套件偶发失败，对同一提交重跑后通过；最新三平台 CI 均绿。

### v4.1.0（累计版）

- **发布 v4.1.0**：自 v4.0.0 以来的功能与修复完整汇总（详见 GitHub Release）。
- **API / Provider 列表（PR #59）**：AI 设置新增 API 列表，可快速添加 / 删除 / 切换模型服务 Provider，并修复 Provider id 复用与 Key 草稿覆盖问题。
- **灵动岛余额峰谷颜色同步（PR #60）**：灵动岛余额峰谷文字颜色跟随设置的峰谷提示颜色开关（高峰红 / 低谷绿）。
- **纯桌宠菜单入口收敛（PR #60）**：无 Chat 的纯桌宠版本去掉“启动 DeepSeek Harness”入口，只保留“打开网页版 DeepSeek”。
- **系统通知（系统级原生弹窗暂未实现）**：AI 对话在“对话完成 / 生成失败 / 需要授权”时，界面不在前台会用**应用自绘的右下角提示气泡**提醒，点击可跳回会话或打开 AI 设置。**为什么没有接入系统原生通知**（Windows 操作中心 toast / macOS / Linux 桌面通知）：
  - Windows 用 `QSystemTrayIcon.showMessage` 的原生气泡在托盘图标被系统收进“隐藏的图标”区域时可能不弹出（v4.1.0 评估后弃用该通道的直接原因）；要真正进入 Windows 通知中心还需 AppUserModelID + 原生通知 API（WinRT），在 PyInstaller 冻结、多实例与绿色版/安装版并存下涉及打包身份与升级路径，验证成本高；
  - macOS 系统通知需用户授权且强依赖签名打包的 `.app`，绿色/未签名分发下不可靠；Linux 桌面通知依赖各发行版的 DBus/notify 服务，实现不一；
  - 三平台一致且可靠的原生通知仍属待办；在此之前不把“系统通知”当作正式可用能力宣告，设置/文档中若出现相关文案以本说明为准。

### v4.0.5 之后（开发版）

- **多开碰撞引擎「鱼塘碰碰车」（PR #41）**：多开桌宠物理对撞、槽位管理、碰撞 IPC 与缩略图缓存；支持拖拽/甩出/弹弓等物理交互。
- **灵动岛（PR #36）**：新增独立灵动岛胶囊窗口，展示余额/状态信息，可拖拽、贴边吸附、记忆位置，支持暗色/浅色/玻璃风格与自定义图标。
- **聊天窗置顶与点击台词绑定（PR #36）**：AI 聊天窗支持始终置顶；点击桌宠可按配置触发对应台词/动画。
- **快速对话气泡（PR #40）**：点击桌宠头顶气泡直接打开 Quick Chat，长文本分页滚动，支持焦点输入。
- **统一三平台构建与 CI（PR #39）**：统一 Linux/macOS/Windows 构建脚本与测试基建，修复 Wayland 下拖动/拖影，CI 测试全局 mock QMessageBox 等。
- **自定义 Agent 联动通道（PR #44）**：`agent_link.custom_agents` 配置驱动，声明 `{key, name, path}` 即可零代码接入任意符合统一事件协议的 JSONL Agent；新增 `docs/AGENT_LINK_PROTOCOL.md` 协议文档。
- **POSIX 碰撞 IPC 重选修复（PR #46）**：QLocalServer 在 Linux/macOS 残留 Unix socket 导致协调者重选死循环——改为先探测活监听者，确认无人应答再清理残留并重试；补 `bytesAvailable()` 兜底读取，恢复 POSIX 测试覆盖。
- **右键菜单 LTR 与平滑入场（PR #47）**：菜单始终 LTR 布局，不再镜像子菜单；新增 140ms OutCubic 位置动画。
- **碰撞协议与协调者生命周期加固（PR #49）**：协议预算（协调者下行 256 KiB / 客户端上行 4 KiB）、成员数/载荷限制、残留端点恢复、锁文件初始化、成员 freshness 统一、leave/failover/epoch 切换时序修正。
- **拖动不再触发点击音效（PR #50）**：按下阶段不再播放 press 音效，确认是点击后才播放完整 press+release。
- **Cloudflare 1010 请求头优化（PR #50）**：urllib 请求统一增加浏览器特征头（Mozilla User-Agent / Accept-Language 等），规避 opencode go 等网关的 Cloudflare 浏览器签名拦截。
- **点击音效切换/解析失败修复（PR #52）**：每次按下都重置当前音效 pair，切换音效包或解析失败后不会复用上一次的旧音效。

### 构建 / CI 失败经验（v4.0.5 之后）

- **POSIX 碰撞 IPC 测试失败（issue #42）**：Linux/macOS 上协调者被强杀后 `QLocalServer` 的 Unix socket 文件残留，幸存者 `listen()` 报 `AddressInUseError` 导致重选死循环；`submit_leave` 成员表为空属于同批时序问题。处理：先探测活监听者、确认无人应答再清理残留并重试；补 `bytesAvailable()` 兜底读取；测试服务名缩短规避 macOS socket 路径长度上限（PR #46/#49）。
- **WebM 线程回收偶发失败**：`test_rapid_start_stop_no_leaked_running_threads` 在 Linux/macOS 偶发断言残留新出现的非预期线程（含 reader 线程）。处理：线程退出是异步的，断言前给 5s 宽限等待；若仍偶发可重跑定位是否负载相关。
- **macOS 右键菜单动画时序失败**：`test_context_menu_transitions_smoothly_to_safe_target` 原先固定 `qWait(50)` 采样动画中间位置，macOS offscreen 子进程定时器调度延迟时会采样到尚未推进的帧（`middle.x == 40`）。处理：改为轮询等待菜单位置首次变化（上限 2s）后再采样，并等待 `duration + margin` 验证最终位置；恢复严格下界断言（PR #53/#54）。
- **经验总结**：
  - 本地 Windows 全量通过 ≠ 三平台通过；QLocalServer、子进程、UI 动画等平台敏感测试必须跑真实 Linux/macOS。
  - 固定短等待采样 UI 中间态是 flaky 主要来源，应轮询“状态变化”而非假设固定帧率/调度。
  - `workflow_dispatch` 手动触发 CI 只测试+构建+上传 artifact，不创建/修改 Release，适合合并前/后验证。
  - 遇到平台相关 flaky 先定位根因，不要简单放宽断言到“永远通过”。
  - 更完整记录见 `docs/BUILD-CI-FAILURE-NOTES-2026-08.md`。

### v4.0.5（功能版）

- **音效体系升级（PR #33）**：点击音效从单个 `click.wav` 升级为完整音效包——内置默认 / 小黄鸭 / 自定义单文件 / 自定义文件夹随机播放；新增音量（0–100%）与试听；播放层统一 QtMultimedia（`QSoundEffect` 即时重启 + MP3/OGG 解码缓存 + 播放器池兜底），旧配置自动迁移。
- **Agent 联动音效（PR #33）**：start / done / error 三类事件支持独立开关、音效路径与试听，统一音量与全局冷却；内置合成提示音，无版权问题。
- **甩出力度档位（PR #33）**：轻柔 / 标准 / 强力 / 疯狂四档力度，物理计时器改真实 dt + 子步积分，高速甩出不再单步瞬移。
- **弹弓弹射（PR #33）**：左键拖拽中按住右键蓄力，反向拉动后松左键发射；带橡皮筋拉带、抛物线预测轨迹与方向性形变；互斥/失焦/隐藏自动取消。
- **光标隐藏自动穿透（PR #33）**：Windows 下只读 `GetCursorInfo` 轮询，系统光标持续隐藏 200ms 自动鼠标穿透、出现即恢复；绝不调用 `ShowCursor` 干扰其他程序。
- **点击 Q 弹卡顿修复（PR #34）**：音频预热等待 `QSoundEffect` 加载完成、点击动画首帧优先预热、音效延迟到下一事件循环，消除首次点击/快速连点的数百 ms 卡顿。
- **开机自启变体独立（PR #35）**：Chat 版与无 Chat 版各自管理自己的 HKCU Run 自启值，关闭其中一个不再影响另一个。
- **失效开机自启自动清理**：启动时自动清理指向已不存在目录/程序的旧自启项，修复“更新后直接删除旧文件夹但忘了关自启，每次开机弹终端报找不到文件夹”的问题。
- **测试兼容与 Python 版本声明**：QMenu 可见性测试兼容无交互桌面环境；README 明确 CI 使用 Python 3.11、Windows 实机验证覆盖 Python 3.13。

### v4.0.4（功能版）

- **余额分档动画（PR #31）**：同步上游 6 个余额动画（钱袋满溢 / 金袋叮当 / 钱袋如常 / 数金皱眉 / 袋空如洗 / 分文不剩），查询余额时按余额档位自动播放对应动画；余额动画同时进入随机动作池，可随机/手动播放。
- **DeepSeek 峰谷提示（PR #31/#32）**：余额气泡下方显示当前高峰/空闲与下一切换时间；可在设置中选择默认「空闲/高峰」、预设「梁文谷/梁文峰」，或自定义高峰/空闲文本；可开关峰谷提示颜色（默认高峰红、低谷绿）。
- **后台音乐自动唱歌（PR #31）**：新增 Windows 音频检测（pycaw），检测到后台播放音乐时自动循环播放「悠闲哼歌」；可在桌宠设置中开关，默认关闭。
- **移动动画调整（PR #31）**：原「原地漂浮踏步 / 原地左转奔跑」重命名为「漂浮踏步 / 左转奔跑」，并归入移动动画参与自动移动。
- **位置记忆修复（PR #31）**：自动移动/物理抛掷/退出不再覆盖手动保存的位置，重启后回到用户最后一次手动放置的位置。
- **开机自启残留清理（PR #31）**：清理所有已知 dsh-pet 自启项，避免旧残留导致“关闭后仍自启”或“开机两个终端/两个桌宠”。
- **右键菜单稳定性（PR #31/#32）**：释放菜单前非阻塞等待图标解码 worker 结束，降低多次右键后崩溃概率。
- **点击音效打断（开发版）**：再次点击桌宠时会先停止上一段点击音效，避免自定义长音效叠放/排队播放。
- **长文本气泡与主动识屏回复同步（PR #29）**：长文本气泡分页自动翻页不再截断；主动识屏回复全文写入日志并同步进 AI 对话会话。
- **DSH profile 枚举与 thinking 专属气泡（PR #29/#30）**：DSH profile 枚举尊重 `DSH_HOME` 并过滤 `node_modules`；补全 `web_search/read_page` 工具名映射，thinking 状态有独立气泡文案，并支持每个 Agent 自定义 thinking 文案。

### v4.0.3（紧急修复版）

- **Windows 透明像素点击穿透（PR #27）**：修复 Windows 上透明区域被点击拦截的问题，透明像素鼠标穿透、可见像素正常点击。
- **DSH 桥接插件自动安装 pnpm（PR #27）**：一键安装桥接插件时若缺少 pnpm 会自动安装，避免安装失败。
- **Windows 官方包中文乱码修复（PR #28，issue #26）**：三平台构建强制 `PYTHONUTF8=1`，新增打包产物中文编码自检，杜绝菜单/气泡/动作名再次乱码。

### v4.0.2（修复版）

- **自定义点击音效支持 MP3/OGG/FLAC/M4A**：音效播放器重构——WAV 继续走轻量 winsound（Windows），非 WAV 统一走 QtMultimedia（QMediaPlayer，自带 FFmpeg 后端），不可用时 macOS/Linux 回退系统播放器（afplay/paplay/aplay）；修复自定义 MP3 在 Windows 上被 winsound 用系统提示音"播放"的问题（winsound 只支持 WAV，传入 MP3 会响系统默认音）。Linux/macOS 构建同步补打包 PySide6.QtMultimedia。
- **动画边缘毛边/暗边修复**：帧渲染改为**预乘 alpha 缩放**（直通 alpha 缩放会让透明像素的 RGB 渗入半透明边缘，产生暗边/彩边）；Windows 上点击命中测试由 setMask 的 1-bit 裁剪改为**逐像素命中测试**（WM_NCHITTEST + HTTRANSPARENT，透明区域鼠标穿透、可见区域可点击），不再破坏 `WA_TranslucentBackground` 的逐像素半透明边缘。
- **Harness 启动兼容旧版 dsh**：启动前探测 `web --help` 是否支持 `--no-open`（按命令缓存）——旧版 dsh（如 0.1.0-rc.3）没有该选项，强行传参会启动失败；不支持时不传，由 dsh 自己打开浏览器，桌宠不重复打开。
- **动画帧率精度**：视频帧时长按 24fps 精确值（40ms → 42ms = 1000/24）修正，动画播放定时器改用精确定时器（PreciseTimer），消除粗略定时器漂移导致的节奏/移动插值偏差。
- **右键菜单启动提速与避让**：动画分类子菜单**首次展开才填充**动作（根菜单构建不再遍历 106 个动画，首次右键不再卡顿数秒）；菜单弹出位置智能选择——优先角色右侧（子菜单向右展开）、屏幕不够时放左侧并让子菜单向左展开（RTL）、再不行放屏幕远角，根菜单与子菜单都不再遮挡角色；快捷启动应用图标按 (类型, 路径) 缓存（QFileIconProvider 首次取图标慢）。
- **设置窗口打开期间暂停气泡**：新版设置/聊天设置任一打开时，桌宠气泡暂停显示（关闭后恢复），不再盖住设置界面。
- **macOS/Linux 打包补 integrations 资源（PR #22）**：onedir 构建显式打包 `integrations/`（含 DSH 桥接插件），修复 macOS/Linux 上「启动 DeepSeek Harness → 一键安装桥接插件」因资源缺失而失败的问题；构建后增加断言检查，漏打包直接报错。
- **Chat 版显式收集 keyring（API Key 系统安全存储）**：Windows/Linux/macOS 构建均显式 `--collect-all keyring`，确保 Chat 版 API Key 走系统凭据存储可用。
- **安装包卸载流程调整**：安装包卸载时不再自动运行 `--uninstall-cleanup` 清理脚本（卸载更快更直接）；源码运行 `python -m pet --uninstall-cleanup` 仍可用。

### v4.0.1（修复版）

- **Windows「自动隐藏任务栏」下桌宠随任务栏一起隐藏（PR #18）**：开启系统「自动隐藏任务栏」后，最大化窗口会铺满整屏（含任务栏区域），旧的全屏判定只看几何，把它误判为"真全屏"而把桌宠隐藏掉。现在真全屏判定增加**无标题栏**条件——真全屏的游戏/视频/浏览器 F11 都会去掉标题栏，普通最大化窗口带标题栏（`WS_CAPTION`）——自动隐藏任务栏下的最大化窗口不再误触发隐藏；已最大化后按 F11 的窗口（应用清掉了标题栏）仍能正确命中隐藏。
- **副屏位置开机自启不恢复（issue #8，PR #16）**：开机自启时副屏可能尚未就绪（显示器唤醒慢于自启），旧逻辑按屏名找不到目标屏就落主屏定型，之后不再回副屏。现在目标屏暂不在线时会先落主屏、记录目标屏并监听屏幕变化（`screenAdded` 即时触发 + 5 秒轮询兜底），目标屏上线后**自动恢复到保存位置**（2 分钟超时放弃）；等待期间不把临时落脚坐标/屏名写回配置（防止覆盖副屏保存位置）；用户真正开始拖动或点「回到右下角」会立即撤销自动恢复。
- **主动识屏恢复后不再重复请求（PR #16）**：隐藏/恢复过程中不再清空网络请求标志，避免恢复后与仍在飞的历史请求并发发起第二条视觉请求；迟到答复按代次检查丢弃，不冒泡、不计费、不写记忆。
- **DSH 桥接插件安装加固（PR #16）**：profile 枚举只认含 `cordis.yml` 的真实目录，过滤 `node_modules` 等包管理器/误操作残留，避免安装失败触发整体回滚；`pet_opacity` 配置脏值（手改配置文件出错等）不再导致启动崩溃。

### v4.0.0（大版本）
- **新版右键菜单 / 设置 / AI 对话窗口（现代双 UI）**：合并 PR #11（modern desktop pet experience）：紧凑分组线性图标菜单、侧边栏卡片式设置、双栏 AI 对话工作台；PR #13 修复彩蛋弹窗在菜单跟踪结束后才弹出的时序，并加固图片目录回退与 UI 字体懒加载；PR #12 稳定 CI 测试时序。
- **性能与主动陪伴（PR #7）**：桌宠隐藏后暂停全部动画解码与定时器（隐藏 CPU ≈ 0%）、启动懒加载与优先级预热、主动识屏陪伴（白名单/停留门限/每日上限/dry-run）、Agent 联动（DSH 桥接插件 + Claude hooks）。
- **新增桌宠设置**：锁定位置（不可拖动）、SHIFT+左键拖动、不透明度 10%–100%。
- **托盘菜单同步**：鼠标穿透 / 开机自启在设置或右键菜单里改动后，托盘勾选状态弹出前实时刷新。
- **聊天窗渲染修复**：无边框圆角窗口去掉窗外方形背景（深色系统黑框/浅色系统白框）；关闭/最小化/新建/删除等按钮图标改为跟随界面主题的深色图标（深色系统不再白底白图）；鼠标进入窗口后光标不再卡在缩放双箭头（hover 驱动刷新）。
- **旧版聊天窗补全**：新增「重命名当前会话」按钮（含深色主题适配）；会话标题优先显示自定义名称。
- **设置保存链路**：直接点 X 关闭自动保存并立即生效（不再需要点「保存」）；保存前从磁盘重读配置，避免覆盖菜单等外部改动。
- **深色系统全面适配**：设置界面（自绘开关/下拉/选项弹窗）、右键菜单、聊天窗按钮与图标在深色模式下均可读。
- **会话与流式修复**：生成中/结束时快速新建会话不再串写；切换长会话后自动滚到底部；输入法组合中回车不上屏误发；上翻历史不被强制拉回底部。
- **内存与并发**：连续打开菜单不再泄漏（图标线程安全、菜单对象及时销毁）；ChatService 竞态、打字机残留、子进程回收等一批修复。
- **彩蛋与弹窗**：彩蛋图片目录配错/缺失时回退默认图片池；多开彩蛋不错位；气泡不再遮挡右键菜单。

### 2026-08 上旬（v3.1.1 及更早）

- **检查更新（新功能）**：右键菜单与托盘菜单新增「检查更新」——后台查询 GitHub 最新版本（GitHub API 不可达时自动回退 jsDelivr CDN 镜像），点击后桌宠气泡即时反馈；发现新版本时会提示你到「更新 / 帮助」菜单打开 Release 下载页自行下载。另提供「GitHub 项目页」与「夸克网盘下载」（Windows 备用下载渠道）入口。
- **AI 对话会话管理增强**：新增「重命名」按钮（自定义会话标题，可备注会话内容，下拉列表优先显示）；删除当前会话与「清空全部会话」均带确认框（防误删，适合会话列表太多时整体清理）；会话列表移到聊天窗左下角（重命名按钮紧随其后），自动标题截断从 24 字放宽到 40 字，下拉尽量显示完整；API Key 输入框提示"已保存，留空保持不变"——修改 System Prompt 等设置无需重输 Key。
- **「看看屏幕」同步到 AI 对话**：视觉模型的回复会自动写入当前 AI 会话（一条 `[看看屏幕] 前台窗口：…` 记录 + 一条回复），可继续追问"你刚才看到什么了"；聊天窗未打开时仅气泡显示、不写入。
- **点击行为设置（桌宠设置）**：可勾选「点击显示 DeepSeek 余额」「点击随机显示一条自定义自言自语」，两个都勾选时自动排队（先余额约 6 秒、隔 1 秒再自言自语）；多次点击会重置序列，只按最后一次点击从头完整显示（防抖，不叠加气泡）。
- **气泡美化与分页**：气泡改为自绘圆角样式 + 底部小箭头（指向角色）+ 柔和阴影；文字超出单页自动分页，点击气泡翻页（页脚显示「1/3 · 点击翻页」）。
- **主菜单优化**：「AI 设置」与「看看屏幕」位置调换；检查更新 / GitHub 项目页 / 夸克网盘下载收进「更新 / 帮助」二级菜单；「开机自启」「全屏时自动隐藏」移入桌宠设置（保存立即生效）；新增「隐藏桌宠」菜单项与「DeepSeek 余额」入口。
- **点击音效**：点击 Q 弹播放短促音效（内置合成音，桌宠设置可开关；可把自定义 `click.wav` 放到数据目录 `sounds/` 替换）；修复关闭音效后仍出声的问题（设置保存后未同步到窗口实例）。
- **DeepSeek 余额显示**：菜单「DeepSeek 余额」查询官方 `/user/balance` 接口（用当前 API Key），气泡显示"余额 ¥xx（充值 / 赠送）"；30 秒缓存复用，重复查询秒回；桌宠设置可开启自动刷新（分钟级）。实现参考 MeteorNOX/DeepSeek-Balance-Whale-Widget。
- **右键菜单整理**：动画相关（待机/转向/移动/点击回应/随机动作/播放速率）收进「动画」二级菜单；新增「隐藏桌宠」菜单项（托盘菜单/双击托盘图标可恢复显示）。
- **全屏时自动隐藏开关不保存（真 bug）**：配置加载白名单漏掉 `auto_hide_fullscreen`，保存后重启会被重置为默认。已修复（白名单补齐）；并修复高 DPI（125%/150% 缩放）下最大化窗口被误判为全屏而误隐藏的问题（物理像素与逻辑坐标统一换算）——现在只在真全屏（视频全屏/游戏/F11）时隐藏，最大化窗口不隐藏。
- **个别电脑启动报错「'NoneType' object has no attribute 'isNull'」**（2026-08-25）：根因是杀毒软件隔离/删除了包内的 ffmpeg 视频解码组件，首帧解码失败导致崩溃。现已修复：① 帧对象增加 None 防御，不再崩溃；② 解码失败时降级显示"半透明圆 + 角色首字"的占位画面，桌宠保持可见可交互；③ 启动时自检 ffmpeg 组件，不可用则弹窗明确提示（"可能被杀毒软件隔离，请在杀毒软件中恢复/信任后重启"）；④ 首帧预热并发从 8 降到 3，降低 ffmpeg 进程洪峰与杀软拦截概率。
- **点击 Q 弹残留上一动画帧 / 透明边缘 / 耳朵被挡**（2026-08-25）：① 点击时先切换点击回应动画再启动 Q 弹，压扁的是新动画画面而不是旧帧；② 全部动画首帧在启动时后台预解码，首次点击任何动画都不再有同步解码卡顿与旧帧残留窗口；③ Q 弹不再放大宽度——窗口与 mask 固定尺寸下宽度放大会把角色边缘裁剪成透明；④ Q 弹期间窗口 mask 与压扁画面使用同一几何同步绘制，贴近边缘的耳朵/头顶装饰不再被裁剪，点击穿透区域与可见画面一致。
- **Windows 置顶稳定性**：修复系统事件（资源管理器重启、分辨率/DPI 变更、休眠唤醒、驱动更新）导致的置顶偶发丢失——窗口每次显示时用 Win32 `SetWindowPos` 原生重设置顶，并每 30 秒自检一次、检测到丢失自动恢复；点击或拖拽桌宠会把它带回置顶最前（不抢键盘焦点）；开启鼠标穿透时气泡提示"无法通过点击唤回置顶"。
- **macOS 右键/托盘菜单首次点击「AI 设置 / 桌宠设置」无反应**（需再点一次或多次才弹出）：macOS 的上下文菜单是原生 NSMenu 跟踪会话，菜单项触发瞬间新建窗口的 `show/activate` 会被 AppKit 抑制。现统一延迟到菜单关闭后再呈现窗口（含「AI 对话」窗口），右键菜单与托盘菜单均生效。
- **macOS 「启动 DeepSeek Harness」偶发静默失败**（点了没反应、浏览器不打开）：Finder 启动的 .app 环境 PATH 极简，`dsh`/`npx` 的 shebang（`/usr/bin/env node`）在子进程环境里找不到 node。现启动子进程时注入增强 PATH（Homebrew / nvm / volta / bun / pnpm 等常见目录），并将就绪等待从 45s 放宽到 90s；无 npm 环境不再卡顿 15 秒。
- **macOS 对话报「网络连接失败：[SSL: CERTIFICATE_VERIFY_FAILED]」**：发布包此前未内置 CA 证书库（macOS 上 Python 默认 CA 路径为空）。现已将 `cacert.pem` 打进发布包；AI 设置新增「跳过 SSL 证书验证」选项，用于本地网关 / 自签名证书 / 代理拦截场景（仅建议在可信环境关闭）。
- **AI 设置「测试连接」按钮**：由占位提示改为真实连通性测试（含 TLS 校验，10 秒超时），结果直接显示在对话框内；并修复连续多次点击「测试连接」导致进程崩溃退出的问题（后台线程改用 Python daemon 线程 + 信号回主线程，不再使用 QThread）。
- **证书错误可操作提示**：对话发送与「测试连接」遇到证书类错误时，错误信息会附上「可在 AI 设置中勾选『跳过 SSL 证书验证』后重试」的指引。
- **macOS 窗口置顶原生兜底的平台防护**：`[NSWindow setLevel:]` 仅在实际 cocoa 平台执行，非 cocoa（offscreen 测试等）环境不再有段错误风险。


</details>

<details>
<summary><b>已知限制</b></summary>

## 已知限制

- 当前发布只提供 WebM 变体（Chat / 无 Chat）；GIF 变体（Windows/macOS/Linux）自 v4.0.0 起不再发布——包体约 800 MB，构建、上传与下载成本过高。确有需要的用户请按「打包发布」一节自行构建（先运行 `python scripts/convert_to_gif.py --force --clean` 生成素材）。
- 安装包未做代码签名，首次运行时 SmartScreen 可能出现提示，需手动放行；macOS 同样未签名，需 Gatekeeper 放行（右键打开）。
- 当前 macOS 发布只提供 Apple Silicon（arm64）的 onedir .app；Intel Mac 请源码运行或自行构建。
- Linux 发布只提供 x86_64 的 onedir 目录包（WebM 两个变体），需自行安装少量系统库（见「Linux 使用」一节）；建议在 X11 桌面使用，Wayland 会话下透明/置顶表现取决于桌面合成器。
- 当前 AI 对话只实现 OpenAI Chat Completions 兼容协议，不实现 Gemini 原生协议。
- 当前不提供完整 Markdown 渲染、云端同步和编辑历史消息后重发。
- 自言自语文本是本地配置内容，不由模型自动生成情绪或动作。
- 本轮重点验证 Windows 发布包；macOS/Linux 保留配置目录和源码运行兼容路径，具体桌面环境仍建议在目标平台单独验证。
- 角色资源若缺少静态头像，聊天窗使用角色 ID 首字母回退；不会强制从 WebM/GIF 生成头像。
- AI 对话默认校验 HTTPS 证书（发布包内置 CA 证书库）。开着代理/梯子（Clash 等）被证书拦截、或使用本地网关（LM Studio / Ollama 代理 / 自签名证书）时，若报「SSL: CERTIFICATE_VERIFY_FAILED」，可在 AI 设置中勾选「跳过 SSL 证书验证」后重试；该选项同时作用于对话、看看屏幕、余额查询与测试连接。仅建议在可信的内网/本地环境中关闭校验。
- 全屏应用会暂时盖住桌宠：浏览器 F11 全屏、网页/视频全屏、游戏（全屏优化/独占渲染）在 Windows 上是系统级置顶行为，任何置顶窗口都会被其覆盖；退出全屏后桌宠自动恢复。
- Windows 上资源管理器重启、分辨率/DPI 变更、休眠唤醒等系统事件偶发导致置顶丢失：程序每 30 秒自检一次，检测到丢失会自动重新置顶（无需手动操作）。
- 鼠标穿透开启期间桌宠无法被点击，也无法通过点击唤回置顶最前；需要交互时请先关闭「鼠标穿透」（托盘菜单切换，开启时桌宠会气泡提示）。


</details>

<details>
<summary><b>项目文档</b></summary>

## 项目文档

- [`AGENTS.md`](AGENTS.md)：工程指南与 CI 成本纪律（PR #76 后硬性规矩）。
- [`docs/WINDOW_PY_SPLIT_GUIDE.md`](docs/WINDOW_PY_SPLIT_GUIDE.md)：`window.py` 演进指南、功能驱动拆分流程与架构红线说明。
- [`docs/HANDOVER_2026-09.md`](docs/HANDOVER_2026-09.md)：2026-09 性能/结构线交付手册（含后续批次更新说明）。
- [`docs/ONEDIR_PACKAGING.md`](docs/ONEDIR_PACKAGING.md)：onedir 构建、绿色版 zip 与 Inno Setup 安装包流水线。
- [`docs/BUILD_ARTIFACTS-2026-08-22.md`](docs/BUILD_ARTIFACTS-2026-08-22.md)：EXE 构建、大小、哈希和启动验证记录。


</details>

<details>
<summary><b>许可证与致谢</b></summary>

## 许可证与致谢

本项目采用 **MIT License**（见仓库根目录 `LICENSE`），第三方素材与组件授权声明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

**特别感谢：**

- [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)：参考实现与动画素材基础。
- [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)：DeepSeek 余额气泡思路参考。
- **贡献者 [ushio2026-alt](https://github.com/ushio2026-alt)**：贡献了 v4.0.0 的现代桌面体验重构（PR #11：新版右键菜单、新版设置、现代双栏 AI 对话窗口、彩蛋入口、多开孵化）、CI 测试时序稳定（PR #12）与彩蛋弹窗时序/图片回退加固（PR #13）。
- **贡献者 [klxxya](https://github.com/klxxya)**：贡献了拖拽物理、全屏自动隐藏、看看屏幕、聊天窗主题背景、裁切取景、文字动画免镜像、置顶看门狗等增强功能（PR #5），以及 v4.0.0 的性能与主动陪伴体系（PR #7：隐藏零功耗、启动懒加载、主动识屏陪伴、Agent 联动、多实例避让）与合并后的加固修复（PR #16：副屏位置开机自启恢复、DSH 桥接安装加固、主动识屏并发修复）。
- **贡献者 [lscatfish123-cell](https://github.com/lscatfish123-cell)**：贡献了 Windows「自动隐藏任务栏」下桌宠误隐藏的修复（PR #18：全屏判定增加标题栏检查）。
- **Issue 反馈者**：[Viteyun](https://github.com/Viteyun)（图标过小反馈）、[Lorin1470](https://github.com/Lorin1470)（macOS 使用反馈与建议）、[YukidokeAzarea](https://github.com/YukidokeAzarea)（AI 对话报错反馈）、[zangxx66](https://github.com/zangxx66)（macOS 右键菜单反馈）——感谢你们帮助发现并定位问题。
- **所有在 Bilibili 上为本项目提供反馈和建议的观众**：你们的评论、建议与使用反馈是项目持续改进的重要动力。v4.0.0 中的设置自动保存、深色模式适配、会话滚动、看看屏幕同步、气泡层级、托盘同步、锁定/不透明度等多项修复与功能均来自用户实测反馈。

</details>
