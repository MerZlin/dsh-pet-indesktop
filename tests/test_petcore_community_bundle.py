"""随包致谢数据的验收：文件必须存在、可解析、且内容是真的。

运行期从不写回 ``petcore/community/data``，所以这一层的错误只能在这里拦住：
缺少快照或快照退化成空占位时，构建出的版本关于页会显示「暂无」。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from petcore.community import model, store

VMID = 293026389
MAX_FANS_BYTES = 2 * 1024 * 1024


def _load(name: str) -> dict:
    path: Path = store.bundle_dir() / name
    assert path.is_file(), f"随包数据缺失：{path}（跑 scripts/fetch_community_credits.py 生成）"
    return json.loads(path.read_text(encoding="utf-8"))


def test_bundled_credits_snapshot_is_complete() -> None:
    credits = model.parse_credits(_load(store.CREDITS_FILE))

    assert credits.repo, "credits.json 必须写明仓库"
    datetime.fromisoformat(credits.fetched_at.replace("Z", "+00:00"))
    handles = [item.handle for item in credits.author_accounts]
    assert "@張墨林" in handles
    assert any(item.platform == "抖音" and item.handle == "@張墨林" for item in credits.author_accounts)
    assert len(credits.contributors) >= 5, "贡献者区不应退化成空"
    assert len(credits.authors) >= 5, "issue/PR 协作者区不应退化成空"
    assert all(item.total > 0 for item in credits.authors)


def test_bundled_special_thanks_name_the_upstream_project_and_artists() -> None:
    credits = model.parse_credits(_load(store.CREDITS_FILE))
    entries = {item.name: item for item in credits.special_thanks}

    upstream = entries["PC2005-cloud/dsh-pet"]
    assert "蓝色大肥鱼" in upstream.note
    assert upstream.url.startswith("https://github.com/")
    for artist in ("@这个刀子真甜", "@上善无形", "@ZipZipPipe"):
        assert artist in entries, f"特别鸣谢缺 {artist}"
        assert "形象" in entries[artist].note
    for contributor in ("@夜宵夜吃", "@小透明到处逛", "@苏辞SuTzu", "@貓事液體這件事"):
        assert contributor in entries, f"特别鸣谢缺 {contributor}"


def test_bundled_fans_snapshot_is_a_real_list() -> None:
    path = store.bundled_fans_path()
    payload = _load(store.FANS_FILE)
    fans = model.parse_fans(payload)

    assert payload.get("schema") == 1
    assert fans.vmid == VMID
    stamp = datetime.fromisoformat(fans.fetched_at.replace("Z", "+00:00"))
    now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
    assert abs((now - stamp).days) <= 366, "抓取时间不在合理窗口（多半是年份写错）"
    assert fans.names, "粉丝快照是空占位：重新跑 scripts/fetch_community_credits.py --fans"
    assert len(fans.names) == len(set(fans.names)), "快照里有重复用户名（脚本已去重）"
    # normalize 是幂等的：能抓出控制字符/超长/首尾空白没处理的情况
    assert all(
        model.normalize_fan_name(name) == name for name in payload["names"]
    ), "有用户名没被 normalize 处理（含控制字符、折叠空白或超长）"
    assert all(0 < len(name) <= 64 for name in fans.names)
    assert fans.total >= len(fans.names), "total 是 B 站口径人数，不能小于去重后的名字数"
    assert fans.total > 0, "缺 total 会让每次刷新都误判「有新粉丝」并重拉 79 页"
    # total 与名字数只该差「账号已注销」这类同名占位；差太多说明快照被截断或 total 写错
    assert 0 <= fans.total - len(fans.names) <= 20, "total 与名单长度差距不合理"
    assert path.stat().st_size <= MAX_FANS_BYTES, "粉丝快照过大，会拖慢随包体积"


def test_bundled_data_is_wired_into_both_packaging_paths() -> None:
    """检查受版本控制的 Windows/POSIX 构建入口，不依赖本机生成的 spec。"""
    from petcore.platform.bundle import DATA_DIRS
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_petcore.ps1").read_text(encoding="utf-8")

    assert "petcore/community/data" in DATA_DIRS
    assert "petcore/community/data;petcore/community/data" in script


def test_bundled_data_files_are_utf8_without_bom() -> None:
    for name in (store.CREDITS_FILE, store.FANS_FILE):
        raw = (store.bundle_dir() / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 带了 BOM"
        raw.decode("utf-8")
