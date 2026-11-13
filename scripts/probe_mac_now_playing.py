# -*- coding: utf-8 -*-
"""macOS 音乐联动的实机探针：在真 Mac 上跑一遍，确认读曲目 / 找播放器 / 发命令三条链路。

用法（仓库根目录，源码环境）::

    python3 scripts/probe_mac_now_playing.py            # 只读：正在播放 + 播放器定位
    python3 scripts/probe_mac_now_playing.py --watch 10 # 每秒读一次，连读 10 秒（看进度外推）
    python3 scripts/probe_mac_now_playing.py --toggle   # 额外发一次「暂停/播放」并回读状态

输出逐项给出结论（OK / 不可用 + 原因）。它直接调用产品代码（``pet/music/mac_media`` /
``now_playing`` / ``players``），与桌宠运行时走的是同一条路径；另外单独起一次
osascript 打印助手脚本的原始 stderr——助手本身在产品里把 stderr 丢掉，脚本被系统拒绝
时只有这里看得到原因。

本脚本不改任何配置、不写盘；``--toggle`` 会真的暂停/恢复当前播放器（再跑一次恢复）。
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.music import mac_media, now_playing, players  # noqa: E402


def _raw_helper_roundtrip() -> None:
    """单独起一次助手，发一条请求，原样打印 stdout / stderr / 退出码。"""
    print("== 1. 助手脚本原始往返（osascript -l JavaScript）")
    try:
        result = subprocess.run(
            mac_media._helper_argv(), input=b"probe\n", capture_output=True, timeout=10,
        )
    except FileNotFoundError:
        print(f"   不可用：找不到 {mac_media.OSASCRIPT}")
        return
    except subprocess.TimeoutExpired:
        print("   不可用：10 秒内没有应答（MediaRemote 调用挂住？）")
        return
    print(f"   exit={result.returncode}")
    print(f"   stdout={result.stdout.decode('utf-8', 'replace').strip()}")
    stderr = result.stderr.decode("utf-8", "replace").strip()
    if stderr:
        print(f"   stderr={stderr}")


def _show_state(label: str) -> mac_media.MacNowPlaying | None:
    state = mac_media.read_now_playing(max_age=0)
    if state is None:
        print(f"   {label}: 没有读到「正在播放」（没有应用在播，或读取不可用——看第 1 项）")
        return None
    position = mac_media.position_at(state, time.time())
    print(f"   {label}: app={state.app_id or '?'} ({state.display_name or '-'})"
          f" playing={state.playing} title={state.title!r} artist={state.artist!r}"
          f" duration={state.duration} elapsed={state.elapsed} rate={state.rate}"
          f" mediaType={state.media_type!r} position≈{position}")
    return state


def _now_playing_view() -> None:
    print("== 3. 产品层（now_playing）看到的结果")
    for allow_browser in (False, True):
        playback = now_playing.get_now_playing(allow_browser=allow_browser)
        if playback is None:
            print(f"   allow_browser={allow_browser}: None（不显示、不取词、不唱歌）")
        else:
            print(f"   allow_browser={allow_browser}: {playback.track.title!r} / "
                  f"{playback.track.artist!r} playing={playback.track.playing} "
                  f"position={playback.position} app_id={playback.app_id}")
    facts = now_playing.refresh_session_probe_once()
    print(f"   唱歌探针事实表={facts}  song_session_playing(浏览器关)="
          f"{now_playing.song_session_playing(False)}")


def _players_view() -> None:
    print("== 4. 播放器定位（右键「打开…给主人放歌」）")
    for key in players.player_keys():
        players.clear_cache()
        started = time.perf_counter()
        path = players.find_player(key)
        cost = (time.perf_counter() - started) * 1000
        identity = players.session_key(path) if path else ""
        print(f"   {players.player_label(key)}: {path or '未找到'}"
              f"{f'  bundle id={identity}' if identity else ''}  ({cost:.1f} ms)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--watch", type=int, default=0, help="连续读取的秒数")
    parser.add_argument("--toggle", action="store_true", help="发一次暂停/播放并回读")
    args = parser.parse_args()

    print(f"macOS {platform.mac_ver()[0] or '?'}  machine={platform.machine()}  "
          f"python={sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}")
    if sys.platform != "darwin":
        print("不是 macOS：本探针只在 Mac 上有意义。")
        return 2

    _raw_helper_roundtrip()

    print("== 2. 常驻助手读取（产品路径，首个请求含助手启动）")
    started = time.perf_counter()
    _show_state("首读")
    print(f"   首读耗时 {(time.perf_counter() - started) * 1000:.1f} ms")
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        mac_media.read_now_playing(max_age=0)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    print(f"   稳态 20 次：中位 {samples[10]:.2f} ms，最大 {samples[-1]:.2f} ms，"
          f"助手 pid={mac_media.helper_pid()}")
    for second in range(args.watch):
        time.sleep(1.0)
        _show_state(f"第 {second + 1} 秒")

    _now_playing_view()
    _players_view()

    if args.toggle:
        print("== 5. 发送「暂停/播放」（MRMediaRemoteSendCommand）")
        print(f"   toggle_play_pause() -> {now_playing.toggle_play_pause()}")
        time.sleep(1.0)
        _show_state("1 秒后")

    print(json.dumps({"helper_pid": mac_media.helper_pid()}, ensure_ascii=False))
    mac_media.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
