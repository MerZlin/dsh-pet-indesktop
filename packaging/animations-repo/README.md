# Animations · dsh-pet-core 素材包仓库

这里是 [dsh-pet-core](https://github.com/MerZlin/dsh-pet-indesktop) 的角色**素材包**仓库。
应用内「设置 → 常规 → 素材包」直接读本仓库的 `packs-index.json`，从 Releases 下载 ZIP，
显示进度与实时网速；服务端支持 Range 时用多连接分段下载。

## 文件

| 文件 | 作用 |
|---|---|
| `packs-index.json` | 应用读取的索引（包名、大小、sha256、下载地址） |
| `Releases → packs-v1` | 实际的素材包 ZIP（不进 git，走 Release 资产） |

## 包格式

ZIP 根目录必须是角色目录的**内容**：

```
shenshen-1.zip
├── videos/          # 各动作的 webm / 图片帧序列 + manifest.json
└── frameseq/        # 可选的帧序列目录
```

应用会用 `install_character_source()` 校验并安装成 `character.<id>`，
已存在的角色 id 不会被覆盖。

## 索引格式

```json
{
  "schema": 1,
  "packs": [
    {
      "id": "shenshen",
      "name": "咲恋 · 完整素材包",
      "version": "1",
      "filename": "shenshen-1.zip",
      "size": 148000000,
      "sha256": "64 位十六进制",
      "character": "shenshen",
      "urls": ["https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"],
      "links": {"github": "…", "quark": "…"},
      "notes": ""
    }
  ]
}
```

`urls` 里的每一条都必须是 https，且主机在白名单内（github.com、release-assets、
objects.githubusercontent.com、raw.githubusercontent.com）。应用会逐条回退，
所以可以按镜像快慢排序。

## 发布新包

在 dsh-pet-core 仓库里：

```powershell
# 1) 打包 + 生成条目（写入 .scratch/animations-repo/packs-index.json）
python scripts/make_animation_pack.py `
  --source assets/characters/shenshen `
  --out .scratch/animations-repo `
  --id shenshen --version 2 --name "咲恋 · 完整素材包"

# 2) 推索引 + 发 Release（需要 gh 已登录）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/publish_animation_pack.ps1
```

## 许可

素材版权归原画师与作者所有，仅用于 dsh-pet-core 桌宠。转载/二次分发请先联系作者。
