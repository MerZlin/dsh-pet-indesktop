# -*- coding: utf-8 -*-
"""G1b：子宠设置路由 → 新档案存储（``pet/pet_profiles.py``）的接线契约。

G1a 交付了存储 API（每宠档案 / 活跃集合 v2 / 代次 / ``reset_slot_profile``），**零消费
者**。本文件钉住 G1b 把「子宠设置页的配置面」（:class:`SlotRoutedConfig`）真正接到那套
API 上的行为：

1. **写**：pending 的每宠键**一批**进档案（锁内重读合并 + 一次原子替换），不再逐键裸写
   （旧路径的 ``overlay_spawn_state.write_slot_setting`` 在路由上不再被调用）；写不成功
   **不清 pending**、坏档案一个字节都不覆盖、``save()`` 如实返回 False；
2. **读**：缺键走**同源默认**（``Config`` 出厂默认），不再持续继承主宠的当前值；
3. **reset**：走 ``reset_slot_profile``——保 ``rx/ry/screen_name``、偏好（含 ``facing``）
   回同源默认、别宠与全局一个字节不动；``partial`` 必须返回 False 且代次缓存及时对齐
   （第二次 reset 能收敛，不被自己上一次推进的代次挡在门外）；
4. **并发**：跨进程写**不同**档案键谁都不丢（真子进程，不用 sleep 猜时序）。

这里**不**覆盖运行壳（``overlay_shell``）的读路径与指令消费：它们仍走旧逻辑，属下一批。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pet import config_write_lock as cwl
from pet import overlay_settings_command as cmd_mod
from pet import pet_profiles
from pet import settings_slot_config
from pet.config import Config
from pet.settings_slot_config import SlotRoutedConfig

REPO = str(Path(__file__).resolve().parent.parent)
WAIT = 60.0  # 子进程同步宽预算（CI 慢机不赌）

#: 子进程脚本（模式经 env 传入）。
#:
#: hold   —— 持目录写锁 → 报 LOCKED → 等 release 哨兵 → 报 RELEASING → 释放
#: routed —— 经**路由视图**反复写自己那一个每宠键（跨进程异键并写不丢）
_CHILD = r"""
import os, sys, time
sys.path.insert(0, os.environ["SPI_REPO"])

mode = os.environ["SPI_MODE"]
config_dir = os.environ["SPI_DIR"]

if mode == "hold":
    from pet import config_write_lock as cwl
    with cwl.config_write_lock(config_dir, timeout=10.0):
        print("LOCKED", flush=True)
        release = os.environ["SPI_RELEASE"]
        deadline = time.monotonic() + 60.0
        while not os.path.exists(release) and time.monotonic() < deadline:
            time.sleep(0.01)
        print("RELEASING", flush=True)
elif mode == "routed":
    os.environ["PET_RENDER_TOPOLOGY"] = "overlay"
    os.environ.pop("DSH_PET_PARENT_TOPOLOGY", None)
    os.environ.pop("DSH_PET_INSTANCE", None)
    from pet.config import Config
    from pet import settings_slot_config as ssc
    base = os.path.dirname(config_dir)
    cfg = Config(base=base, instance_id="slot-" + os.environ["SPI_SLOT"])
    routed = ssc.route_settings_config(cfg)
    assert routed is not cfg, "route did not apply"
    for i in range(int(os.environ["SPI_ITER"])):
        routed.set(os.environ["SPI_KEY"], i)
        assert routed.save() is True, "routed save failed"
    print("DONE", flush=True)
else:
    print("BAD-MODE", flush=True)
    sys.exit(2)
"""


class _Child:
    """真子进程 + **有界**通信（stdout/err 各一个读线程，全部等待带超时）。

    沿用 ``tests/test_config_write_lock.py`` 的纪律：不用阻塞 ``readline`` 等 token
    （子进程不说话就能把整套测试挂死），超时立刻 kill 并带诊断失败。
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._lines: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        try:
            for line in self.proc.stdout:
                self._lines.put(line)
        except Exception:  # noqa: BLE001 - 读线程只负责转送
            pass
        finally:
            self._lines.put(None)

    def _pump_stderr(self) -> None:
        try:
            for line in self.proc.stderr:
                self._stderr.append(line)
        except Exception:  # noqa: BLE001
            pass

    @property
    def stderr(self) -> str:
        return "".join(self._stderr)

    def wait_token(self, token: str, timeout: float = WAIT) -> None:
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.kill()
                raise AssertionError(
                    f"子进程未在 {timeout}s 内输出 {token}（已见 {seen}）"
                    f"stderr={self.stderr[:500]!r}")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise AssertionError(
                    f"子进程输出结束仍未出现 {token}（已见 {seen}，退出码 "
                    f"{self.proc.poll()}）stderr={self.stderr[:500]!r}")
            text = line.strip()
            seen.append(text)
            if text == token:
                return

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            pass


def _spawn(mode: str, **env) -> _Child:
    payload = {
        "SPI_REPO": REPO,
        "SPI_MODE": mode,
        "PYTHONPATH": REPO,
        "PYTHONIOENCODING": "utf-8",
        **{key: str(value) for key, value in env.items()},
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", env={**os.environ, **payload},
        cwd=REPO,          # 换目录也要成立：脚本自己按 SPI_REPO 注入 sys.path
    )
    return _Child(proc)


def _child_reports(children) -> str:
    return "子进程 stderr: " + " | ".join(child.stderr[-400:] for child in children)


@pytest.fixture(autouse=True)
def _overlay_topology_env(monkeypatch):
    """本文件测的是 overlay 拓扑下的 slot 身份路由（判定与磁盘/父进程陈述无关）。"""
    monkeypatch.delenv(cmd_mod.ENV_TOPOLOGY, raising=False)
    monkeypatch.delenv(cmd_mod.ENV_PARENT_TOPOLOGY, raising=False)
    monkeypatch.delenv("DSH_PET_INSTANCE", raising=False)
    cmd_mod.reset_topology_snapshot()
    yield
    cmd_mod.reset_topology_snapshot()


def _main_config(tmp_path, **values) -> Config:
    main = Config(base=tmp_path)
    for key, value in values.items():
        main.set(key, value)
    assert main.save() is True
    return main


def _routed(tmp_path, slot: int = 1, **values):
    """(路由视图, 主配置, slot 身份 Config)：全部落在 tmp_path（绝不碰用户目录）。"""
    main = _main_config(tmp_path, **values)
    pet_profiles.write_profile_settings(main.dir, slot, {"scale": 0.5})
    cfg = Config(base=tmp_path, instance_id=f"slot-{slot}")
    routed = settings_slot_config.route_settings_config(cfg)
    assert isinstance(routed, SlotRoutedConfig), "前提：overlay 拓扑下的 slot 身份要分流"
    return routed, main, cfg


def _profile_path(main: Config, slot: int = 1) -> Path:
    return pet_profiles.slot_profile_path(main.dir, slot)


def _write_profile(main: Config, slot: int, data: dict) -> None:
    """直接放一份档案（绕过 Config 的落种，便于构造缺键/坏值现场）。"""
    path = _profile_path(main, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ 1. 读：同源默认
def test_missing_per_pet_key_reads_the_same_source_default_not_the_main_pet(tmp_path):
    """档案里缺键 → ``Config`` 出厂默认，**不是**主宠当前值。

    旧口径（``get`` 的兜底分支）把缺键回落主配置，于是"把这宠的偏好重置掉"之后，页面
    立刻又显示出主宠的值——重置看起来没生效，子宠成了主宠设置的持续继承者。
    """
    routed, main, _cfg = _routed(tmp_path, slot=1, scale=0.5, self_talk_enabled=True)
    _write_profile(main, 1, {"version": 4, "rx": 0.25})

    assert pet_profiles.same_source_default("scale") != 0.5, "前提：默认值 != 主宠值"
    assert routed.get("scale") == pytest.approx(pet_profiles.same_source_default("scale"))
    assert routed.get("self_talk_enabled") is pet_profiles.same_source_default(
        "self_talk_enabled")


def test_profile_value_wins_over_the_same_source_default(tmp_path):
    """非回归：档案里有合法值就优先（同源默认只在缺键时兜底）。"""
    routed, main, _cfg = _routed(tmp_path, slot=1, scale=0.5)
    assert pet_profiles.write_profile_settings(
        main.dir, 1, {"scale": 1.1}, user_customized=True).status == "ok"

    assert routed.get("scale") == pytest.approx(1.1)


def test_corrupt_profile_reads_defaults_instead_of_inheriting_the_main_pet(tmp_path):
    """坏档案：读侧按同源默认（不假装"键在主配置里"），写侧拒绝覆盖（见下一条）。"""
    routed, main, _cfg = _routed(tmp_path, slot=1, scale=0.5)
    _profile_path(main).write_text("{ broken", encoding="utf-8")

    assert routed.get("scale") == pytest.approx(pet_profiles.same_source_default("scale"))


# ------------------------------------------------------------------ 2. 写：一批 + 失败不丢草稿
def test_routed_save_batches_all_pending_keys_into_one_profile_write(
        tmp_path, monkeypatch):
    """一次保存 = **一次**锁内合并写（不是逐键裸写），且不再走旧单键写。

    旧实现逐键 ``overlay_spawn_state.write_slot_setting``：每个键各自"读整份 → 改一个
    键 → 原子替换"，没有锁、N 个键 N 次 replace；并发写者（壳的几何自存 / 另一个设置
    进程）会被整表覆盖吞掉。
    """
    routed, main, _cfg = _routed(tmp_path, slot=1)

    def _legacy_write(*_args, **_kwargs):
        raise AssertionError("路由的落盘不得再逐键调用 write_slot_setting")

    from pet import overlay_spawn_state

    monkeypatch.setattr(overlay_spawn_state, "write_slot_setting", _legacy_write)
    writes: list[Path] = []
    real_write = cwl.atomic_write_json

    def spy(path, data):
        writes.append(Path(path))
        return real_write(path, data)

    monkeypatch.setattr(cwl, "atomic_write_json", spy)
    routed.set("scale", 1.2)
    routed.set("self_talk_enabled", True)
    routed.set("user_customized", True)

    assert routed.save() is True

    assert writes == [_profile_path(main)], (
        f"三个键必须合成一次档案写入，实际 {writes}")
    on_disk = json.loads(_profile_path(main).read_text(encoding="utf-8"))
    assert on_disk["scale"] == pytest.approx(1.2)
    assert on_disk["self_talk_enabled"] is True
    assert on_disk["user_customized"] is True


def test_corrupt_profile_is_not_overwritten_and_the_draft_survives(tmp_path):
    """坏档案：一个字节不覆盖、``save()`` 返回 False、草稿留在 pending 里。

    旧实现把坏文件当空 dict 读，然后按这份"空"整表重写：用户手改坏的档案被静默销毁
    成只剩这次保存的一两个键，而 ``save()`` 还报成功。
    """
    routed, main, _cfg = _routed(tmp_path, slot=1)
    path = _profile_path(main)
    path.write_text("{ broken", encoding="utf-8")
    before = path.read_bytes()

    routed.set("scale", 1.4)
    assert routed.save() is False, "坏档案不得被当成空档案覆盖"
    assert path.read_bytes() == before, "坏档案必须原样留着"

    path.write_text('{"version": 4}', encoding="utf-8")
    assert routed.save() is True, "草稿必须还在：修好档案后一次保存就能落盘"
    assert json.loads(path.read_text(encoding="utf-8"))["scale"] == pytest.approx(1.4)


def test_bad_utf8_profile_is_corrupt_and_never_overwritten(tmp_path):
    """非法 UTF-8 的档案：按损坏处理（不抛 UnicodeDecodeError），也不覆盖。"""
    routed, main, _cfg = _routed(tmp_path, slot=1)
    path = _profile_path(main)
    path.write_bytes(b'{"scale": 1.0, "note": "\xff\xfe bad bytes"}')
    before = path.read_bytes()

    assert routed.get("scale") == pytest.approx(pet_profiles.same_source_default("scale"))
    routed.set("scale", 0.9)
    assert routed.save() is False
    assert path.read_bytes() == before


def test_pending_is_not_cleared_when_the_directory_lock_is_held(tmp_path):
    """锁被别的进程占住：``save()`` 返回 False（不谎称成功），pending 不丢。

    真子进程持锁（不是同进程假锁）；超时后不改盘、不吞这次编辑，释放后一次保存落盘。
    """
    routed, main, _cfg = _routed(tmp_path, slot=1)
    path = _profile_path(main)
    before = path.read_bytes()
    release = tmp_path / "release.sentinel"
    child = _spawn("hold", SPI_DIR=str(main.dir), SPI_RELEASE=str(release))
    try:
        child.wait_token("LOCKED")
        routed.set("scale", 1.3)
        assert routed.save() is False, "拿不到目录写锁时不得降级成无锁写入"
        assert path.read_bytes() == before, "失败的那个出口一个字节都不写"
    finally:
        release.write_text("go", encoding="utf-8")
        child.wait_token("RELEASING")
        child.kill()

    assert routed.save() is True, "草稿必须还在"
    assert json.loads(path.read_text(encoding="utf-8"))["scale"] == pytest.approx(1.3)


def test_main_flush_lock_timeout_keeps_pending_and_memory(tmp_path):
    """主配置那一路同样在目录锁内 reload→重放→save；失败保 pending、内存不假同步。"""
    routed, main, _cfg = _routed(tmp_path, slot=1, pet_opacity=55)
    release = tmp_path / "release.sentinel"
    child = _spawn("hold", SPI_DIR=str(main.dir), SPI_RELEASE=str(release))
    try:
        child.wait_token("LOCKED")
        routed.set("pet_opacity", 30)
        assert routed.save() is False
        assert json.loads(main.path.read_text(encoding="utf-8"))["pet_opacity"] == 55
        assert routed.get("pet_opacity") == 30, "内存里仍是用户刚设的值"
    finally:
        release.write_text("go", encoding="utf-8")
        child.wait_token("RELEASING")
        child.kill()

    assert routed.save() is True
    assert json.loads(main.path.read_text(encoding="utf-8"))["pet_opacity"] == 30


# ------------------------------------------------------------------ 3. 跨进程：异键不丢
def test_cross_process_routed_writes_of_distinct_profile_keys_all_survive(tmp_path):
    """两个真进程各写**不同**的每宠键（各 12 轮）：谁的键都不许被整表覆盖吞掉。"""
    _routed(tmp_path, slot=1)  # 前置：主配置 + 落种
    config_dir = Config(base=tmp_path).dir
    children = [
        _spawn("routed", SPI_DIR=str(config_dir), SPI_SLOT="1",
               SPI_KEY="playback_speed", SPI_ITER="12"),
        _spawn("routed", SPI_DIR=str(config_dir), SPI_SLOT="1",
               SPI_KEY="bubble_text_scale", SPI_ITER="12"),
    ]
    try:
        for child in children:
            child.wait_token("DONE")
    finally:
        for child in children:
            child.kill()

    profile = pet_profiles.read_slot_profile(config_dir, 1)
    assert profile.status == "ok"
    assert profile.data["playback_speed"] == 11, _child_reports(children)
    assert profile.data["bubble_text_scale"] == 11, _child_reports(children)
    assert profile.data["version"] == pet_profiles.PROFILE_FILE_VERSION


# ------------------------------------------------------------------ 4. 身份边界：slot 0 / 128
@pytest.mark.parametrize("slot", [0, pet_profiles.SLOT_MAX])
def test_route_writes_the_profile_of_slot_0_and_slot_128(tmp_path, slot):
    """0 与 128 都是合法档案身份（0 加入后上界不缩短），各写各的文件、主配置不动。"""
    routed, main, cfg = _routed(tmp_path, slot=slot, pet_opacity=55)
    assert cfg.path == _profile_path(main, slot), "档案身份 = config-slot-N.json"
    before_main = main.path.read_bytes()

    routed.set("scale", 1.25)
    routed.set("self_talk_enabled", True)
    assert routed.save() is True

    profile = pet_profiles.read_slot_profile(main.dir, slot)
    assert profile.exists is True
    assert profile.value("scale") == pytest.approx(1.25)
    assert profile.value("self_talk_enabled") is True
    assert main.path.read_bytes() == before_main, "每宠键不许污染主配置"


def test_slot_128_keeps_the_keys_that_were_already_in_its_profile(tmp_path):
    """档案是"合并写"：落种进档案的其它键（如窗口级键）不被这次保存整表抹掉。"""
    routed, main, _cfg = _routed(tmp_path, slot=pet_profiles.SLOT_MAX)
    path = _profile_path(main, pet_profiles.SLOT_MAX)
    seeded = json.loads(path.read_text(encoding="utf-8"))
    seeded["rx"] = 0.37
    path.write_text(json.dumps(seeded), encoding="utf8")

    routed.set("scale", 1.25)
    assert routed.save() is True

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["rx"] == seeded["rx"]
    assert "pet_opacity" not in on_disk
    assert on_disk["scale"] == pytest.approx(1.25)


# ------------------------------------------------------------------ 5. reset：保位置、保别宠
def _dirty_profile(main: Config, slot: int = 1) -> None:
    """给某只宠一份"用户改过"的档案：偏好 + 位置 + 本地旗标。"""
    assert pet_profiles.write_profile_settings(
        main.dir, slot,
        {"scale": 1.6, "self_talk_enabled": True, "character": "shenshen",
         "playback_speed": 2.0},
        user_customized=True).status == "ok"
    assert pet_profiles.write_profile_geometry(
        main.dir, slot,
        {"rx": 0.25, "ry": 0.4, "screen_name": "primary", "facing": "right",
         "scale": 1.6}).status == "ok"


def test_slot_reset_keeps_position_and_returns_preferences_to_defaults(tmp_path):
    """本宠 reset：保 ``rx/ry/screen_name``、偏好（含 facing）回同源默认、别宠/全局不变。"""
    routed, main, cfg = _routed(tmp_path, slot=1)
    _dirty_profile(main, 1)
    _dirty_profile(main, 2)
    slot2_before = _profile_path(main, 2).read_bytes()
    main_before = main.path.read_bytes()
    old_generation = pet_profiles.slot_generation(main.dir, 1)

    assert routed.reset_to_defaults() is True

    profile = pet_profiles.read_slot_profile(main.dir, 1)
    assert (profile.data["rx"], profile.data["ry"]) == (0.25, 0.4), "位置必须保留"
    assert profile.data["screen_name"] == "primary"
    assert "scale" not in profile.data and "self_talk_enabled" not in profile.data
    assert "facing" not in profile.data, "朝向是偏好不是位置：回默认 left"
    assert profile.value("facing") == "left"
    assert profile.value("scale") == pytest.approx(pet_profiles.same_source_default("scale"))
    assert profile.data["user_customized"] is True, "本地旗标不是偏好值，原样保留"
    assert routed.get("scale") == pytest.approx(
        pet_profiles.same_source_default("scale")), "读侧立刻回同源默认"
    assert cfg.data["scale"] == pytest.approx(
        pet_profiles.same_source_default("scale")), "slot 身份那份内存视图同步"
    assert pet_profiles.slot_generation(main.dir, 1) == old_generation + 1, "代次 +1"
    assert _profile_path(main, 2).read_bytes() == slot2_before, "别宠一个字节都不动"
    assert main.path.read_bytes() == main_before, "全局配置一个字节都不动"


def test_slot_reset_invalidates_late_geometry_writes_from_the_old_generation(tmp_path):
    """reset 后，迟到（上一代）的几何自存必须被代次门挡掉。"""
    routed, main, _cfg = _routed(tmp_path, slot=1)
    _dirty_profile(main, 1)
    old_generation = pet_profiles.slot_generation(main.dir, 1)

    assert routed.reset_to_defaults() is True

    late = pet_profiles.write_profile_geometry(
        main.dir, 1, {"scale": 1.9}, generation=old_generation)

    assert late.status == "stale-generation", late
    assert pet_profiles.read_slot_profile(main.dir, 1).value(
        "scale") == pytest.approx(pet_profiles.same_source_default("scale"))


def test_slot_reset_second_attempt_converges_after_a_partial_write(tmp_path, monkeypatch):
    """``partial``：返回 False、代次已推进、草稿保住，**再点一次**能收敛（缓存对齐）。

    档案写失败时存储层已经推进了代次；若调用方拿的是自己缓存的旧代次，第二次 reset
    会被"stale-generation"挡在门外——用户永远重置不成功。快照必须每次重取。
    """
    routed, main, _cfg = _routed(tmp_path, slot=1)
    _dirty_profile(main, 1)
    old_generation = pet_profiles.slot_generation(main.dir, 1)
    path = _profile_path(main)
    state = {"fail": True}
    real_write = cwl.atomic_write_json

    def flaky(target, data):
        if state["fail"] and Path(target) == path:
            raise cwl.AtomicWriteFailed("磁盘满")
        return real_write(target, data)

    monkeypatch.setattr(cwl, "atomic_write_json", flaky)
    routed.set("scale", 1.5)          # 用户改过、还没保存的草稿

    assert routed.reset_to_defaults() is False, "partial 不得谎称成功"
    assert pet_profiles.slot_generation(main.dir, 1) == old_generation + 1
    assert pet_profiles.read_slot_profile(main.dir, 1).value(
        "scale") == pytest.approx(1.6), "档案未被重置（partial 的真实状态）"

    state["fail"] = False
    assert routed.save() is False, "旧草稿不能跨越reset代次"
    assert routed._pending_slot["scale"] == pytest.approx(1.5)

    assert routed.reset_to_defaults() is True, "代次缓存必须及时对齐，第二次能收敛"
    assert pet_profiles.read_slot_profile(main.dir, 1).value(
        "scale") == pytest.approx(pet_profiles.same_source_default("scale"))


def test_slot_reset_without_a_profile_reports_success_and_advances_generation(tmp_path):
    """没有档案可重置 = 本来就在默认值上：如实当成功（关窗），但代次照样推进。"""
    routed, main, _cfg = _routed(tmp_path, slot=3)
    path = _profile_path(main, 3)
    path.unlink(missing_ok=True)
    old_generation = pet_profiles.slot_generation(main.dir, 3)

    assert routed.reset_to_defaults() is True

    assert not path.exists(), "不凭空造一份档案"
    assert pet_profiles.slot_generation(main.dir, 3) == old_generation + 1, (
        "代次必须推进：迟到的旧几何写要被挡掉")


def test_slot_reset_keeps_the_draft_when_the_profile_is_corrupt(tmp_path):
    """坏档案：reset 拒绝（不清理、不覆盖），返回 False 且 pending 草稿不丢。"""
    routed, main, _cfg = _routed(tmp_path, slot=1)
    path = _profile_path(main)
    path.write_text("{ broken", encoding="utf-8")
    before = path.read_bytes()
    routed.set("scale", 1.7)

    assert routed.reset_to_defaults() is False
    assert path.read_bytes() == before, "坏档案照样不许被 reset 顺手覆盖"

    path.write_text('{"version": 4}', encoding="utf-8")
    assert routed.save() is True, "草稿仍在"
    assert json.loads(path.read_text(encoding="utf-8"))["scale"] == pytest.approx(1.7)


def test_routed_main_dict_edit_preserves_external_other_leaf(tmp_path):
    routed, main, _cfg = _routed(tmp_path)
    island = dict(routed.get('dynamic_island'))
    island['opacity'] = 0.7
    routed.set('dynamic_island', island)
    external = Config(base=tmp_path)
    external.data['dynamic_island']['accent'] = 'orange'
    assert external.save()
    assert routed.save()
    raw = json.loads(main.path.read_text(encoding='utf-8'))
    assert raw['dynamic_island']['opacity'] == 0.7
    assert raw['dynamic_island']['accent'] == 'orange'


def test_routed_old_pending_does_not_cross_global_reset(tmp_path):
    routed, main, _cfg = _routed(tmp_path)
    routed.set('weather_city', 'old draft')
    assert main.reset_to_defaults()
    before = main.path.read_bytes()
    routed.reload()
    assert routed.save() is False
    assert main.path.read_bytes() == before
    assert routed._pending_main['weather_city'] == 'old draft'


# ------------------------------------------------------------------ 6. 键集单一来源
def test_routed_key_set_matches_the_profile_key_set():
    """路由键集必须与档案层的每宠键集**逐字相同**（漂移就是 refused-key / 死开关）。"""
    assert settings_slot_config.SLOT_ROUTED_KEYS == pet_profiles.PER_PET_KEYS
    assert settings_slot_config.SLOT_LOCAL_KEYS <= pet_profiles.SLOT_LOCAL_KEYS


# ------------------------------------------------------------------ 7. 几何自存：自己写的必须自己看得见
def test_geometry_write_does_not_freeze_the_slot_snapshot(tmp_path):
    """#304：运行期视图**自己**写下的几何，``reload()`` 之后不许被旧快照冻住。

    现场：``@app.py _apply_legacy_profile`` 每次档案变动都会 ``reload()``。待写草稿
    （这里是 ``window.change_scale`` 造出的 ``user_customized``）按「表单显示口径」让
    ``_invalidate_slot_snapshot`` 故意保留快照，而 ``save_geometry`` 是**直写档案**的
    旁路，写完不清草稿也不作废快照——于是 reload 也解不开那份旧快照，``get('scale')``
    读到的是改大小**之前**的尺寸，窗口照着它弹回去（用户看到"闪一下"）。
    """
    routed, _main, _cfg = _routed(tmp_path, slot=1)
    generation = routed.profile_generation()
    routed.set("user_customized", True)

    assert routed.save_geometry({"scale": 0.92}, generation) is True

    routed.reload()
    assert routed.get("scale") == pytest.approx(0.92)


def test_geometry_write_keeps_the_drafts_of_the_open_form(tmp_path):
    """上一条的边界：刷新读视图**不许**动用户草稿，也不许洗掉编辑代次基线。"""
    routed, _main, _cfg = _routed(tmp_path, slot=1)
    generation = routed.profile_generation()
    routed.set("scale", 1.3)
    pinned = routed._pinned_edit_generation

    assert routed.save_geometry({"rx": 0.4}, generation) is True

    assert routed.get("scale") == pytest.approx(1.3), "未保存的草稿优先于盘上的几何"
    assert routed._pinned_edit_generation == pinned, "编辑代次基线是表单的，不许被刷新洗掉"
