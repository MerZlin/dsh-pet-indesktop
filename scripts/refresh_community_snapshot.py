#!/usr/bin/env python
"""刷新 ``petcore/community/data`` 下的致谢/粉丝快照（提交时状态）。

用法::

    python scripts/refresh_community_snapshot.py
    python scripts/refresh_community_snapshot.py --credits

- 默认刷新 ``fans.json``：SESSDATA 从系统钥匙串（``community.bilibili/sessdata``）
  读取，与设置 →「致谢」里保存的是同一份凭据；未配置时退出码 2。
- ``--credits`` 额外刷新 ``credits.json`` 的 ``contributors`` / ``authors`` 两个键；
  失败只打印原因，不动文件。
- 名单没读完（partial）不写文件，避免半截名单覆盖提交时的完整快照。
- **绝不打印凭据**：输出只有条数与耗时。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from petcore.api.credentials import KeyringCredentials  # noqa: E402
from petcore.community import model  # noqa: E402
from petcore.community.service import (  # noqa: E402
    COOKIE_KEY,
    COOKIE_OWNER,
    DEFAULT_REPO,
    DEFAULT_VMID,
    reason_of,
    stamp_now,
)

DATA_DIR = ROOT / "petcore" / "community" / "data"
MAX_FAN_BUDGET_S = 300.0
NO_COOKIE_MESSAGE = "未配置 B 站凭据：设置 → 致谢 → 粘贴 SESSDATA 后再运行"


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict) -> None:
    """先写同目录临时文件再 ``os.replace``，中途失败不留半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _sessdata() -> str:
    try:
        value = KeyringCredentials().get(COOKIE_OWNER, COOKIE_KEY)
    except Exception:  # noqa: BLE001 - 钥匙串不可用按未配置处理
        return ""
    return str(value or "").strip()


def refresh_fans(path: Path) -> int:
    from petcore.net import bilibili

    cookie = _sessdata()
    if not cookie:
        print(NO_COOKIE_MESSAGE, file=sys.stderr)
        return 2

    existing = _load(path)
    try:
        vmid = int(existing.get("vmid") or DEFAULT_VMID)
    except (TypeError, ValueError):
        vmid = DEFAULT_VMID

    started = time.monotonic()
    total = 0
    try:
        total = bilibili.fetch_fan_count(vmid)
    except Exception as exc:  # noqa: BLE001 - 总数失败不阻断名单抓取
        print(f"粉丝总数查询失败：{reason_of(exc)}（继续读取名单）", file=sys.stderr)

    def progress(page: int, pages: int) -> None:
        print(f"\r粉丝第 {page}/{pages} 页…", end="", file=sys.stderr)

    try:
        result = bilibili.fetch_fans(
            vmid, sessdata=cookie, page_size=50, budget_s=MAX_FAN_BUDGET_S, progress=progress
        )
    except Exception as exc:  # noqa: BLE001 - 失败不动文件
        print("", file=sys.stderr)
        print(f"粉丝名单抓取失败：{reason_of(exc)}，fans.json 未改动", file=sys.stderr)
        return 1
    print("", file=sys.stderr)

    names: list[str] = []
    seen: set[str] = set()
    for item in result.get("names") or ():
        name = model.normalize_fan_name(item)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)

    list_total = int(result.get("total") or 0)
    if result.get("partial"):
        print(
            f"名单未读完（{len(names)}/{list_total or '?'}），fans.json 未改动",
            file=sys.stderr,
        )
        return 1
    if not names:
        print("没有抓到任何粉丝名，fans.json 未改动", file=sys.stderr)
        return 1

    payload = {
        "schema": existing.get("schema", 1),
        "vmid": vmid,
        "fetched_at": stamp_now(),
        # B 站口径总人数（含「账号已注销」这类同名占位），与 known_total() 同源。
        "total": total or list_total or len(names),
        "names": names,
    }
    _write(path, payload)
    elapsed = time.monotonic() - started
    print(
        f"fans.json: {len(names)} 位粉丝（B 站总数 {payload['total']}），用时 {elapsed:.1f}s",
        file=sys.stderr,
    )
    return 0


def refresh_credits(path: Path, repo: str) -> int:
    from petcore.net import github

    existing = _load(path)
    started = time.monotonic()
    try:
        contributors = github.fetch_contributors(repo)
        activity = github.fetch_activity_authors(repo)
    except Exception as exc:  # noqa: BLE001 - 失败不动文件
        print(f"贡献者名单抓取失败：{reason_of(exc)}，credits.json 未改动", file=sys.stderr)
        return 1
    rows = [
        {"login": item.login, "contributions": item.contributions}
        for item in model.parse_contributors(contributors)
    ]
    authors = [
        {"login": item.login, "issues": item.issues, "prs": item.prs}
        for item in model.parse_authors(
            activity.get("authors") if isinstance(activity, dict) else (), repo=repo
        )
    ]
    if not rows and not authors:
        print("没有抓到任何贡献者/协作者，credits.json 未改动", file=sys.stderr)
        return 1
    payload = dict(existing)
    payload["contributors"] = rows
    payload["authors"] = authors
    _write(path, payload)
    elapsed = time.monotonic() - started
    print(
        f"credits.json: {len(rows)} 位贡献者 / {len(authors)} 位 issue·PR 协作者，"
        f"用时 {elapsed:.1f}s",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--credits", action="store_true", help="刷新贡献者与 issue/PR 作者")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库（owner/name）")
    parser.add_argument("--out-dir", default=str(DATA_DIR), help="输出目录")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    status = 0
    if args.credits:
        # 贡献者走 GitHub，与 B 站凭据无关：先做，缺凭据也不白跑一趟。
        status = refresh_credits(out_dir / "credits.json", args.repo)
    status = max(status, refresh_fans(out_dir / "fans.json"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
