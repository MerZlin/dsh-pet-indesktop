# petcore 角色资源包

> 基线：`feat/petcore-vpet-architecture`，2026-10-20。

> 本文是角色包的**规格**（目录、动画分类、manifest、帧序列）。打包与导入的实际流程——`pack-character` 产出的是目录而不是 zip、三条导入入口、数据目录里该放哪、哪些能力还不支持——见 [`PETCORE-MOD-DEVELOPMENT.md`](PETCORE-MOD-DEVELOPMENT.md) §9。

## 1. 包布局

```
mods/character.shenshen/
  mod.json
  videos/
    manifest.json          可选：name / body_box / head_box
    move_strides.json      可选：每段移动动画的地面位移
    text_clips.json        可选：{"no_mirror": [动画名]}
    idle/    *.webm|*.gif|*.webp
    turn/
    move/
    click/
    drag/
    random/
  frameseq/                可选：预生成的 WebP 帧序列
    <folder>/<stem>.g<源sha12>/{meta.json, f_0001.webp...}
```

旧素材目录 `assets/characters/<id>/videos/` 与上述 `videos/` 同构，由 core 以只读 provider 接入，无需复制。

## 2. 动画分类

| 目录 | GraphType | AnimatType | locate | loop |
|---|---|---|---|---|
| `idle/` | `Idel` | `B_Loop` | ground | 是 |
| `idle_turn/` | `Idel` | `B_Loop` | ground | 是 |
| `turn/` | `Move` | `B_Loop` | in_place | 否 |
| `move/` | `Move` | `B_Loop` | ground | 否 |
| `click/` | `Touch_Body` | `Single` | ground | 否 |
| `drag/` | `Raised_Static` | `Single` | ground | 否 |
| `random/` 与未知目录 | `Default` | `Single` | ground | 否 |

同目录多个文件即多变体，`find_for` 在变体间随机选择；同名动画按 `(graph, animat, mode)` 索引，不按文件路径。

## 3. manifest.json

```json
{
  "name": "Shenshen",
  "body_box": [212, 60, 428, 330],
  "head_box": [185, 60, 436, 195]
}
```

- 画布固定 640×360，脚底 y=330，绘制下移 30px。
- `body_box`/`head_box` 必须在画布范围内且 `x1<x2`、`y1<y2`；非法值忽略。
- 缺 `head_box` 时按 `body_box` 顶部 45% 推导。
- **`body_box` 是运行期定位锚点，不只是素材元数据**：贴边、右下角落位、位置恢复/比例、屏幕热插拔都用 `body_box × scale` 的身体框；未声明时才回退整窗。做包时必须按 alpha≥128 量身体框，否则桌宠会按透明边留出错误间距。
- `head_box` 留给边缘探头（P1-E）：45° 探头用头框决定「只露头/脸」的切面；没有头框时按身体框顶部 45% 推导。
- 身体框用于拖拽/边缘锚点与命中参考；首版命中仍按帧 alpha。

## 4. move_strides.json

```json
{
  "run": 240,
  "walk": {
    "stride": 120,
    "curve": [0, 0.5, 1],
    "segments": {"start": 0, "loop_start": 1, "loop_end": 4, "end": 9}
  }
}
```

- 整数或对象都生效；缺省 120px。
- `stride` 是整条 `curve` 0→1 的水平位移（px，scale=1.0）；`curve` 用于逐帧位置插值。
- `segments` 可选，帧号闭区间；声明「真正可循环的中间段」。素材把绊倒/趴地烤进
  clip 时，把 `loop_end` 放在绊倒前，键盘移动按住只循环中间段，松手从
  `loop_end + 1` 播收尾。缺省时按 curve 自动推导（前导 0 = 前摇，尾部 1 = 收尾）。

## 5. text_clips.json

```json
{"no_mirror": ["typing", "reading"]}
```

列出的动画在向左朝向时不水平镜像。

## 6. WebP 帧序列

- 目录名 `<stem>.g<源 webm sha256 前 12 位>` 即源身份；`meta.json` 记录 `fps`、`frames`、`source_sha256`、`encoder`。
- 采纳条件：目录戳可读、meta 戳一致、meta 帧数等于磁盘帧数、`encoder == unblend-white-v5`（当前档）。
- 任一条件不成立 → 该段回退 WebM，绝不播放旧档或无身份产物。
- 首版只消费帧序列；WebM→WebP 转换与世代退役/GC 仍在旧应用或 `tools/convert_frameseq.py`，P1 再移植。

## 6a. WebP 单文件与精灵表（2026-11-13）

- 单文件 `.webp` 与 `.webm/.gif` 同级：`media/backends` 注册 `webp`，由 `WebPClip`
  用 QMovie 播放；帧延时按 `nextFrameDelay()` 累计，不再套 GIF 的固定 42ms。
- 精灵表推荐写 `videos/manifest.json` 的 `clips`：
  `{"clips": {"走路": {"sheet": "move/走路.webp", "frame_w": 640, "frame_h": 360,
  "columns": 8, "rows": 15, "frame_count": 120, "fps": 12, "folder": "move"}}}`
- 也支持 aboutme-pets 的同名 `.json` 边车（`FrameWidth/FrameHeight/Columns/Rows/
  FrameCount/SampleFps`）；没有边车的 `.webp` 当单文件动画。
- `SheetClip` 一次解码整张图（超过 16M 像素等比缩小），按网格切帧 + 4 帧缓存；
  **不要**用 `QImageReader.jumpToImage`（WebP handler 返回 False）。
- aboutme-pets 单角色目录可用 `petcore/content/sprite_pack.py` 导入；应用内下载索引
  在 `petcore/content/data/aboutme_packs.json`，只含许可文件齐全的 `dsh`/`dsh1`，
  逐文件 sha256 校验，不随安装包分发。设置 → 角色素材包另有「素材来源」按钮打开
  `https://github.com/hunmer/aboutme-pets`。
- 「扩展管理 → 从链接安装」接受两种链接：
  - zip 直链（功能扩展 / 内容扩展 / aboutme 单角色包，`preview_zip`）；
  - **GitHub 角色目录** `https://github.com/<owner>/<repo>/tree/<ref>/<path>`
    （`petcore/net/aboutme_repo.py` + `url_install.fetch_tree`）：只解析出
    owner/repo/ref/path，自己拼 `raw.githubusercontent.com`，按 `manifest.json` /
    `actions.json` 声明的 `sources.webp` 逐个下载（单文件 16 MB、总量 512 MB、
    ≤400 个；不消耗 GitHub API 配额），再走 `build_sprite_pack` 的 staging/回滚。
    默认只取 `essential_actions()` 的常用动作（待机/移动/点击/拖拽/工作 + 2 个随机，
    ≤8 张，与 curated 下载同口径）；「下载全部动作」勾选后整包（可能上百张）。
    设置页提供 9 个 aboutme 角色包下拉 +「打开 / 填入输入框」按钮。

## 7. 制作新角色包

1. 按第 1 节建立 `videos/` 与分类目录；
2. 写 `manifest.json`（至少 body_box）；
3. 可选 `move_strides.json` / `text_clips.json`；
4. 写 `mod.json`（`kind: "content"`，`character.media: "videos"`）；
5. `python -m petcore mods validate <目录>`；
6. `python -m petcore mods install <目录或.zip>`；
7. `python -m petcore --check` 确认每段 clip 采用的后端；
8. `python -m petcore --character <id>` 实机确认。

旧内置角色可用 `python -m petcore pack-legacy shenshen --out mods/character.shenshen`（默认硬链接，避免复制 53MB）生成标准包。
