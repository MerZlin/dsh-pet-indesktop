#!/usr/bin/env python
"""生成 ``petcore/community/data`` 下的致谢/粉丝快照（提交时状态）。

用法::

    python scripts/fetch_community_credits.py --github
    DSH_PET_BILI_SESSDATA=... python scripts/fetch_community_credits.py --fans
    python scripts/fetch_community_credits.py --fans --cookie-file secret.txt

- ``--github``：刷新贡献者与 issue/PR 作者，保留手工维护的作者账号/特别鸣谢。
- ``--fans``：用 SESSDATA 抓完整粉丝名单；**没读完（partial）就不写文件**，
  避免用半截名单覆盖提交时的完整快照。
- Cookie 只从环境变量或文件读，不写进任何输出/快照。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from petcore.community import model  # noqa: E402
from petcore.community.service import DEFAULT_REPO, DEFAULT_VMID, stamp_now  # noqa: E402

MAX_FAN_BUDGET_S = 300.0


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _contributor_rows(items) -> list[dict]:
    return [
        {"login": item.login, "contributions": item.contributions}
        for item in model.parse_contributors(items)
    ]


def _author_rows(items, repo: str) -> list[dict]:
    return [
        {"login": item.login, "issues": item.issues, "prs": item.prs}
        for item in model.parse_authors(items, repo=repo)
    ]


def update_github(out_dir: Path, repo: str) -> int:
    from petcore.net import github

    existing = _load(out_dir / "credits.json")
    bundle = model.parse_credits(existing)
    print("contributors…", file=sys.stderr)
    contributors = github.fetch_contributors(repo)
    print("issues / pull requests…", file=sys.stderr)
    activity = github.fetch_activity_authors(repo)
    rows = _contributor_rows(contributors)
    authors = _author_rows(
        activity.get("authors") if isinstance(activity, dict) else (), repo
    )
    payload = {
        "schema": 1,
        "repo": repo,
        "fetched_at": stamp_now(),
        "author_accounts": [
            {
                "platform": item.platform,
                "handle": item.handle,
                "url": item.url,
                **({"role": item.role} if item.role else {}),
            }
            for item in (bundle.author_accounts or model.DEFAULT_AUTHOR_ACCOUNTS)
        ],
        "special_thanks": [
            {"kind": item.kind, "name": item.name, "url": item.url, "note": item.note}
            for item in (bundle.special_thanks or model.DEFAULT_SPECIAL_THANKS)
        ],
        "contributors": rows,
        "authors": authors,
    }
    _write(out_dir / "credits.json", payload)
    print(
        f"credits.json: {len(rows)} 位贡献者 / {len(authors)} 位 issue·PR 协作者",
        file=sys.stderr,
    )
    return 0


def _cookie(args) -> str:
    if args.cookie_file:
        try:
            return Path(args.cookie_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"读取 cookie 文件失败：{type(exc).__name__}", file=sys.stderr)
            return ""
    return str(os.environ.get("DSH_PET_BILI_SESSDATA") or "").strip()


def update_fans(out_dir: Path, vmid: int, cookie: str) -> int:
    if not cookie:
        print(
            "缺少 B 站 SESSDATA：请设置 DSH_PET_BILI_SESSDATA 或传 --cookie-file",
            file=sys.stderr,
        )
        return 2
    from petcore.net import bilibili

    def progress(page: int, total: int) -> None:
        print(f"\r粉丝第 {page}/{total} 页…", end="", file=sys.stderr)

    result = bilibili.fetch_fans(
        vmid,
        sessdata=cookie,
        page_size=50,
        budget_s=MAX_FAN_BUDGET_S,
        progress=progress,
    )
    print("", file=sys.stderr)
    names = []
    seen: set[str] = set()
    for item in result.get("names") or ():
        name = model.normalize_fan_name(item)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    if result.get("partial"):
        print(
            f"名单未读完（{len(names)}/{result.get('total')}），未写入 fans.json",
            file=sys.stderr,
        )
        return 3
    if not names:
        print("没有抓到任何粉丝名，未写入 fans.json", file=sys.stderr)
        return 3
    payload = {
        "schema": 1,
        "vmid": int(vmid),
        "fetched_at": stamp_now(),
        # B 站口径总人数（含「账号已注销」这类同名占位）；运行期用它判断有没有新粉丝。
        "total": int(result.get("total") or len(names)),
        "names": names,
    }
    _write(out_dir / "fans.json", payload)
    print(f"fans.json: {len(names)} 位粉丝（B 站总数 {result.get('total')}）", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github", action="store_true", help="刷新贡献者与 issue/PR 作者")
    parser.add_argument("--fans", action="store_true", help="刷新 B 站粉丝名单")
    parser.add_argument("--cookie-file", default="", help="保存 SESSDATA 的文件（不要提交）")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--vmid", type=int, default=DEFAULT_VMID)
    parser.add_argument(
        "--out-dir", default=str(ROOT / "petcore" / "community" / "data"), help="输出目录"
    )
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    both = not (args.github or args.fans)
    status = 0
    if args.github or both:
        status = max(status, update_github(out_dir, args.repo))
    if args.fans or both:
        status = max(status, update_fans(out_dir, args.vmid, _cookie(args)))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
