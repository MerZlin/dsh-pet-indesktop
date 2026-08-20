# dsh-pet-indesktop

## 下载

无需从源码构建，直接到 [Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)
页面下载对应系统的安装包：

| 你的系统                        | 安装包                              | 说明                                                                  |
| ------------------------------- | ----------------------------------- | --------------------------------------------------------------------- |
| Windows                         | `dsh-pet-standalone-webm.exe`       | 双击即跑，首次启动解压需几秒                                          |
| macOS（Apple Silicon / M 系列） | `dsh-pet-indesktop-macos-arm64.zip` | 解压得 `dsh-pet-indesktop.app`，首次打开需放行（见下方「macOS」章节） |
| macOS（Intel）                  | —                                   | 暂无安装包，请按「macOS」章节源码运行                                 |

> 文件名以 Release 页面实际发布为准。

> **声明与致谢**：本项目改自、源于 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。
> 桌宠的动画素材、动画链行为模型、交互设计均来自原项目，特此声明并感谢原作者的贡献。

把 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 插件里的桌宠，改造成一个
**跨平台的独立桌面宠物**软件（支持 **Windows** 与 **macOS**）—— 不依赖 DSH 运行时，
用 Python + PySide6 实现，双击即跑，复用原项目 51 段高清动画（640×360，24fps）。

当前版本为 **webm 直解路线**：运行时直接解码上游 640×360 透明 webm（VP9 + 8-bit alpha），
不再打包体积庞大的 GIF 素材，画质与 web 端一致。

Windows 用户见下方「快速开始 / 打包为 exe」，macOS 用户见「macOS」章节。

## 相关优化项目

[ianlike-ui/dsh-pet-standalone](https://github.com/ianlike-ui/dsh-pet-standalone)

这是其他开发者基于本项目做的优化实现，可能在播放性能、打包体积、多角色支持或使用体验等方面进行了改进。  
如果你希望体验社区优化版，可以前往该仓库查看说明和最新成果。

## 特性

- **webm 高清播放**：直接运行时解码 640×360 透明 webm（VP9 + 8-bit alpha），保留半透明边缘
- **动画链**：每个动画播完按概率选下一个 —— 30% 待机 / 10% 转向 / 40% 随机动作 / 20% 移动，永不停止
- **多形象支持**：支持用户通过外部目录添加自定义角色
- **角色热切换**：右键桌宠或托盘菜单可随时切换形象，无需重启
- **屏幕漫游**：朝面向方向行走，先检查屏幕空间、不走出屏幕（移动动画前后各 2s 准备/收尾，位置由代码驱动）
- **左右朝向**：转向动画播完翻转朝向，所有动画支持水平镜像
- **点击回应**：点击宠物随机播放当前角色配置的回应动画（链上非待机动画播放中不打断）
- **点击 Q 弹**：点击时立即产生“变矮再复原”的挤压回弹反馈；连续点击可打断当前动画并重复触发 Q 弹
- **拖拽**：按住拖动超过 5px 判定为拖拽，宠物播放"悬空反馈"动画跟手，松手停在原地
- **透明穿透**：窗口逐帧按人物 alpha 生成 mask，透明区域鼠标直接穿透到下层窗口
- **右键菜单**：手动播放待机/转向/移动/点击回应/随机动作、切换角色、回到右下角、窗口置顶、不移动、开机自启、4 档大小、退出
- **系统托盘**：显示/隐藏、切换角色、开机自启、退出；位置/朝向/大小/置顶自动持久化
- **开机自启**：Windows 写 HKCU 注册表 Run 键 / macOS 写 LaunchAgents（均无需管理员权限），可随时开关

## 自定义角色教程

你可以通过两种方式使用自定义形象：

1. **随 exe 打包**：把形象放到项目源码的 `assets/characters/<角色ID>/videos/`，重新打包 exe。
2. **用户本地外部扩展**：不需要重新打包，直接在 exe 同目录或用户数据目录放置形象文件夹。

### 方法一：随 exe 打包

在项目源码中创建：

```text
assets/characters/<角色ID>/videos/
├── idle/
├── turn/
├── move/
├── click/
├── drag/
└── random/
```

把对应分类的 `.webm` 放进去，然后重新执行打包命令即可。

### 方法二：exe 同目录外部扩展（推荐给最终用户）

在 exe 同目录下创建：

```text
<exe 所在目录>/
├── dsh-pet-standalone-webm.exe
└── characters/
    └── <角色ID>/
        └── videos/
            ├── idle/
            ├── turn/
            ├── move/
            ├── click/
            ├── drag/
            └── random/
```

也支持用户数据目录：

```text
Windows: %APPDATA%/dsh-pet-standalone/characters/<角色ID>/videos/
macOS:   ~/Library/Application Support/dsh-pet-standalone/characters/<角色ID>/videos/
```

程序启动或切换角色时会自动检测：

- 外部目录存在 → 优先使用外部形象。
- 外部目录不存在 → 回退到 exe 内置形象，不会报错。

### 如何准备自定义形象的动画（参考项目绘制方法）

推荐直接参考上游项目 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)：

1. 克隆或下载参考项目：
   ```sh
   git clone --depth 1 https://github.com/PC2005-cloud/dsh-pet.git
   ```
2. 参考项目中的透明动画位于：
   ```text
   dsh-pet/dsh-pet/assets/thumb/*.webm
   ```
   这些是 640×360 透明 webm（VP9 + 8-bit alpha）。
3. 你可以：
   - 直接复制这些 webm 作为基础形象；
   - 或参考它们的动作分类，生成自己角色的同尺寸透明 webm；
   - 或使用 ffmpeg / 图像生成工具制作新的透明动画，只要输出 640×360 透明 webm 即可。

4. 将制作好的 webm 按分类放入对应子目录：

```text
videos/
├── idle/     # 待机动画
├── turn/     # 转向动画
├── move/     # 移动动画
├── click/    # 点击回应动画
├── drag/     # 拖拽动画（可选）
└── random/   # 随机动作动画
```

5. 如果文件名无法通过关键词自动识别，可以添加 `manifest.json` 精确指定分类。

6. 启动后在桌宠右键菜单或托盘菜单的「切换角色」中选择你的新角色 ID。

## 使用手册

### 启动与退出

- **启动**：双击 `run.bat`（或 `python -m pet` / 打包后的 exe），桌宠出现在屏幕右下角
- **退出**：右键桌宠 →「退出」，或点系统托盘图标 →「退出」

### 鼠标交互

- **点击**：单击宠物本体，随机播放当前角色配置的回应动画之一，并触发 Q 弹挤压回弹效果。
  连续点击可以打断当前动画，反复触发 Q 弹，手感更跟手。
- **拖拽**：按住拖动超过 5px 判定为拖拽，宠物播放「悬空反馈」动画跟手，松手停在原地
- **穿透**：只有宠物本体（不透明区域）可点，其余透明区域鼠标直接穿透到下层窗口

### 右键菜单（右键点击宠物本体）

| 菜单项          | 功能                                                                                      |
| --------------- | ----------------------------------------------------------------------------------------- |
| 动画 · 待机     | 手动播放待机动画；如果待机目录有多个视频，会显示二级菜单                                  |
| 动画 · 转向     | 手动播放转向动画；如果转向目录有多个视频，会显示二级菜单                                  |
| 动画 · 移动     | 手动播放移动动画（走路姿态 + 朝面向方向真实走动；「不移动」模式下这是唯一触发移动的方式） |
| 动画 · 点击回应 | 手动播放点击回应动画                                                                      |
| 动画 · 随机动作 | 手动播放随机动作动画                                                                      |
| 切换角色        | 热切换当前形象（内置 + 外部扩展角色都会列出）                                             |
| 回到右下角      | 把宠物复位到屏幕右下角                                                                    |
| 窗口置顶        | 勾选 = 始终显示在其他窗口之上，取消 = 可被其他窗口遮挡                                    |
| 不移动          | 勾选 = 只播放原地动画（待机/转向/随机动作），不再自动走动；取消 = 恢复正常模式            |
| 开机自启        | 勾选 = 随 Windows 登录自动启动（见下方说明）                                              |
| 大小            | 4 档缩放：320px / 462px / 544px / 640px（默认 462px）                                     |
| 退出            | 关闭桌宠                                                                                  |

### 系统托盘图标

| 操作                     | 功能                     |
| ------------------------ | ------------------------ |
| 双击托盘图标             | 显示 / 隐藏宠物          |
| 右键托盘图标 → 显示/隐藏 | 同上                     |
| 右键托盘图标 → 切换角色  | 热切换当前形象           |
| 右键托盘图标 → 开机自启  | 与右键菜单的开机自启同步 |
| 右键托盘图标 → 退出      | 关闭桌宠                 |

### 自动行为（无需任何操作）

桌宠会自己"生活"，挂机即可观赏：

- **动画链**：每个动画播完按概率抽下一个 —— 30% 待机 / 10% 转向 / 40% 随机动作 / 20% 移动，永不停止
- **屏幕漫游**：朝面向方向行走，先检查屏幕空间、不走出屏幕
- **朝向翻转**：播完「东张西望」后左右翻转朝向，所有动画随之镜像
- **状态记忆**：位置、朝向、大小、置顶、不移动、开机自启、当前角色均自动保存（`%APPDATA%/dsh-pet-standalone/config.json`），下次启动自动恢复

### 开机自启

勾选「开机自启」后，桌宠随系统登录自动启动；取消勾选即移除。

- **Windows**：写入注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`（无需管理员权限）；
  源码运行时指向 `pythonw -m pet`；打包成 exe 后会先用 `start /D` 切到 exe 所在目录再启动 exe，
  避免开机时默认工作目录不可写导致 onefile 解压失败（旧版自启命令会在下次启动时自动升级）
- **macOS**：写入 LaunchAgents（`~/Library/LaunchAgents/`），登录后自动启动

## 实现方式

### 技术栈

- **Python 3.10+ / PySide6**（Qt for Python，LGPL 许可）
- **imageio-ffmpeg** 运行时解码 640×360 透明 webm（VP9 alpha，RGBA 帧）

### 动画链状态机（1:1 移植原插件 `client.js`）

原插件是一个"链式"状态机：每个动画一次性播放，播完按概率抽下一个
（30% 待机 / 10% 转向 / 40% 动作 / 20% 移动）。本项目的 `pet/window.py`
将这套行为完整移植为 Python：

- `_pick_next()` 按概率抽下一个动画
- 转向动画播完翻转朝向（`facing=right` 时水平镜像，等效原版 `scaleX(-1)`）
- 移动动画只提供"走路姿态"，位置由 `QTimer` 驱动：开头/结尾各 2s 原地不动，
  中间按播放进度插值位移
- 点击回应/拖拽动画播完先回"待机缓冲"，待机播完再回到随机链
- 点击只在待机时响应，5px 阈值区分点击与拖拽（等效原版命中层设计）

### 透明窗口与鼠标穿透

窗口用 Qt 的 `Tool` 无边框置顶窗口 + `WA_TranslucentBackground` 实现透明背景。
每帧按人物 alpha 通道生成 `QBitmap` mask，透明区域鼠标直接穿透到下层窗口，
实现"只有宠物本体可点击"（等效原版 HIT_BOX 命中层）。

### 素材播放（webm 主路线）

本项目直接运行时解码上游 640×360 透明 **webm**（VP9 + 8-bit alpha），
使用 `imageio-ffmpeg` 自带的静态 ffmpeg 输出 RGBA 帧，保留半透明边缘。

关键实现：

- 解码命令核心参数：
  ```python
  imageio_ffmpeg.read_frames(
      path,
      pix_fmt="rgba",
      bits_per_pixel=32,
      input_params=["-c:v", "libvpx-vp9"],
  )
  ```
  `-c:v libvpx-vp9` 必须放在输入之前，否则原生 vp9 解码器会丢弃 alpha。
- 播放架构：
  - 后台 reader 线程只负责把 RGBA 帧放入有界队列。
  - 主线程 `QTimer` 按视频 fps 逐帧从队列取帧。
  - 每次只取最早的一帧，**不跳帧、不追帧**，避免动画快进。
  - 所有 `QImage/QPixmap` 和窗口 mask 更新都在主线程完成。
  - Windows 下 `imageio-ffmpeg` 内部使用 `STARTUPINFO` 隐藏 ffmpeg 控制台窗口，
    避免旧 ffmpeg 子进程方案导致的“窗口反复出现/消失”。

### 多形象支持

项目支持多角色形象，每个角色一个独立目录：

```text
assets/characters/<character_id>/videos/
├── idle/     # 待机
├── turn/     # 转向
├── move/     # 移动
├── click/    # 点击回应
├── drag/     # 拖拽（可选）
└── random/   # 随机动作
```

当前内置角色：

```text
shenshen（内置） + 用户通过外部目录添加的角色
```

- 默认形象为 `shenshen`，当前动画放在 `assets/characters/shenshen/videos/`。
- 不同角色可以有**不同的动作集**：程序会递归扫描 `videos/` 下的子目录，
  按目录自动区分“待机 / 转向 / 移动 / 点击回应 / 拖拽 / 随机动作”。
- 内置角色会随 exe 一起打包。
- 同时支持用户本地外部扩展：
  - exe 同目录或当前工作目录下的 `characters/<id>/videos/`
  - 用户数据目录下的 `dsh-pet-standalone/characters/<id>/videos/`
  - 运行时自动检测，存在则优先使用，不存在则回退内置，不会报错。
- 右键桌宠或托盘菜单中的「切换角色」可热切换形象。
- 右键菜单中「动画 · 待机」和「动画 · 转向」已拆分为两个独立按钮。

#### 动作分类规则

程序按以下优先级区分“待机 / 转向 / 移动 / 点击回应 / 拖拽 / 随机动作”：

1. **优先按 `videos/` 下的子目录分类**：
   - `idle/` → 待机
   - `turn/` → 转向
   - `move/` → 移动
   - `click/` → 点击回应
   - `drag/` → 拖拽（可选）
   - `random/` → 随机动作
   - 放在这些子目录之外的 webm 会进入随机动作池。
   - 兼容旧结构：如果存在 `idle_turn/`，程序仍会尝试按文件名关键词拆分待机和转向。

2. **可选 `manifest.json` 补充/覆盖**：
   - 查找位置：
     - `<角色目录>/videos/manifest.json`
     - `<角色目录>/manifest.json`
   - 示例：
     ```json
     {
       "idle": "我的待机.webm",
       "turn": "转身动画.webm",
       "moves": ["走路1.webm", "走路2.webm"],
       "clicks": ["点击回应.webm"],
       "drag": "拖拽动画.webm"
     }
     ```
   - 当子目录无法满足精确分类时，可以用 manifest 指定。

3. **关键词兜底**：
   - 待机：包含 `待机`、`idle`、`呼吸`
   - 转向：包含 `转向`、`转身`、`东张西望`、`回头`、`turn`
   - 移动：包含 `走`、`跑`、`移动`、`move`、`walk`、`run`、`踏步`、`奔跑`
   - 点击回应：包含 `点击`、`回应`、`click`、`response`
   - 拖拽：包含 `拖拽`、`拖`、`悬空`、`drag`、`抓`

4. 如果某个角色没有可用的“待机”，程序会安全回退到该角色第一个动画，避免启动崩溃。

> 建议：新角色推荐直接使用 `idle/ turn/ move/ click/ drag/ random/` 子目录结构，
> 这样不需要 manifest 也能正确分类。

## 快速开始

### 1. 安装依赖

```sh
pip install PySide6 imageio-ffmpeg
```

### 2. 准备素材

请从上游 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 仓库获取
`dsh-pet/assets/thumb/*.webm`（51 个 640×360 透明 webm），按分类放到本项目的
`assets/characters/shenshen/videos/` 下对应子目录：

```text
assets/characters/shenshen/videos/
├── idle/
├── turn/
├── move/
├── click/
├── drag/
└── random/
```

无需转码即可直接运行。

### 3. 运行

双击 `run.bat`，或命令行 `python -m pet`。

## 打包为 exe（可选）

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx ^
    --name dsh-pet-standalone-webm ^
    --collect-all imageio_ffmpeg ^
    --add-data "assets/characters;assets/characters" ^
    packaging/pet_entry.py
```

> 打包入口必须用 `packaging/pet_entry.py`（绝对导入）；直接用 `pet/__main__.py`
> 会因相对导入在冻结模式下失效。onefile 模式启动时会先解压素材（约 5~15 秒）。
>
> `--runtime-tmpdir "."` 是按“进程当前工作目录”解析的，不是 exe 所在目录。
> 程序内部的开机自启会先切到 exe 所在目录，因此解压目录会生成在 exe 同目录；
> 若 exe 位于系统保护目录（如 `Program Files`）且无写权限，请把 exe 放到用户可写目录。

## macOS

### 安装包（.app，GitHub Actions 自动构建）

无需本地 Mac，项目通过 GitHub Actions 自动打包 macOS 版本：

1. 打开仓库的 **Actions** 页 → 左侧选「Build macOS App」→ 右侧 **Run workflow** → 确认
2. 等待构建完成（约 10 分钟），在构建详情页底部下载 artifact：
   `dsh-pet-indesktop-macos-arm64.zip`（Apple Silicon / M 系列芯片）
3. 解压得到 `dsh-pet-indesktop.app`，拖入「应用程序」文件夹即可

> **Intel Mac 用户**：当前仅提供 Apple Silicon（arm64）安装包，Intel 芯片的 Mac
> 请用下方「源码运行」方式使用。

> **未签名提示**：目前为免费版（ad-hoc 签名，未经 Apple 公证），首次打开会被 macOS
> Gatekeeper 拦截。放行方法（任选其一，`<你的app路径>` 改成实际位置）：
>
> 1. 右键 app →「打开」→ 再点「打开」；若无「打开」选项，走第 2 条
> 2. 系统设置 → 隐私与安全性 → 下滑找到「已阻止 'dsh-pet-indesktop'」→ 点「仍然打开」
> 3. 终端清除隔离标记后双击（最常用）：
>    ```sh
>    sudo xattr -d com.apple.quarantine "<你的app路径>/dsh-pet-indesktop.app"
>    ```
> 4. 若第 3 条仍被拦，再补 ad-hoc 签名后双击：
>    ```sh
>    xattr -cr "<你的app路径>/dsh-pet-indesktop.app"
>    codesign --force --deep --sign - "<你的app路径>/dsh-pet-indesktop.app"
>    ```

### 源码运行

```sh
pip install PySide6 imageio-ffmpeg
# 准备素材：同「快速开始」第 2 步，把 webm 放到 assets/characters/shenshen/videos/
python -m pet
```

### macOS 已知差异

- **开机自启**：macOS 通过 LaunchAgents（`~/Library/LaunchAgents/`）实现，与 Windows 注册表等价
- **透明穿透**：Qt 的窗口 mask 鼠标穿透在 macOS 上行为与 Windows 有差异，透明区域点击穿透**可能不完全生效**，需真机验证反馈
- **配置目录**：macOS 下配置保存在 `~/Library/Application Support/dsh-pet-standalone/`

## 目录结构

```
├── pet/                 # 核心代码
│   ├── catalog.py       # 动画目录、多形象常量、分类、几何/概率常量
│   ├── library.py       # 素材库：按角色加载 webm
│   ├── webm_clip.py     # imageio-ffmpeg 解码 webm 的播放器
│   ├── window.py        # 桌宠窗口：状态机 + 动画链 + 移动驱动 + 交互
│   ├── config.py        # 配置持久化（跨平台：APPDATA / Application Support / .config）
│   ├── autostart.py     # 开机自启（跨平台：Windows 注册表 / macOS LaunchAgents）
│   └── app.py           # 入口 + 系统托盘
├── assets/characters/   # 多形象动画（每个角色一个子目录）
│   └── <character_id>/videos/*.webm
├── packaging/           # PyInstaller 打包入口
├── .github/workflows/   # GitHub Actions（macOS 自动打包）
├── tests/               # 冒烟测试 / 诊断工具
├── run.bat              # Windows 一键启动
└── requirements.txt     # PySide6 + imageio-ffmpeg
```

## 已知说明

**webm 直解**：与 web 端一致播放 640×360 透明 webm（VP9 视频，8-bit alpha），
保留半透明边缘和原始色彩。不再打包体积庞大的 GIF 素材。

## 开发经验与教训

> 记录本项目开发与打包过程中踩过的坑，供后续维护者参考。

### 打包与分发

- **CI 打包成功 ≠ 能运行**：macOS 的 GitHub Actions 构建曾"绿色成功"，但产物缺 `pet`
  模块（运行时才报 `ModuleNotFoundError`）。原因：`pyinstaller` 命令不会把当前目录加进
  模块搜索路径，从 `packaging/pet_entry.py` 入口分析时找不到项目根的 `pet` 包。必须用
  `python -m PyInstaller --paths .`。教训：打包后在 CI 里加验证步骤（检查 warn 文件 /
  解压检查权限），别只看绿灯。
- **zip 会丢 macOS 可执行权限**：`zip -r` 打包 .app 后解压，二进制丢失 +x 权限，双击
  无反应、终端 `permission denied`。改用 macOS 原生 `ditto -c -k --keepParent` 打包。
- **未签名 app 必被 Gatekeeper 拦**：免费版加 ad-hoc 签名
  （`codesign --force --deep --sign -`）后，「右键打开 / 系统设置放行」可用；彻底免
  拦截需 Apple 开发者账号公证（$99/年）。放行方法见上文「macOS」章节。
- **打包前先关掉正在运行的桌宠进程**：Windows 打包时若旧 exe 进程存活，PyInstaller
  覆盖产物会报 `PermissionError: 拒绝访问`；webm 版 exe 约 110MB，但仍需结束进程后重试。

### macOS 平台特性

- **Tool 窗口置顶用 `WA_MacAlwaysShowToolWindow`**：macOS 上 Qt 的
  `WindowStaysOnTopHint` 对 `Tool` 窗口不可靠（Qt 官方已知问题 QTBUG-38580），正确
  做法是设置 `Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow`；原生 `NSWindow.setLevel`
  需等窗口重建完成后（`QTimer.singleShot(0)`）再调，否则被 Qt 覆盖。
- **ctypes 调 ObjC 必须显式声明 restype**：`sel_registerName` 返回 64 位 SEL 指针，
  不设 `restype = c_void_p` 会被 ctypes 默认按 32 位截断，损坏的 SEL 使 ObjC runtime
  段错误（SIGSEGV，try/except 拦不住）。任何返回指针的 C 函数都要显式声明返回类型。
- **屏幕坐标比例要减 `availableGeometry` 的 left/top**：macOS 上
  `availableGeometry().top()` 等于菜单栏高度（≠0），按「窗口坐标 ÷ 可用区宽高」存比例
  会偏一个菜单栏高度；正确做法是 `(坐标 - avail.left()/top()) / avail.width()/height()`。

### 素材与播放

- **ffmpeg 解码透明 webm 时 `-c:v libvpx-vp9` 必须放在 `-i` 之前**：ffmpeg 原生 vp9
  解码器会丢弃 WebM alpha 通道（上游 DESIGN.md 踩坑记录第 3 条，已实测复现）。
- **webm 播放不能“清空队列只取最新帧”**：如果每次刷新都丢弃中间帧，动画会像快进一样。
  正确做法是按视频 fps 逐帧取最早的一帧。
- **播放结束标记不能误停新动画**：最后一帧触发窗口层切换动画后，旧的结束标记不应再
  停止新动画的定时器；否则会出现“播完一个动画后卡住不动”。
- **Windows 下 ffmpeg 子进程要隐藏控制台**：使用 `imageio_ffmpeg` 自带的
  `STARTUPINFO` 或显式 `CREATE_NO_WINDOW`，避免窗口反复出现/消失。

### 验证

- **真机验证不可替代**：macOS 专属代码路径（ctypes/ObjC、窗口置顶）在 Windows 上编译
  与冒烟测试都覆盖不到，必须真机验证；诊断日志（恢复位置/回到右下角时记录
  availableGeometry 与 DPR）就是为此加的。

## 附录：旧版 GIF/QMovie 路线（已归档）

> 当前版本已改为 **webm 直解路线**。以下为旧版 GIF/QMovie 路线的完整说明，仅作历史存档，不再使用。

<details>
<summary>点击展开查看旧版 GIF 版本说明</summary>

### 已移除/变更记录

- 内置角色 `guga`、`dada`、`suansuan`、`dudu`、`mimi` 已移除（动画未能正常绘制）。当前内置角色仅保留 `shenshen`。
- 自定义角色仍可通过 exe 同目录或用户数据目录下的 `characters/<id>/videos/` 添加，并在「切换角色」菜单中热切换。

---

# dsh-pet-indesktop

## 下载

无需从源码构建，直接到 [Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)
页面下载对应系统的安装包：

| 你的系统                        | 安装包                              | 说明                                                                  |
| ------------------------------- | ----------------------------------- | --------------------------------------------------------------------- |
| Windows                         | `dsh-pet-standalone.exe`            | 双击即跑，首次启动解压需几秒                                          |
| macOS（Apple Silicon / M 系列） | `dsh-pet-indesktop-macos-arm64.zip` | 解压得 `dsh-pet-indesktop.app`，首次打开需放行（见下方「macOS」章节） |
| macOS（Intel）                  | —                                   | 暂无安装包，请按「macOS」章节源码运行                                 |

> 文件名以 Release 页面实际发布为准。

> **声明与致谢**：本项目改自、源于 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。
> 桌宠的动画素材、动画链行为模型、交互设计均来自原项目，特此声明并感谢原作者的贡献。

把 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 插件里的桌宠，改造成一个
**跨平台的独立桌面宠物**软件（支持 **Windows** 与 **macOS**）—— 不依赖 DSH 运行时，
用 Python + PySide6 实现，双击即跑，复用原项目 51 段高清动画（640×360，24fps）。

Windows 用户见下方「快速开始 / 打包为 exe」，macOS 用户见「macOS」章节。

## 特性

- **webm 高清播放**：直接运行时解码 640×360 透明 webm（VP9 + 8-bit alpha），保留半透明边缘
- **动画链**：每个动画播完按概率选下一个 —— 30% 待机 / 10% 转向 / 40% 随机动作 / 20% 移动，永不停止
- **多形象支持**：内置多个角色，并支持用户通过外部目录添加自定义角色
- **角色热切换**：右键桌宠或托盘菜单可随时切换形象，无需重启
- **屏幕漫游**：朝面向方向行走，先检查屏幕空间、不走出屏幕（移动动画前后各 2s 准备/收尾，位置由代码驱动）
- **左右朝向**：转向动画播完翻转朝向，所有动画支持水平镜像
- **点击回应**：点击宠物随机播放当前角色配置的回应动画（链上非待机动画播放中不打断）
- **拖拽**：按住拖动超过 5px 判定为拖拽，宠物播放"悬空反馈"动画跟手，松手停在原地
- **透明穿透**：窗口逐帧按人物 alpha 生成 mask，透明区域鼠标直接穿透到下层窗口
- **右键菜单**：手动播放待机/转向/移动/点击回应/随机动作、切换角色、回到右下角、窗口置顶、不移动、开机自启、4 档大小、退出
- **系统托盘**：显示/隐藏、切换角色、开机自启、退出；位置/朝向/大小/置顶自动持久化
- **开机自启**：Windows 写 HKCU 注册表 Run 键 / macOS 写 LaunchAgents（均无需管理员权限），可随时开关

## 使用手册

### 启动与退出

- **启动**：双击 `run.bat`（或 `python -m pet` / 打包后的 exe），桌宠出现在屏幕右下角
- **退出**：右键桌宠 →「退出」，或点系统托盘图标 →「退出」

### 鼠标交互

- **点击**：单击宠物本体，随机播放当前角色配置的回应动画之一。
  链上非待机动画播放中点击不会打断
- **拖拽**：按住拖动超过 5px 判定为拖拽，宠物播放「悬空反馈」动画跟手，松手停在原地
- **穿透**：只有宠物本体（不透明区域）可点，其余透明区域鼠标直接穿透到下层窗口

### 右键菜单（右键点击宠物本体）

| 菜单项          | 功能                                                                                      |
| --------------- | ----------------------------------------------------------------------------------------- |
| 动画 · 待机     | 手动播放待机动画；如果待机目录有多个视频，会显示二级菜单                                  |
| 动画 · 转向     | 手动播放转向动画；如果转向目录有多个视频，会显示二级菜单                                  |
| 动画 · 移动     | 手动播放移动动画（走路姿态 + 朝面向方向真实走动；「不移动」模式下这是唯一触发移动的方式） |
| 动画 · 点击回应 | 手动播放点击回应动画                                                                      |
| 动画 · 随机动作 | 手动播放随机动作动画                                                                      |
| 切换角色        | 热切换当前形象（内置 + 外部扩展角色都会列出）                                             |
| 回到右下角      | 把宠物复位到屏幕右下角                                                                    |
| 窗口置顶        | 勾选 = 始终显示在其他窗口之上，取消 = 可被其他窗口遮挡                                    |
| 不移动          | 勾选 = 只播放原地动画（待机/转向/随机动作），不再自动走动；取消 = 恢复正常模式            |
| 开机自启        | 勾选 = 随 Windows 登录自动启动（见下方说明）                                              |
| 大小            | 4 档缩放：320px / 462px / 544px / 640px（默认 462px）                                     |
| 退出            | 关闭桌宠                                                                                  |

### 系统托盘图标

| 操作                     | 功能                     |
| ------------------------ | ------------------------ |
| 双击托盘图标             | 显示 / 隐藏宠物          |
| 右键托盘图标 → 显示/隐藏 | 同上                     |
| 右键托盘图标 → 切换角色  | 热切换当前形象           |
| 右键托盘图标 → 开机自启  | 与右键菜单的开机自启同步 |
| 右键托盘图标 → 退出      | 关闭桌宠                 |

### 自动行为（无需任何操作）

桌宠会自己"生活"，挂机即可观赏：

- **动画链**：每个动画播完按概率抽下一个 —— 30% 待机 / 10% 转向 / 40% 随机动作 / 20% 移动，永不停止
- **屏幕漫游**：朝面向方向行走，先检查屏幕空间、不走出屏幕
- **朝向翻转**：播完「东张西望」后左右翻转朝向，所有动画随之镜像
  - **状态记忆**：位置、朝向、大小、置顶、不移动、开机自启、当前角色均自动保存（`%APPDATA%/dsh-pet-standalone/config.json`），下次启动自动恢复

### 开机自启

勾选「开机自启」后，桌宠随系统登录自动启动；取消勾选即移除。

- **Windows**：写入注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`（无需管理员权限）；
  源码运行时指向 `pythonw -m pet`；打包成 exe 后会先用 `start /D` 切到 exe 所在目录再启动 exe，
  避免开机时默认工作目录不可写导致 onefile 解压失败（旧版自启命令会在下次启动时自动升级）
- **macOS**：写入 LaunchAgents（`~/Library/LaunchAgents/`），登录后自动启动

## 实现方式

### 技术栈

- **Python 3.10+ / PySide6**（Qt for Python，LGPL 许可）
- **QMovie** 播放 GIF，运行时零外部依赖（无需 ffmpeg）

### 动画链状态机（1:1 移植原插件 `client.js`）

原插件是一个"链式"状态机：每个动画一次性播放，播完按概率抽下一个
（30% 待机 / 10% 转向 / 40% 动作 / 20% 移动）。本项目的 `pet/window.py`
将这套行为完整移植为 Python：

- `_pick_next()` 按概率抽下一个动画
- 转向动画播完翻转朝向（`facing=right` 时水平镜像，等效原版 `scaleX(-1)`）
- 移动动画只提供"走路姿态"，位置由 `QTimer` 驱动：开头/结尾各 2s 原地不动，
  中间按播放进度插值位移
- 点击回应/拖拽动画播完先回"待机缓冲"，待机播完再回到随机链
- 点击只在待机时响应，5px 阈值区分点击与拖拽（等效原版命中层设计）

### 透明窗口与鼠标穿透

窗口用 Qt 的 `Tool` 无边框置顶窗口 + `WA_TranslucentBackground` 实现透明背景。
每帧按人物 alpha 通道生成 `QBitmap` mask，透明区域鼠标直接穿透到下层窗口，
实现"只有宠物本体可点击"（等效原版 HIT_BOX 命中层）。

### 素材转码（webm → GIF）

原项目的高清资源是 640×360 透明 **webm**（VP9 + 8-bit alpha）。为了让桌宠
运行时零依赖，本项目把 webm 一次性预转码为同分辨率的透明 **GIF**，用 QMovie 播放。
`scripts/convert.py` 的关键点是 `-c:v libvpx-vp9` 必须放在 `-i` 之前 ——
ffmpeg 原生 vp9 解码器会丢弃 alpha（原项目 DESIGN.md 踩坑记录第 3 条，已实测复现）。

## 快速开始

### 1. 安装依赖

```sh
pip install PySide6
```

### 2. 准备素材

素材体积较大（GIF 392MB，未随仓库分发）。请从上游
[dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 仓库获取
`dsh-pet/assets/thumb/*.webm`（51 个 640×360 透明 webm），放到本项目的
`assets/videos/` 目录，然后转码：

```sh
pip install imageio-ffmpeg pillow
python scripts/convert.py            # 默认读 assets/videos/，输出 assets/animations/
# 或指定源目录：python scripts/convert.py --src <你的webm目录>
```

### 3. 运行

双击 `run.bat`，或命令行 `python -m pet`。

## 打包为 exe（可选）

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name dsh-pet-indesktop ^
    --add-data "assets/animations;assets/animations" ^
    packaging/pet_entry.py
```

> 打包入口必须用 `packaging/pet_entry.py`（绝对导入）；直接用 `pet/__main__.py`
> 会因相对导入在冻结模式下失效。onefile 模式启动时会先解压素材（约 5~15 秒）。
>
> `--runtime-tmpdir "."` 是按“进程当前工作目录”解析的，不是 exe 所在目录。
> 程序内部的开机自启会先切到 exe 所在目录，因此解压目录会生成在 exe 同目录；
> 若 exe 位于系统保护目录（如 `Program Files`）且无写权限，请把 exe 放到用户可写目录。

## macOS

### 安装包（.app，GitHub Actions 自动构建）

无需本地 Mac，项目通过 GitHub Actions 自动打包 macOS 版本：

1. 打开仓库的 **Actions** 页 → 左侧选「Build macOS App」→ 右侧 **Run workflow** → 确认
2. 等待构建完成（约 10 分钟），在构建详情页底部下载 artifact：
   `dsh-pet-indesktop-macos-arm64.zip`（Apple Silicon / M 系列芯片）
3. 解压得到 `dsh-pet-indesktop.app`，拖入「应用程序」文件夹即可

> **Intel Mac 用户**：当前仅提供 Apple Silicon（arm64）安装包，Intel 芯片的 Mac
> 请用下方「源码运行」方式使用。

> **未签名提示**：目前为免费版（ad-hoc 签名，未经 Apple 公证），首次打开会被 macOS
> Gatekeeper 拦截。放行方法（任选其一，`<你的app路径>` 改成实际位置）：
>
> 1. 右键 app →「打开」→ 再点「打开」；若无「打开」选项，走第 2 条
> 2. 系统设置 → 隐私与安全性 → 下滑找到「已阻止 'dsh-pet-indesktop'」→ 点「仍然打开」
> 3. 终端清除隔离标记后双击（最常用）：
>    ```sh
>    sudo xattr -d com.apple.quarantine "<你的app路径>/dsh-pet-indesktop.app"
>    ```
> 4. 若第 3 条仍被拦，再补 ad-hoc 签名后双击：
>    ```sh
>    xattr -cr "<你的app路径>/dsh-pet-indesktop.app"
>    codesign --force --deep --sign - "<你的app路径>/dsh-pet-indesktop.app"
>    ```

### 源码运行

```sh
pip install PySide6
# 准备素材：同「快速开始」第 2 步，把 webm 放到 assets/videos/ 后转码
pip install imageio-ffmpeg pillow
python scripts/convert.py
python -m pet
```

### macOS 已知差异

- **开机自启**：macOS 通过 LaunchAgents（`~/Library/LaunchAgents/`）实现，与 Windows 注册表等价
- **透明穿透**：Qt 的窗口 mask 鼠标穿透在 macOS 上行为与 Windows 有差异，透明区域点击穿透**可能不完全生效**，需真机验证反馈
- **配置目录**：macOS 下配置保存在 `~/Library/Application Support/dsh-pet-standalone/`

## 目录结构

```
├── pet/                 # 核心代码
│   ├── catalog.py       # 51 段动画目录、分类、几何/概率常量
│   ├── library.py       # QMovie 素材库（速度补偿）
│   ├── window.py        # 桌宠窗口：状态机 + 动画链 + 移动驱动 + 交互
│   ├── config.py        # 配置持久化（跨平台：APPDATA / Application Support / .config）
│   ├── autostart.py     # 开机自启（跨平台：Windows 注册表 / macOS LaunchAgents）
│   └── app.py           # 入口 + 系统托盘
├── scripts/convert.py   # 素材转码：webm → 640×360 透明 GIF
├── packaging/           # PyInstaller 打包入口
├── .github/workflows/   # GitHub Actions（macOS 自动打包）
├── tests/               # 冒烟测试 / 帧率实测 / 诊断工具
├── run.bat              # Windows 一键启动
└── requirements.txt     # PySide6
```

## 已知说明

**清晰度略糊于 web 端**：web 端直接播放 640×360 透明 webm（VP9 视频，8-bit alpha），
而本项目的 GIF 受格式本身限制 —— ① 只支持 **1-bit alpha**（每像素要么全透明要么
全不透明，无半透明过渡，发丝边缘略硬）；② 最多 **256 色调色板**（有损颜色量化）。
分辨率与帧率与 web 端一致（640×360 / 24fps），但颜色与边缘过渡略逊，属 GIF 格式的
固有限制。若追求与 web 端完全一致的画质，可改用运行时 ffmpeg 解码 webm 的方案。

## 开发经验与教训

> 记录本项目开发与打包过程中踩过的坑，供后续维护者参考。

### 打包与分发

- **CI 打包成功 ≠ 能运行**：macOS 的 GitHub Actions 构建曾"绿色成功"，但产物缺 `pet`
  模块（运行时才报 `ModuleNotFoundError`）。原因：`pyinstaller` 命令不会把当前目录加进
  模块搜索路径，从 `packaging/pet_entry.py` 入口分析时找不到项目根的 `pet` 包。必须用
  `python -m PyInstaller --paths .`。教训：打包后在 CI 里加验证步骤（检查 warn 文件 /
  解压检查权限），别只看绿灯。
- **zip 会丢 macOS 可执行权限**：`zip -r` 打包 .app 后解压，二进制丢失 +x 权限，双击
  无反应、终端 `permission denied`。改用 macOS 原生 `ditto -c -k --keepParent` 打包。
- **未签名 app 必被 Gatekeeper 拦**：免费版加 ad-hoc 签名
  （`codesign --force --deep --sign -`）后，「右键打开 / 系统设置放行」可用；彻底免
  拦截需 Apple 开发者账号公证（$99/年）。放行方法见上文「macOS」章节。
- **打包前先关掉正在运行的桌宠进程**：Windows 打包时若旧 exe 进程存活（或杀毒软件
  正在扫描 400MB 大文件），PyInstaller 覆盖产物会报 `PermissionError: 拒绝访问`，
  需结束进程并等扫描结束后重试。

### macOS 平台特性

- **Tool 窗口置顶用 `WA_MacAlwaysShowToolWindow`**：macOS 上 Qt 的
  `WindowStaysOnTopHint` 对 `Tool` 窗口不可靠（Qt 官方已知问题 QTBUG-38580），正确
  做法是设置 `Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow`；原生 `NSWindow.setLevel`
  需等窗口重建完成后（`QTimer.singleShot(0)`）再调，否则被 Qt 覆盖。
- **ctypes 调 ObjC 必须显式声明 restype**：`sel_registerName` 返回 64 位 SEL 指针，
  不设 `restype = c_void_p` 会被 ctypes 默认按 32 位截断，损坏的 SEL 使 ObjC runtime
  段错误（SIGSEGV，try/except 拦不住）。任何返回指针的 C 函数都要显式声明返回类型。
- **屏幕坐标比例要减 `availableGeometry` 的 left/top**：macOS 上
  `availableGeometry().top()` 等于菜单栏高度（≠0），按「窗口坐标 ÷ 可用区宽高」存比例
  会偏一个菜单栏高度；正确做法是 `(坐标 - avail.left()/top()) / avail.width()/height()`。

### 素材与播放

- **ffmpeg 转码透明 webm 时 `-c:v libvpx-vp9` 必须放在 `-i` 之前**：ffmpeg 原生 vp9
  解码器会丢弃 WebM alpha 通道（上游 DESIGN.md 踩坑记录第 3 条，已实测复现）。
- **QMovie 播放 GIF 偏慢约 20%**：QMovie 的定时器 + 解码开销使每帧比 GIF 原生时长慢，
  需 `setSpeed(120)` 校准（见 `pet/library.py` 的 `PLAYBACK_SPEED`）。

### 验证

- **真机验证不可替代**：macOS 专属代码路径（ctypes/ObjC、窗口置顶）在 Windows 上编译
  与冒烟测试都覆盖不到，必须真机验证；诊断日志（恢复位置/回到右下角时记录
  availableGeometry 与 DPR）就是为此加的。

</details>

## 许可与致谢

- 本项目的 Python 代码为独立实现，采用 **MIT** 许可。
- 动画素材版权与许可归属原项目 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（MIT）。
- 再次感谢 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 原作者。
