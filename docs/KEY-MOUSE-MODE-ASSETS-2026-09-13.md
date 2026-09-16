# 键鼠跟随模式：素材契约、替换步骤与 GPT 出图提示词

> **素材契约仍然有效，但"存放位置"变了（2026-09-16）**：现在由 BongoCat 自己托管模型
> （用它自带的「导入模型」），不再往 dsh-pet 的数据目录覆盖层里放文件。
> 模式实现见 `docs/EXTERNAL-MODE-2026-09-16.md`；
> 完整的"做自己的角色"流程与提示词见 `docs/KEY-MOUSE-MODE-OWN-CHARACTER-2026-09-15.md`。

- 日期：2026-09-13
- 适用：模式「键鼠跟随」（复用 BongoCat 运行时，见 `docs/KEY-MOUSE-MODE-2026-09-13.md`）
- 校验工具：`python scripts/verify_bongo_assets.py --dir <模型目录>`（`--strict` 把缺件警告升级为失败）
- **要做"自己的角色版本"（用三视图生成素材）请看专文**：
  `docs/KEY-MOUSE-MODE-OWN-CHARACTER-2026-09-15.md`（含可粘贴的 GPT 提示词与两个辅助脚本）

## 1. 素材放在哪

**2026-09-16 起**：模型改由 BongoCat 自己托管（用它自带的「导入模型」），
所以下面的路径只作历史说明——现行做法是把整套模型目录导入 BongoCat。
（历史方案：把素材放进 dsh-pet 数据目录的覆盖层，进入模式前覆盖进运行时副本：）

```text
%APPDATA%\dsh-pet-standalone[-变体]\bongocat\models\
  standard\      ← 默认模型（猫 + 键盘）
  keyboard\      ← 键盘版预置模型（可选替换）
  gamepad\       ← 手柄版预置模型（可选替换）
```

例如只替换默认模型的背景：`...\bongocat\models\standard\resources\background.png`。
模式切换时会自动同步；若正在键鼠跟随模式中，退出再进入一次即可看到新素材。

## 2. 素材契约表

单个模型目录（以 `standard` 为例）的结构与要求：

| 文件 | 必需 | 尺寸/格式 | 说明 |
| --- | --- | --- | --- |
| `cat.model3.json` | 是 | JSON | Live2D 模型清单；替换贴图时**不要**改文件名与引用 |
| `demomodel.moc3` | 是 | 二进制 | Cubism 模型数据；只有重做绑定才会变 |
| `demomodel.1024/texture_00.png` `texture_01.png` `texture_02.png` | 是 | 各 **1024×512**，RGBA | 模型贴图。目录名里的 `.1024` 是**宽度**约定（上游三套预置模型实测均为宽 1024、高 512）；重绘必须保持尺寸与部件布局 |
| `demomodel.cdi3.json`、`live2d_expression*.exp3.json`、`exp_*.exp3.json` | 是 | JSON | 表情/显示信息；一般不动 |
| `live2d_motion1.motion3.json`、`live2d_motion2.motion3.json`（+ `live2d_motion1.flac`） | 是 | JSON/FLAC | 动作；一般不动 |
| `resources/background.png` | 否 | PNG，建议带透明通道 | 桌宠窗口背景图；缺省则无背景 |
| `resources/cover.png` | 否 | PNG | 偏好窗口里的模型封面；缺省用预览 |
| `resources/left-keys/<Key>.png` | 否 | PNG，**同目录同尺寸**，带透明通道 | 左半区按键覆盖图（整窗透明叠加层，只有该键区域有内容） |
| `resources/right-keys/<Key>.png` | 否 | PNG，同目录同尺寸 | 右半区按键覆盖图 |

键名清单（缺哪个键就只是不显示那个键的覆盖图，不是错误）：

- `left-keys`：`Alt` `AltGr` `BackQuote` `Backspace` `CapsLock` `Control` `ControlLeft`
  `ControlRight` `Delete` `Escape` `Fn` `KeyA`…`KeyZ` `Meta` `Num0`…`Num9` `Return`
  `Shift` `ShiftLeft` `ShiftRight` `Slash` `Space` `Tab`
- `right-keys`（键盘模型）：`DownArrow` `LeftArrow` `RightArrow` `UpArrow`
  —— **`standard` 预置模型没有这个目录**，属正常状态（`--strict` 才会把它报成失败）。
- 手柄模型（`gamepad`）另一套键名：左 `DPadUp` `DPadDown` `DPadLeft` `DPadRight`
  `LeftTrigger` `LeftTrigger2`；右 `North` `South` `East` `West` `RightTrigger` `RightTrigger2`。

## 3. 三种替换路线（按投入从低到高）

### 路线 A：只换背景 / 封面（10 分钟，零风险）

做两张图覆盖 `resources/background.png` 与 `resources/cover.png` 即可，
猫本体完全不变。想更干净可以同时只做少数几个键的覆盖图（例如 `Space`、`KeyA`）。

### 路线 B：按键覆盖图（推荐主用，改风格不改角色）

覆盖图是**整窗大小的透明 PNG**，每个键一张，只有该键按下的高亮区域有像素。
GPT 一次生成 50 多张逐键图并不现实，可行做法是：

1. 用提示词 P1 生成一张「键盘 + 爪印」的整窗透明美术图；
2. 在任意绘图工具（Krita / Photoshop / Photopea 均可）里复制该图，逐个键只保留
   需要高亮的区域，其余擦成全透明，按上表键名导出 PNG；
3. 全部导出后跑一次 `python scripts/verify_bongo_assets.py --dir <模型目录> --strict`，
   它会检查同目录尺寸一致、透明通道与缺件。

只想快速见效时，先做 `Space` / `Enter`（`Return`）/ `KeyA` / `KeyS` 四个就够了。

### 路线 C：重绘模型贴图（换角色形象，需要耐心）

把 `demomodel.1024/texture_00..02.png` 当作画布做 **img2img 重绘**（提示词 P4）：
保持 1024×512（宽×高）、部件位置与透明区域不变，只改画风/配色/花纹。**不要**移动部件位置，
否则网格 UV 会错位、模型会撕裂。重绘后逐张替换并跑校验脚本。

### 路线 D（进阶）：完整自制模型

要换的是「形状/结构」而不只是画风，就必须在 Live2D Cubism Editor 里重新绑定并导出
`moc3`，且参数名要与原版对齐，否则键鼠跟随会失效：

| 参数 | 用途 |
| --- | --- |
| `CatParamLeftHandDown` / `CatParamRightHandDown` | 左/右手按下（键盘输入时触发） |
| `ParamMouseLeftDown` / `ParamMouseRightDown` | 鼠标左/右键按下 |
| `ParamMouseX` `ParamMouseY` `ParamAngleX` `ParamAngleY` `ParamAngleZ` `ParamEyeBallX` `ParamEyeBallY` | 鼠标位置跟随（头/眼/身体朝向） |
| 组 `EyeBlink`（`ParamEyeLOpen`、`ParamEyeROpen`） | 自动眨眼 |

导出后与贴图、动作、表情一起放进模型目录，保持 `cat.model3.json` 的文件引用结构即可。

## 4. GPT 出图提示词

以下提示词可直接粘进支持图像生成的 GPT 会话。**统一要求**：输出 PNG、透明背景、
不要水印/文字/签名；如果模型不支持透明背景，就要求「纯品红 #FF00FF 背景，便于抠图」。

### P1 · 按键覆盖图（整窗透明美术层）

```text
请生成一张用于桌面宠物「键盘覆盖层」的透明背景 PNG 插画。

画面内容：一只可爱的猫爪/猫咪前臂，正是打字姿势，视角为正面俯视键盘。
构图为 1024×1024 正方形，主体居中，四周留出 8% 以上透明边距。

美术要求：干净利落的粗轮廓线（约 6px），扁平化赛璐璐上色，两点高光，柔和阴影，
配色以奶白 + 浅灰 + 淡粉肉垫为主，可选一点点薄荷绿点缀。整体风格日系卡通风、
清晰、无渐变噪点，适合小尺寸缩略显示。

硬性要求：
- 纯透明背景（不要棋盘格、不要地面、不要投影到画布边缘）；
- 不要出现任何文字、按键字符、logo、水印或签名；
- 不要画完整的键盘，只画猫爪/前臂，留出后续按网格叠加高亮的空间；
- 输出为正方形 PNG，边缘不可裁切主体。

负面提示：杂乱背景、写实毛发、噪点、渐变网格、多重主体、文字。
```

需要「高亮态」素材时追加一句：

```text
额外生成同一构图、同一姿势的第二张：这只猫爪按下左侧第一个键位，指尖下压、
带一圈淡蓝色高亮光晕（发光强度柔和、不刺眼），其余部分与第一张完全一致。
```

### P2 · 窗口背景 `resources/background.png`

```text
请生成一张桌面宠物窗口背景图，透明背景 PNG，尺寸 1024×576（16:9）。

主题：柔和的深夜书桌氛围，只保留极简元素——左下角一盏微弱的小台灯光晕、
桌面边缘一条细线、顶部几粒星星。整体低饱和度、暗蓝紫主色，方便前方的角色突出。

硬性要求：
- 四周 12% 区域保持完全透明，避开角色主体区域（画面正中）；
- 不要出现文字、人物、动物、logo、水印；
- 明暗对比低，避免干扰前景角色；
- 输出 PNG，不要白色或棋盘格背景。
```

### P3 · 偏好窗口封面 `resources/cover.png`

```text
请生成一张桌面宠物模型的封面缩略图，PNG，1024×1024。

内容：同一只猫咪角色正面坐姿，居中，四周留 10% 安全边距；背景为同色系浅色渐变
圆形色块（不透明），角色本体描边干净、配色与前景模型一致。

风格：扁平化卡通、粗描边、柔和阴影、明亮通透。
硬性要求：不要文字、logo、水印、签名；不要裁切主体；正方形构图。
```

### P4 · 模型贴图重绘（img2img / 局部重绘）

把原贴图 `texture_00.png`（或 `texture_01/02`）作为输入图，用下面这段提示词：

```text
以我提供的这张 Live2D 贴图为底图做重绘，严格保持：
- 画布尺寸 1024×512（宽×高，与输入图完全一致）；
- 每个部件的形状、位置、朝向、面积与透明区域完全不变（不要移动、缩放、增删部件）；
- 部件之间的留白与接缝位置保持不变。

只改变：配色方案（改为奶白 + 浅灰 + 淡粉肉垫，点缀薄荷绿）、线稿粗细与风格
（统一为 5-6px 干净描边）、上色方式（扁平赛璐璐 + 一点高光）。

不要：新增或删减部件、改变部件轮廓、添加文字/logo/水印、添加背景、
改变透明通道的覆盖范围。
```

替换时命名必须一致：`demomodel.1024/texture_00.png`、`texture_01.png`、`texture_02.png`。

### P5 · 动作/表情（可选，需 Cubism Editor 手工绑定）

GPT 只能出**静态拆件图**，`.motion3.json` 与 `.exp3.json` 里的关键帧必须在
Cubism Editor 里做。要做新动作时，用它生成参考帧（起势/中段/收势各一张，透明背景、
同尺寸同构图），再在编辑器里照着摆姿势；参数名沿用第 3 节路线 D 的对照表。

## 5. 替换后自检

```powershell
# 校验覆盖层（推荐加 --strict：缺件也不放过）
python scripts\verify_bongo_assets.py `
  --dir "$env:APPDATA\dsh-pet-standalone\bongocat\models\standard" --strict

# 校验整个运行时副本
python scripts\verify_bongo_assets.py `
  --runtime "$env:APPDATA\dsh-pet-standalone\bongocat\runtime" --strict
```

报告要点：`texture-size-mismatch`（贴图尺寸与目录名约定不符）、
`missing-reference`（model3.json 引用的文件缺失）、`key-overlay-size-inconsistent`
（同目录覆盖图尺寸不一致）、`missing-key-overlay`（缺键，严格模式下失败）。

`--strict` 只用于「你自己要求素材必须齐全」的场景；随包内置素材的 CI 校验用默认
模式（缺件是上游预置素材的正常状态：例如 `standard` 没有 `right-keys`），
只把结构、尺寸、PNG 类错误当门禁。

替换完退出并重新进入一次键鼠跟随模式（或重启 `BongoCat.exe`）即可生效。
