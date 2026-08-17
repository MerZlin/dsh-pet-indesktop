# dsh-pet-indesktop

> **声明与致谢**：本项目改自、源于 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。
> 桌宠的动画素材、动画链行为模型、交互设计均来自原项目，特此声明并感谢原作者的贡献。

把 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 插件里的桌宠，改造成一个
**独立的 Windows 桌面宠物**软件 —— 不依赖 DSH 运行时，用 Python + PySide6 实现，
双击即跑，复用原项目 51 段高清动画（640×360，24fps）。

## 特性

- **动画链**：每个动画播完按概率选下一个 —— 30% 待机 / 10% 转向 / 40% 随机动作 / 20% 移动，永不停止
- **屏幕漫游**：朝面向方向行走，先检查屏幕空间、不走出屏幕（移动动画前后各 2s 准备/收尾，位置由代码驱动）
- **左右朝向**：东张西望播完翻转朝向，所有动画支持水平镜像
- **点击回应**：点击宠物随机播放 3 种回应动画之一（链上非待机动画播放中不打断）
- **拖拽**：按住拖动超过 5px 判定为拖拽，宠物播放"悬空反馈"动画跟手，松手停在原地
- **透明穿透**：窗口逐帧按人物 alpha 生成 mask，透明区域鼠标直接穿透到下层窗口
- **右键菜单**：手动播放任意动画、回到右下角、窗口置顶、开机自启、4 档大小、退出
- **系统托盘**：显示/隐藏、开机自启、退出；位置/朝向/大小/置顶自动持久化
- **开机自启**：写 HKCU 注册表 Run 键（无需管理员权限），可随时开关

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

## 目录结构

```
├── pet/                 # 核心代码
│   ├── catalog.py       # 51 段动画目录、分类、几何/概率常量
│   ├── library.py       # QMovie 素材库（速度补偿）
│   ├── window.py        # 桌宠窗口：状态机 + 动画链 + 移动驱动 + 交互
│   ├── config.py        # 配置持久化（%APPDATA%/dsh-pet-indesktop/config.json）
│   ├── autostart.py     # 开机自启（HKCU Run 注册表）
│   └── app.py           # 入口 + 系统托盘
├── scripts/convert.py   # 素材转码：webm → 640×360 透明 GIF
├── packaging/           # PyInstaller 打包入口
├── tests/               # 冒烟测试 / 帧率实测 / 诊断工具
├── run.bat              # 一键启动
└── requirements.txt     # PySide6
```

## 已知说明

**清晰度略糊于 web 端**：web 端直接播放 640×360 透明 webm（VP9 视频，8-bit alpha），
而本项目的 GIF 受格式本身限制 —— ① 只支持 **1-bit alpha**（每像素要么全透明要么
全不透明，无半透明过渡，发丝边缘略硬）；② 最多 **256 色调色板**（有损颜色量化）。
分辨率与帧率与 web 端一致（640×360 / 24fps），但颜色与边缘过渡略逊，属 GIF 格式的
固有限制。若追求与 web 端完全一致的画质，可改用运行时 ffmpeg 解码 webm 的方案。

## 许可与致谢

- 本项目的 Python 代码为独立实现，采用 **MIT** 许可。
- 动画素材版权与许可归属原项目 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（MIT）。
- 再次感谢 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 原作者。
