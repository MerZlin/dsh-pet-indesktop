# dsh-pet-indesktop

一个基于 **Python + PySide6** 的独立桌面宠物。项目脱离 DSH 运行时，提供透明无边框、置顶、可拖动、角色切换、动画播放、系统托盘和可选 AI 对话能力。

> 当前发布形态为 **onedir 目录打包 + Inno Setup 安装包（`.exe`）+ 便携 zip 绿色版**：安装版与绿色版运行期都不解压、不产生临时缓存，启动快、卸载干净。本文档以 **2026-08-22** 工作区的代码、素材、测试结果和构建产物为准。

## 项目来源与素材声明

本项目改自、源于 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。桌宠的基础交互思路、动画链行为模型和部分资源组织方式来自原项目，感谢原作者的开源贡献。

当前动画素材已同步参考项目近期更新后的高清 WebM 资源。项目以 WebM 目录为动画源，并为每一组 WebM 生成对应 GIF；当前 `assets/characters` 与 `assets/characters_gif` 的相对动画路径保持一致，各包含 91 个动画文件。后续新增或替换动画时，请先更新 WebM，再重新生成 GIF，不要手工维护两套不一致的素材。

## 当前状态

- Windows 发布形态为 **WebM 两个版本**：Chat 版（含 AI 对话）与无 Chat 版，均提供安装包与绿色版。
- 安装包免管理员、按当前用户安装，向导中可自由选择安装盘符与目录；卸载后无残留运行缓存。
- 绿色版解压即用、删除即卸载，可放在任意盘符或 U 盘。
- AI 对话窗口为独立的手机式聊天窗口，不改变桌宠主窗口的透明背景、mask、鼠标穿透和动画状态机。
- WebM 播放速率设置已修复，切换动画后仍会按当前速率播放。
- 支持相邻非待机动画之间的可选等待间隔，默认值为 `0`，保持连续播放行为。
- 支持可开关的随机自言自语气泡，并优先定位在角色当前可见形象的正上方。

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
- **想开机自启？** 右键托盘 → 勾选「开机自启」即可（写入当前用户注册表 Run 键，无需管理员）。
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

## 功能概览

### 桌宠窗口

- PySide6 透明、无边框、置顶窗口。
- 保留桌宠窗口的透明背景、mask、鼠标穿透和动画状态机。
- 支持点击互动、拖动、拖动惯性、方向转向和系统托盘。
- 支持角色切换；角色目录按素材自动发现，不要求把角色写死在代码中。
- 右键菜单可打开设置、AI 对话、AI 设置、角色选择和退出入口。
- 右键菜单与托盘菜单提供「启动 DeepSeek Harness」：一键后台拉起 `dsh web`（端口 3080）并自动打开浏览器；已在运行时直接打开页面。启动命令自动适配不同安装方式（PATH 上的 `dsh` → node + npm 全局包 → 官方 `npx @deepseek-ai/dsh`），macOS 同样可用（.app 环境会额外探测 Homebrew/nvm 等常见目录，需装有 Node.js）。

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

## 使用教程

### 基本操作

| 操作 | 效果 |
|---|---|
| 左键点击桌宠 | 触发点击互动动画 |
| 按住并拖动 | 移动桌宠；松开后根据拖动方向和速度处理转向、移动或惯性 |
| 右键桌宠 | 打开上下文菜单 |
| 双击托盘图标 | 显示 / 隐藏桌宠 |
| 右键托盘图标 | 打开设置、AI 对话、开机自启、启动 DeepSeek Harness、退出等菜单 |
| 右键桌宠 | 打开上下文菜单（含「启动 DeepSeek Harness」） |
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

### 切换角色

1. 打开右键菜单中的角色选择入口。
2. 选择角色后，桌宠会加载对应角色目录中的动画。
3. Chat 版会同步更新聊天窗口的角色名称、头像回退、主题色、有效 system prompt 和会话列表。
4. 角色之间的消息历史相互隔离。

## AI 对话使用教程（Chat 版）

### 第一步：配置 API

1. 右键桌宠（或托盘菜单）→「AI 设置」。
2. 新建或选择一个 Provider。
3. 填写兼容接口的 API 地址、模型、超时和生成参数。
4. 填写 API Key，并按提示选择钥匙串或配置文件回退。
5. 使用「连接测试」确认配置可用。

首期协议是 OpenAI Chat Completions 兼容协议。Gemini 等其他服务只有在提供兼容网关或兼容端点时才可使用。

### 第二步：开始对话

1. 右键桌宠（或托盘菜单）→「AI 对话」。
2. 聊天窗第一次打开时会定位在桌宠旁边，并根据桌宠当前可见形象边界和屏幕边界自动避让。
3. 输入区支持多行输入：`Enter` 发送，`Shift+Enter` 换行；生成中按钮变为「停止」。
4. 可在 AI 设置中开启或关闭「跟随桌宠移动」。

聊天窗为独立的手机式外观，包含：

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
- 会话标题优先取第一条用户消息，无法生成时使用时间标题。
- 可在顶部下拉框切换已有会话。
- 可新建、删除当前会话或清空消息。
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
2. 放入透明 WebM 文件，命名保持稳定、避免重复。
3. 如有角色身份信息，在 `<character_id>/manifest.json` 中填写名称、prompt、主题色和动作映射。
4. 如需 GIF 变体，运行 GIF 转换脚本同步生成 GIF。
5. 使用源码运行或重新打包验证角色切换、播放、气泡定位和 Chat 身份区。

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

## 旧版 onefile 缓存清理（仅旧版本需要）

旧版单文件 EXE（onefile）运行时会在系统临时目录创建 `_MEI数字` 目录，崩溃或强制结束时可能残留；**当前 onedir 发布版不会再产生该缓存**。程序启动时仍会自动尝试清理超过 24 小时的遗留目录，并跳过当前进程正在使用的运行目录；权限不足或目录被占用时只记录日志，不强制修改 ACL。

也可以使用项目提供的专用脚本检查：脚本默认只预览，不会删除任何目录。确认所有桌宠进程都已退出后，才使用 `--delete`：

```powershell
python scripts/cleanup_mei_cache.py
python scripts/cleanup_mei_cache.py --min-age-hours 0
python scripts/cleanup_mei_cache.py --delete
```

如果某些目录因权限异常仍无法删除，请先退出所有桌宠，再用管理员 PowerShell 运行脚本；脚本不会自动接管目录所有权，避免误操作其他临时文件。

## 配置与安全说明

- API Key 不会写入日志，也不应放入截图、Issue 或公开配置。
- 默认优先使用系统钥匙串；钥匙串不可用时，设置界面会提示配置文件回退风险。
- 会话文件保存在本地，不实现云端同步。
- OpenAI 兼容接口的错误响应、网络异常和空响应会转换为界面错误状态，并保留用户消息供重试。
- 当前消息按纯文本显示；不要把不可信的模型输出当作 HTML 或脚本执行。

## 已知限制

- 当前发布只提供 WebM 变体（Chat / 无 Chat）；GIF 变体包体约 800 MB，不再默认发布，需要时按「打包发布」一节自行构建。
- 安装包未做代码签名，首次运行时 SmartScreen 可能出现提示，需手动放行；macOS 同样未签名，需 Gatekeeper 放行（右键打开）。
- 当前 macOS 发布只提供 Apple Silicon（arm64）的 onedir .app；Intel Mac 请源码运行或自行构建。
- 当前 AI 对话只实现 OpenAI Chat Completions 兼容协议，不实现 Gemini 原生协议。
- 当前不提供完整 Markdown 渲染、云端同步和编辑历史消息后重发。
- 自言自语文本是本地配置内容，不由模型自动生成情绪或动作。
- 本轮重点验证 Windows 发布包；macOS/Linux 保留配置目录和源码运行兼容路径，具体桌面环境仍建议在目标平台单独验证。
- 角色资源若缺少静态头像，聊天窗使用角色 ID 首字母回退；不会强制从 WebM/GIF 生成头像。

## 项目文档

- [`SPEC.md`](SPEC.md)：项目设计边界和验收标准。
- [`LOG.md`](LOG.md)：施工记录和决策说明。
- [`LOG-INDEX.md`](LOG-INDEX.md)：施工记录索引。
- [`docs/ONEDIR_PACKAGING.md`](docs/ONEDIR_PACKAGING.md)：onedir 构建、绿色版 zip 与 Inno Setup 安装包流水线。
- [`docs/BUILD_ARTIFACTS-2026-08-22.md`](docs/BUILD_ARTIFACTS-2026-08-22.md)：EXE 构建、大小、哈希和启动验证记录。

## 许可证与致谢

请以仓库中的许可证文件和上游项目说明为准。再次感谢 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 提供的参考实现与动画素材基础。
