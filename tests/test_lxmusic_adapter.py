# -*- coding: utf-8 -*-
"""lxmusic 适配器的离线单测：起一个假的 /status，验证解析与 Playback 构造。"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, r"C:\Users\Tomeu\Documents\dsh-pet-src")

from pet.music import lxmusic  # noqa: E402

PAYLOAD = {
    "status": "playing",
    "name": "Let me go (独唱版)",
    "singer": "星落落oi",
    "albumName": "单曲",
    "progress": 47.5,
    "duration": 200.0,
}
STATE = {"payload": PAYLOAD}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(STATE["payload"], ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run_case(name, payload, expect_none=False):
    STATE["payload"] = payload
    got = lxmusic.read_playback()
    if expect_none:
        ok = got is None
        print(("PASS  " if ok else "FAIL  ") + name + "  -> " + repr(got))
        return ok
    ok = (
        got is not None
        and got.track.title == payload["name"]
        and got.track.artist == payload["singer"]
        and got.position == payload["progress"]
        and got.app_id == lxmusic.LX_APP_ID
        and got.track.playing is True
    )
    print(("PASS  " if ok else "FAIL  ") + name + "  -> " + (repr(got) if got else "None"))
    return ok


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # 绕开真实配置：直连测试端口
    lxmusic._read_lx_config = lambda: (True, port)  # type: ignore[assignment]
    lxmusic._blocked_until = 0.0

    results = []
    results.append(run_case("正常在播", PAYLOAD))
    results.append(run_case("暂停", {**PAYLOAD, "status": "paused"}, expect_none=True))
    results.append(run_case("停止", {**PAYLOAD, "status": "stoped"}, expect_none=True))
    results.append(run_case("空标题空歌手", {**PAYLOAD, "name": "", "singer": ""}, expect_none=True))
    # 进度字段脏：不该抛，position 退化为 None
    STATE["payload"] = {**PAYLOAD, "progress": "abc"}
    got = lxmusic.read_playback()
    ok = got is not None and got.position is None
    print(("PASS  " if ok else "FAIL  ") + "进度脏值降级  -> " + repr(got))
    results.append(ok)

    # 设置页开关关掉时（allow_lxmusic=False）整条源不参与，直接回落 SMTC
    from pet.music import now_playing

    STATE["payload"] = PAYLOAD
    off = now_playing._read_lx_playback(False)
    ok = off is None
    print(("PASS  " if ok else "FAIL  ") + "开关关闭时短路  -> " + repr(off))
    results.append(ok)

    on = now_playing._read_lx_playback(True)
    ok = on is not None and on.app_id == lxmusic.LX_APP_ID
    print(("PASS  " if ok else "FAIL  ") + "开关打开时生效  -> " + ("Playback" if on else "None"))
    results.append(ok)

    server.shutdown()
    print("\n%d/%d 通过" % (sum(1 for r in results if r), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
