"""设置页共用的外链常量：只放 URL 字符串，不做任何网络请求。"""

from __future__ import annotations

REPO_URL = "https://github.com/MerZlin/dsh-pet-indesktop"
QUARK_URL = "https://pan.quark.cn/s/68fc681ae486"
DEEPSEEK_API_URL = "https://platform.deepseek.com/api_keys"
#: 「完整角色素材包 / 高清完整角色素材包」的 GitHub 落点；换成独立素材页只改这里。
#: 指向 assets-v1 Release（分件 zip 与 packs-index.json 的实际存放处），
#: 用户点开就能看到 core/extra 分件；仓库根目录仍可从该页一跳到达。
CHARACTER_PACKS_URL = f"{REPO_URL}/releases/tag/assets-v1"
