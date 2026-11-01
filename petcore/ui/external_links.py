"""设置页共用的外链常量：只放 URL 字符串，不做任何网络请求。"""

from __future__ import annotations

REPO_URL = "https://github.com/MerZlin/dsh-pet-indesktop"
QUARK_URL = "https://pan.quark.cn/s/68fc681ae486"
DEEPSEEK_API_URL = "https://platform.deepseek.com/api_keys"
#: 「完整角色素材包 / 高清完整角色素材包」的 GitHub 落点；换成独立素材页只改这里。
#: 指向 assets-v1 Release（分件 zip 与 packs-index.json 的实际存放处），
#: 用户点开就能看到 core/extra 分件；仓库根目录仍可从该页一跳到达。
CHARACTER_PACKS_URL = f"{REPO_URL}/releases/tag/assets-v1"

#: 素材包专用仓库（只放素材包与索引，与主仓库解耦；换仓只改这两行）。
ANIMATIONS_REPO_URL = "https://github.com/MerZlin/Animations"
ANIMATIONS_RELEASE_TAG = "packs-v1"
#: 应用内下载读的索引。索引作为 Release 资产发布（走 api 直链，永远最新），
#: raw 做兜底；两个都必须是 https 且在 `net/packs.DEFAULT_PACK_HOSTS` 白名单内。
PACK_INDEX_URL = (
    "https://github.com/MerZlin/Animations/releases/download/packs-v1/packs-index.json"
)
#: 索引的兜底源：jsDelivr 有 CDN 缓存可能滞后，raw 只是最后一条路（本机常被 RST）。
PACK_INDEX_FALLBACKS = (
    "https://fastly.jsdelivr.net/gh/MerZlin/Animations@main/packs-index.json",
    "https://raw.githubusercontent.com/MerZlin/Animations/main/packs-index.json",
)
