# dsh-pet-indesktop

一个基于 **Python + PySide6** 的独立桌面宠物。项目脱离 DSH 运行时，提供透明无边框、置顶、可拖动、角色切换、动画播放、系统托盘和可选 AI 对话能力。

> 当前发布形态为 **onedir 目录打包 + Inno Setup 安装包（`.exe`）+ 便携 zip 绿色版**：安装版与绿色版运行期都不解压、不产生临时缓存，启动快、卸载干净。本文档以 **2026-08-22** 工作区的代码、素材、测试结果和构建产物为准。

## 目录

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
- [最近修复（2026-08）](#最近修复2026-08)
- [已知限制](#已知限制)
- [项目文档](#项目文档)
- [许可证与致谢](#许可证与致谢)

---

<details>
<summary><b>项目来源与素材声明</b></summary>

## 项目来源与素材声明

本项目改自、源于 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。桌宠的基础交互思路、动画链行为模型和部分资源组织方式来自原项目，感谢原作者的开源贡献。

DeepSeek 余额显示（气泡/小部件思路）参考了 [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)，本项目的实现为桌宠内置的轻量版（菜单「DeepSeek 余额」+ 可选自动刷新，通过 DeepSeek 官方 `/user/balance` 接口查询，详见 [DeepSeek API 查询余额文档](https://api-docs.deepseek.com/zh-cn/api/get-user-balance/)）。

当前动画素材已同步参考项目近期更新后的高清 WebM 资源。项目以 WebM 目录为动画源，并为每一组 WebM 生成对应 GIF；当前 `assets/characters` 与 `assets/characters_gif` 的相对动画路径保持一致，各包含 91 个动画文件。后续新增或替换动画时，请先更新 WebM，再重新生成 GIF，不要手工维护两套不一致的素材。


</details>

<details>
<summary><b>当前状态</b></summary>

## 当前状态

- Windows 发布形态为 **WebM 两个版本**：Chat 版（含 AI 对话）与无 Chat 版，均提供安装包与绿色版。
- 安装包免管理员、按当前用户安装，向导中可自由选择安装盘符与目录；卸载后无残留运行缓存。
- 绿色版解压即用、删除即卸载，可放在任意盘符或 U 盘。
- AI 对话窗口为独立的手机式聊天窗口，不改变桌宠主窗口的透明背景、mask、鼠标穿透和动画状态机。
- WebM 播放速率设置已修复，切换动画后仍会按当前速率播放。
- 支持相邻非待机动画之间的可选等待间隔，默认值为 `0`，保持连续播放行为。
- 支持可开关的随机自言自语气泡，并优先定位在角色当前可见形象的正上方。


</details>

<details>
<summary><b>下载与版本选择</b></summary>

## 下载与版本选择

正式发布时请以 [Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases) 页面实际上传的文件为准。当前推荐下载的 Windows 产物如下：

| 版本 | 安装包（setup.exe） | 绿色版（zip） | 适合场景 |
|---|---|---|---|
| Chat WebM | `dsh-pet-standalone-webm-chat-setup.exe`（约 100 MB） | `dsh-pet-standalone-webm-chat-portable.zip`（约 122 MB） | WebM 高清播放 + AI 对话，功能完整 |
| 无 Chat WebM | `dsh-pet-standalone-webm-setup.exe`（约 100 MB） | `dsh-pet-standalone-webm-portable.zip`（约 122 MB） | 只想要桌宠本体，不接入 AI |

选择建议：

- **想体验完整功能（含 AI 对话）**：装 Chat 版。
- **只需要桌宠陪伴**：装无 Chat 版，包体更小、启动更轻。
- **不想安装、追求便携**：用绿色版 zip，解压到任意目录双击即用。

> 两个版本使用同一套高清 WebM 素材（91 段动画），只是入口不同：Chat 版会加载聊天子系统，无 Chat 版完全不携带 AI 对话依赖。
>
> 旧版 GIF 超大单文件（约 800 MB，运行时会在 C 盘临时目录解压并可能残留缓存）不再默认发布；确有需要可参考本文档「打包发布」一节自行构建 GIF 变体。
>
> macOS（Apple Silicon）用户：产物为 `dsh-pet-standalone-*-macos-arm64.zip`（onedir .app），由 GitHub Actions 构建，见下方「macOS 使用」。
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
- **配置存在哪里？** 设置与聊天会话保存在 `%APPDATA%\dsh-pet-standalone\`，重装/升级不会丢失。

### 方式二：绿色版（zip）免安装

1. 下载 `dsh-pet-standalone-webm-chat-portable.zip`。
2. 解压到任意可写目录（例如 `E:\dsh-pet\`），**保持文件夹内结构完整**。
3. 双击文件夹里的 `dsh-pet-standalone-webm-chat.exe` 即可运行。
4. 删除整个文件夹即完成卸载，不残留任何运行缓存。

> 绿色版与安装版是同一套 onedir 产物，运行行为完全一致；区别只是安装版多了快捷方式与卸载器。

### 卸载

- **安装版**：`设置 → 应用 → 已安装的应用`（或「控制面板 → 程序和功能」）→ 找到 `dsh-pet-standalone (WebM Chat)` → 卸载。
- 卸载程序会删除安装目录与快捷方式；`%APPDATA%\dsh-pet-standalone\` 中的配置与会话默认保留，如需彻底清除可手动删除该目录。

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

建议使用 Python 3.10 或更高版本，并在项目根目录执行：

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

- PySide6 透明、无边框、置顶窗口。
- 保留桌宠窗口的透明背景、mask、鼠标穿透和动画状态机。
- 支持点击互动、拖动、拖动惯性、方向转向和系统托盘。
- 支持角色切换；角色目录按素材自动发现，不要求把角色写死在代码中。
- 右键菜单只维护紧凑新版布局；新版独立使用现代弹出菜单风格，并可在“外观”中调整跟随系统/浅色/深色、UI 字体与字号、菜单密度、圆角、浅深主题色和半透明表面。AI 对话保留现代与经典窗口切换。新版首行彩蛋与“快捷启动”均可配置，「生小肥鱼」会启动一只独立的新桌宠。
- 右键菜单与托盘菜单提供「启动 DeepSeek Harness」：一键后台拉起 `dsh web`（默认端口 38080，可用环境变量 `DSH_PORT` 覆盖；注：3080 在部分 Windows 上会落入 winnat/Hyper-V 保留段导致无法监听）并自动打开浏览器；已在运行时直接打开页面。启动命令自动适配不同安装方式（PATH 上的 `dsh` → node + npm 全局包 → 官方 `npx @deepseek-ai/dsh`），macOS 同样可用（.app 环境会额外探测 Homebrew/nvm 等常见目录，需装有 Node.js）。

### 动画播放

- WebM 版：直接播放透明 WebM，默认素材为 640×360、24fps。
- 播放速率可在设置中调整，当前范围为 `1.0x` 到 `2.0x`。
- 动画按 `idle`、`turn`、`move`、`click`、`drag`、`random` 等目录组织。
- 支持相邻非待机动画之间的等待间隔；等待期间只播放待机和转向动画。
- 支持随机自言自语气泡；没有自定义文本时使用内置文本。

### AI 对话（Chat 版）

- 支持 OpenAI Chat Completions 兼容接口。
- 支持自定义 API 地址、模型、超时、温度和最大输出 token。
- 支持 SSE 流式输出、多轮上下文裁剪、会话 JSON 持久化、停止生成、失败重试。
- 会话按角色隔离；切换角色时不会把旧角色消息带入新角色。
- 聊天窗口靠近桌宠显示，并支持用户选择是否跟随桌宠移动。
- API Key 优先使用系统钥匙串；钥匙串不可用时可按设置选择配置文件回退。
- 纯文本安全显示，不包含完整 Markdown 渲染器。

### 看看屏幕（Chat 版）

- 右键菜单 →「看看屏幕」：截取当前屏幕（含多显示器）→ 附带前台窗口「程序名 | 标题」上下文 → 发给视觉模型，用人设口吻回应一句（关心/吐槽/好奇），结果以气泡显示。
- **回复会自动同步到 AI 对话当前会话**（一条 `[看看屏幕] 前台窗口：…` 记录 + 一条回复），可继续追问；聊天窗未打开时仅气泡显示、不写入。
- 截图自动压缩（最长边 768px、JPEG 70）后只保存在本地 `screenshots/`（自动保留最近 20 张），仅发送到你自行配置的 API，不会上传到任何第三方。
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


</details>

<details>
<summary><b>使用教程</b></summary>

## 使用教程

### 基本操作

| 操作 | 效果 |
|---|---|
| 左键点击桌宠 | 触发点击互动动画 |
| 按住并拖动 | 移动桌宠；松开后根据拖动方向和速度处理转向、移动或惯性 |
| 右键桌宠 | 打开带图标的上下文菜单；可切换新旧菜单模板，或选择「生小肥鱼」启动独立的新桌宠 |
| 双击托盘图标 | 显示 / 隐藏桌宠 |
| 右键托盘图标 | 打开设置、AI 对话、开机自启、启动 DeepSeek Harness、退出等菜单 |
| 拖拽桌宠时 | 若开启了聊天窗跟随，聊天窗口会一起移动；默认不跟随 |

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

聊天窗为独立的现代桌面双栏外观，左侧管理会话与跟随状态，右侧集中显示消息时间线与输入区，包含：

- 自绘标题栏：角色头像、窗口拖动、最小化、关闭和双击最大化/还原。
- 上下文栏：Provider 状态、当前会话、新建/删除/清空会话。
- 消息时间线：用户和桌宠气泡、流式回复、错误和停止状态。

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
- 会话标题优先取用户自定义标题（✎ 重命名按钮，可备注会话内容），未自定义时取第一条用户消息，无法生成时使用时间标题。
- 会话管理列表位于聊天窗**左下角**（重命名按钮紧随其后），其余操作按钮（新建/删除/清空等）在顶部工具栏。
- 可在顶部下拉框切换已有会话。
- 可新建、删除当前会话（带确认，防误删）或清空消息。
- 可一键清空该角色的全部会话（带确认，适合会话列表太多时整体清理）。
- 生成过程中会限制切换和删除，避免旧请求污染新会话。
- 停止生成时，未完成的半截 assistant 内容不会作为完整消息保存。

配置与会话目录：

| 系统 | 数据目录 |
|---|---|
| Windows | `%APPDATA%/dsh-pet-standalone/` |
| macOS | `~/Library/Application Support/dsh-pet-standalone/` |
| Linux | `~/.config/dsh-pet-standalone/` |

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
├── characters/
│   └── shenshen/
│       ├── manifest.json
│       └── videos/
│           ├── idle/
│           ├── turn/
│           ├── move/
│           ├── click/
│           ├── drag/
│           └── random/
└── characters_gif/
    └── shenshen/
        └── videos/
            └── 与 characters/<id>/videos 相同的相对路径
```

- `assets/characters` 是 WebM 动画源目录。
- `assets/characters_gif` 是由 WebM 生成的 GIF 目录，仅在需要构建 GIF 变体时使用。
- 两套目录中的角色 ID、子目录和文件相对路径应保持一致。
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
- **按秒分解**：每个动作的配方按 0~3 / 3~7 / 7~10 秒（或 0~2 / 2~5 / 5~7 / 7~10）分段描述动作节奏，确保生成结果可复现。

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

> 参考项目全部 91 个动作均采用**路线 B（PR 手工抠像）**：对含第三方物品/透明边缘复杂的动作，自动 HSV 抠像易残边或误抠；`chroma_step02.py` 保留为自动化兜底。中间产物 step01~04 由脚本生成、不入仓库；`video/` 源视频与 `scripts/` 是成果、入库维护。

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
3. 命名保持稳定、避免重复；可参考 `assets/characters/shenshen/videos/` 现有 91 段动画的组织方式。
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
├── app.py                 # 应用入口、托盘、角色切换和聊天集成
├── config.py              # 配置读取、迁移和持久化
├── window.py              # 桌宠主窗口、透明/mask/鼠标穿透和动画状态机
├── catalog.py             # 角色和动画素材发现
├── library.py             # 动画库访问
├── webm_clip.py           # WebM 播放和速率控制
├── gif_clip.py            # GIF/QMovie 播放
├── speech_bubble.py       # 自言自语与状态气泡定位
└── chat/                  # 独立 AI 对话子系统
    ├── models.py
    ├── providers.py
    ├── prompt.py
    ├── service.py
    ├── session_store.py
    ├── widgets.py
    ├── settings_dialog.py
    ├── pet_link.py
    └── styles.qss

packaging/
├── pet_entry.py           # Chat 构建入口
├── pet_entry_no_chat.py   # 无 Chat 构建入口
└── dsh-pet.iss            # Inno Setup 通用安装包脚本（/D 参数编译各变体）

scripts/
├── build_onedir.ps1       # onedir 构建 + zip 绿色版打包
├── make_icon.py           # 从待机动画提取封面帧生成应用图标（assets/icon.ico）
├── convert_to_gif.py      # WebM → GIF 全量同步脚本
└── cleanup_mei_cache.py   # 检查/清理旧 onefile 版本遗留的 _MEI 缓存（默认预览）

tests/                     # 单元测试、Qt offscreen 测试和构建相关验证
```


</details>

<details>
<summary><b>测试与验证</b></summary>

## 测试与验证

在项目根目录执行：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
python -m compileall pet packaging scripts
```

最近一轮记录：

- `pytest`：33 passed。
- `compileall`：通过。
- WebM Chat、WebM 无 Chat 两个 onedir 构建均完成启动冒烟验证：进程存活超过 8 秒，系统临时目录与程序目录**均无新增 `_MEI` 缓存**。

如果要验证真实窗口，不要设置 `QT_QPA_PLATFORM=offscreen`，直接运行 `python -m pet` 或打包后的程序，重点检查：

1. 桌宠透明背景、鼠标穿透、拖动和动画播放没有回归。
2. 自言自语气泡位于角色形象正上方，靠近屏幕边缘时不会遮住角色。
3. 动作等待间隔只限制相邻非待机动画，不阻塞待机、转向和窗口操作。
4. WebM 播放速率切换后，当前片段和下一片段节奏都发生变化。
5. Chat 窗口不透明、位于桌宠可见形象旁边，跟随开关符合设置。
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

### 2) Inno Setup 安装包

本机已安装便携版 ISCC：`E:\tools\InnoSetup6\ISCC.exe`（免管理员）。通用脚本 `packaging\dsh-pet.iss` 用 `/D` 定义编译不同变体：

```powershell
# WebM Chat 版（脚本默认值）
E:\tools\InnoSetup6\ISCC.exe packaging\dsh-pet.iss

# WebM 无 Chat 版
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-webm /DMyAppExeName=dsh-pet-standalone-webm.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-webm "/DMyAppId={{ED2590E4-A968-4E8D-B7C4-75DFE012D0E9}}" "/DMyAppDisplay=dsh-pet-standalone (WebM)" packaging\dsh-pet.iss
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

本地构建（在 Linux 机器上）：

```bash
python -m pip install pyinstaller
# WebM Chat 版（VARIANT 取值 webm-chat / webm / gif-chat / gif）
echo "VARIANT = 'webm-chat'" > packaging/build_variant.py
python -m PyInstaller --noconfirm --clean --onedir --paths . \
  --collect-all imageio_ffmpeg --collect-all certifi \
  --add-data "assets/sounds:assets/sounds" \
  --add-data "assets/chat:assets/chat" \
  --add-data "pet/chat/styles.qss:pet/chat" \
  --add-data "assets/characters:assets/characters" \
  --name dsh-pet-standalone-webm-chat packaging/pet_entry.py
cd dist && zip -r dsh-pet-standalone-webm-chat-linux-x86_64.zip dsh-pet-standalone-webm-chat/
```

> GIF 变体需先运行 `python scripts/convert_to_gif.py --force --clean` 并把 `--add-data` 换成 `assets/characters_gif`。
> Linux 上 PyInstaller 忽略 `--icon`（仅 Windows/macOS 生效），窗口/托盘图标由程序运行时设置。


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
<summary><b>最近修复（2026-08）</b></summary>

## 最近修复（2026-08）

- **检查更新与一键下载（新功能）**：右键菜单与托盘菜单新增「检查更新」——后台查询 GitHub 最新版本（GitHub API 不可达时自动回退 jsDelivr CDN 镜像），点击后桌宠气泡即时反馈；有新版本时可直接下载当前平台/变体安装包（流式下载、进度框显示保存路径/大小/实时速度、可取消，完成后可一键打开所在文件夹），或打开 Release 下载页。另提供「GitHub 项目页」与「夸克网盘下载」（Windows 备用下载渠道）入口。
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

- 当前发布只提供 WebM 变体（Chat / 无 Chat）；GIF 变体包体约 800 MB，不再默认发布，需要时按「打包发布」一节自行构建。
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

- [`SPEC.md`](SPEC.md)：项目设计边界和验收标准。
- [`LOG.md`](LOG.md)：施工记录和决策说明。
- [`LOG-INDEX.md`](LOG-INDEX.md)：施工记录索引。
- [`docs/ONEDIR_PACKAGING.md`](docs/ONEDIR_PACKAGING.md)：onedir 构建、绿色版 zip 与 Inno Setup 安装包流水线。
- [`docs/BUILD_ARTIFACTS-2026-08-22.md`](docs/BUILD_ARTIFACTS-2026-08-22.md)：EXE 构建、大小、哈希和启动验证记录。


</details>

<details>
<summary><b>许可证与致谢</b></summary>

## 许可证与致谢

本项目采用 **MIT License**（见仓库根目录 `LICENSE`）。

**特别感谢：**

- [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)：参考实现与动画素材基础。
- [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)：DeepSeek 余额气泡思路参考。
- **贡献者 [klxxya](https://github.com/klxxya)**：贡献了拖拽物理、全屏自动隐藏、看看屏幕、聊天窗主题背景、裁切取景、文字动画免镜像、置顶看门狗等一批增强功能（PR #5）。
- **Issue 反馈者**：[Viteyun](https://github.com/Viteyun)（图标过小反馈）、[Lorin1470](https://github.com/Lorin1470)（macOS 使用反馈与建议）、[YukidokeAzarea](https://github.com/YukidokeAzarea)（AI 对话报错反馈）、[zangxx66](https://github.com/zangxx66)（macOS 右键菜单反馈）——感谢你们帮助发现并定位问题。
- **Bilibili 上所有为本项目提供反馈和建议的观众**：你们的评论、建议与使用反馈是项目持续改进的重要动力。

</details>
