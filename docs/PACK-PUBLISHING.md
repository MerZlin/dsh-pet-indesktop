# 素材包发布指南（给素材制作者）

> 面向：负责制作/发布角色素材包的人。不需要懂代码，照着流程走即可。
> 最后更新：2026-10-20（素材包市场 v2 形态）。

## 0. 一分钟看懂这套体系

- 素材以「包」为单位分发：**标清包（640×360）/ 高清包（1280×720）/ 服装变体包**并存，
  用户在设置页「角色与素材」里看到一行卡片，点下载，**重启桌宠生效**。
- 一个包切成若干**分件**：`core`（日常动作：待机/移动/转向/点击/拖拽，必须有）+
  `extra1、extra2…`（随机/事件动作，按 ~256MB 自动切块，用户按需下载）。
- 货架 = **GitHub Release**（放 zip 大包）+ **仓库 main 分支的 `packs-index.json`**（清单）。
  用户的设置页每次打开时拉清单，**发新包不需要用户更新桌宠版本**（除非 §4 的情形）。
- 每个分件有**三条下载地址**（GitHub 直连 + 两条镜像），断流自动换线 + 断点续传，
  你不用管国内用户连不上 GitHub 的事。
- **发布门**：打包脚本会把你打的包真装一遍并全量校验，门不过就不许发——
  所以你基本不可能发出一个「用户装不上」的包。

## 1. 前提：素材帧序列已按当前档位产出

包的内容不是 webm 视频，是**无损 WebP 帧序列**（每段动画一个目录，里面是
`f_0001.webp…` + `meta.json`）。从 webm 产出帧序列：

```powershell
python tools/convert_frameseq.py --character <角色id> --include random,events
```

- 产物在 `assets/characters/<角色id>/frameseq/`。
- **档位纪律**：帧序列的编码档必须与代码当前档一致（现在是
  `libwebp lossless (bgra straight, unblend-white-v6)`）。不一致时发布门会拒，
  提示「先重出素材」——把 frameseq 目录删掉重跑上面的命令即可，不要手动改任何
  meta.json（改了也过不了逐帧校验，纯属浪费时间）。
- 如果素材本身更新了（新动作、重制），重跑同一命令，幂等，只转新增/变化的。

## 2. 打包 + 发布门（一条命令）

```powershell
python scripts/build_character_pack.py --character shenshen --version 2.0.0 `
    --url-base "https://github.com/MerZlin/dsh-pet-indesktop/releases/download/assets-v1"
```

参数：

| 参数 | 说明 |
|---|---|
| `--character` | 角色 id（`assets/characters/` 下的目录名） |
| `--version` | 包版本号。**内容变了必须抬版本号**（目录不可变：同路径不同内容会被安装端拒绝） |
| `--variant` | 变体名（默认 `base`；冬装这类用 `winter` 等） |
| `--url-base` | 分件在 Release 上的下载前缀（见 §3，决定清单里的下载地址） |
| `--app-min` | 最低应用版本（默认 4.4.0；只有 §4 的情形才需要动） |

它会依次做：收集全部 clip → core 闭包校验（五类日常动作缺一类直接失败）→
按 core / extraN 打 zip（产物在 `dist-packs/`）→ **发布门**：把每个 zip 真装进
临时目录，过解包预算 / 结构校验 / 发行身份 / 逐段摘要 / 首尾帧真实解码 + 画布比对 /
运行时采纳闸门，最后查 core 闭包——**全过才生成清单**。

输出（以 shenshen 2.0.0 为例）：

```
dist-packs/shenshen-2.0.0-base-640x360.core.zip      （日常动作，~138MB）
dist-packs/shenshen-2.0.0-base-640x360.extra1~6.zip  （其余动作，各 ~250MB）
packs-index.json（仓库根，自动并入新包）
```

## 3. 上架（两个动作，缺一不可）

**权限说明**：传 Release 和 push main 都需要仓库写权限（目前只有仓库共创者）。
素材制作者的分工边界：**§1–§2（素材产出 + 打包 + 发布门）在本地就能全部做完**，
把 `dist-packs/` 的产物和 `packs-index.json` 的变更交给有权限的人执行下面两步
（或者让仓库主人把你加成 collaborator，Triage/Write 权限即可）。

**① zip 传 Release**（gh CLI 示例，也可以用网页上传）：

```powershell
gh release upload assets-v1 dist-packs/*.zip --clobber
```

没有 `assets-v1` 这个 Release 就先建一个（`gh release create assets-v1`）。
**注意**：`--url-base` 里的路径必须和实际 Release 下载前缀一致，否则清单里的地址是错的。

**② 清单提交到 main**：

```powershell
git add packs-index.json
git commit -m "assets: 发布 shenshen 2.0.0 素材包"
git push
```

用户端看到的是 main 分支上的清单（经 jsdelivr 分发）。**jsdelivr 有小时级缓存**，
刚 push 不一定立刻生效，急用的话手动 purge：
`https://purge.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/packs-index.json`

## 4. 什么时候用户需要更新桌宠版本（app_min）

只有这一种情况：包用到了**当前桌宠版本没有的新机制**（格式变更、带代码的插件、
新的几何/能力）。这时 `--app-min` 标成包含该机制的版本，老版本用户会看到置灰卡片
「需要应用 ≥ X」，而不是装一个用不了的。

**纯数据新增**（新动作、冬装、新角色、高清包）一律不需要抬 `app_min`——
默认 4.4.0 即可，用户不用更新桌宠。

## 5. 发布 720p 高清包 / 变体包

- **720p**：素材帧序列按 1280×720 产出（同一条 convert 命令，素材源是 720p 的即可），
  打包时画布自动识别。清单里它会显示为「高清角色包」卡片，与标清包并存，用户任选。
  注意：720p 素材**必须**带正确的 `manifest.json`（body_box/head_box 按 1280×720 像素写），
  应用会自动换算，不会把宠物放大。
- **变体（冬装等）**：`--variant winter`，目录结构与 base 相同。用户按角色选择穿哪套。

## 6. 常见发布门失败对照

| 报错 | 意思 | 怎么办 |
|---|---|---|
| `缺世代目录` | 有 webm 没转帧序列 | 跑 §1 的 convert 命令 |
| `档位不符（先重出素材）` | 帧序列是旧编码档 | 删 frameseq 重转，**不许手改 meta** |
| `帧数不符` | meta 声明帧数 ≠ 磁盘帧数 | 重转该段（转的时候被打断过） |
| `帧率不一致` | 包内各段 fps 不同 | 一个包必须同帧率，统一后再打 |
| `core 分件缺必需动作类` | idle/move/turn/click/drag 缺一类 | 这不是分包问题，是素材缺动作 |
| `运行时采纳闸门不过` | 帧能装上但播放器不认 | 一般是编码档/画布/meta 不对，重转 |
| `同路径不同内容，拒绝覆盖` | 用同一版本号发了不同内容 | **抬版本号重发**（目录不可变） |

## 7. 发完自验（5 分钟）

1. 浏览器打开 `https://cdn.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/packs-index.json`，
   能看到新包条目（看不到 = 清单没 push 或缓存没刷）。
2. 桌宠设置 → 桌宠 → 角色与素材：新包出现在「可下载」（没出现 = 同上）。
3. 点一个最小的分件下载 → 显示「已安装，重启后生效」。
4. core 全装后点「使用这套」→ 按钮变「已选择，重启后生效」→ 重启桌宠 →
   日志里有 `素材加载完成：<角色> N 段动画`，动作正常播放。

## 8. 千万别做的事

- 不要手动改 `packs-index.json` 里的 sha256/bytes（那是发布门算出来的；改了用户端
  校验必失败）。
- 不要复用旧版本号发新内容（安装端会拒；抬版本号）。
- 不要把 zip 里的目录结构改掉（`part.json` + `anim/` + sidecar，发布门按这个出）。
- 不要绕过发布门（`--skip-verify` 只给调试本地链路用，正式包必须过门）。
