# -*- coding: utf-8 -*-
"""组件**装回来 / 删掉 / 换内容 / 退订重订 / stop→start** 的行为守护（A07 / A08 / O1）。

三条纪律都落在同一个前提上——**清单没变，但运行期的来源状态变了**：

1. **安装面失效（A07）**：扫描签名的依据是「清单版本 + **provider 文件版本**」。
   清单不动、只把 provider 装回来 / 删掉 / 整体改写，运行中的进程必须看得见，
   而且**不许靠** ``reset_cache()``——那是测试的拐杖，用户装组件时不会有人替他调；
2. **每段订阅代次（A08）**：退订 → 重订算**新序列**，退订前那次在飞的 ``read``
   回来不许冒充新序列的第一拍。全局 ``epoch`` 拦不住它（它带着当刻 epoch、状态还是
   ok，宿主会把它当成"新序列的第一拍"收下并推进告警）；解析结果与 provider 文件
   版本绑定，文件删了 / 改了必须丢掉缓存的 callable；
3. **线程有界（A08/O1）**：``stop()`` 之后旧 worker 还卡在 ``read`` 里时，``start()``
   不许再起第二条 worker（Python 杀不掉阻塞中的线程，如实承认），但要能**接续**同
   一条线程继续采新订阅——不能"``start()`` 返回 False 之后就永远停"；provider 模块
   缓存按路径只留最新版本、总量有上限（不随用户编辑组件的次数无界增长）。

时序纪律：真实线程 + 真实 ``Event``（判定点等 Event，宽预算），不赌固定 sleep；
``_tick()`` 这种同步面直接调（采样线程跑的就是它）。
"""
from __future__ import annotations

import threading

import pytest

from pet.island import extension_host as host
from pet.island import segments as isl
from tests.test_island_extension_host import (
    PROVIDER, _wait_for, _write_component,
)

THREAD_NAME = host.THREAD_NAME

#: 只把 provider 整体换成"固定返回 777"（长度与默认 provider 不同，是一次真实改写）。
PROVIDER_REWRITTEN = (
    "def read_celsius():\n"
    "    return 777\n\n\n"
    "def format_celsius(value):\n"
    "    return str(value)\n"
)


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """每个用例一份独立可信目录（环境变量覆盖）+ 干净的进程级缓存。"""
    root = tmp_path / "island-extensions"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(host.ENV_DIRS, str(root))
    host.reset_cache()
    yield root
    host.reset_cache()


# ---------------------------------------------------------------- 闸门 provider（真实线程 + 真实 Event）
class _Gate:
    """第 N 次 ``read`` 的闸门：``entered`` 表示已进 read，``release`` 由测试放行。"""

    def __init__(self, value):
        self.value = value
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()


class _GatedProvider:
    """``read`` 每次调用先记在飞计数、再等闸门（闸门由测试持有）。

    第 N 次调用用第 N 个闸门，超出之后复用最后一个——所以"后面几拍"在测试放行过
    最后一次闸门之后就不再阻塞，用例不用为余下的节拍铺闸门。
    """

    def __init__(self, *values):
        self.gates = [_Gate(value) for value in values]
        self.lock = threading.Lock()
        self.calls = 0
        self.inflight = 0
        self.max_inflight = 0

    def read(self):
        with self.lock:
            self.calls += 1
            gate = self.gates[min(self.calls - 1, len(self.gates) - 1)]
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        gate.entered.set()
        try:
            gate.release.wait(30.0)          # 宽预算：用例异常时也不留挂死的线程
        finally:
            with self.lock:
                self.inflight -= 1
            gate.returned.set()
        return gate.value

    @staticmethod
    def format_value(value):
        """数值 → 文案（与内置段同款取整，岛上盒宽/文案断言按它写）。"""
        return f"{round(float(value))}"

    def declaration(self, segment_id: str = "a") -> isl.SegmentDeclaration:
        """这一段的可采样声明（数据源就是本闸门 provider）——注入解析器用。"""
        return isl.SegmentDeclaration(
            id=segment_id, format_value=self.format_value, max_text="99", read=self.read)

    def release_all(self) -> None:
        for gate in self.gates:
            gate.release.set()


def _gated_sampler(providers: dict, *, interval: float = 0.1) -> host.ExtensionSampler:
    """把闸门 provider 当成段的数据源注入采样器（不 import 组件文件、不起替身线程）。"""
    declarations = {segment_id: provider.declaration(segment_id)
                    for segment_id, provider in providers.items()}
    return host.ExtensionSampler(
        interval=interval, resolver=lambda segment_id: declarations[segment_id])


def _live_workers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate()
            if thread.name == THREAD_NAME and thread.is_alive()]


# ---------------------------------------------------------------- A07：清单不变，来源变了
def test_installing_the_provider_file_alone_is_seen_without_resetting_the_cache(_clean_state):
    """清单先落、provider 稍后装回来：**不许要求手工 reset_cache 才看得见**。"""
    root = _clean_state
    directory = _write_component(root, provider=None, reset_cache=False)
    first = host.declaration_for("cpu_temp")
    assert first is not None and first.provider_present is False
    assert host.declared_provider_missing("cpu_temp") != ""

    (directory / "provider.py").write_text(PROVIDER, encoding="utf-8")   # 只把文件装回来

    again = host.declaration_for("cpu_temp")
    assert again is not None, "清单还在，声明不该消失"
    assert again.provider_present is True, \
        "provider 装回来了，下一次扫描就得看见（这正是「文件回来即恢复」的承诺）"
    assert host.declared_provider_missing("cpu_temp") == ""
    assert "cpu_temp" in host.declared_metric_ids()


def test_removing_the_provider_file_stops_executing_the_cached_callable(_clean_state):
    """清单不变、provider 被删：已经解析过的段不许继续跑那个文件的旧代码。"""
    directory = _write_component(_clean_state, reset_cache=False)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler._tick()                                   # 采样线程跑的就是这个方法
        assert sampler.snapshot()["cpu_temp"].text == "74"

        (directory / "provider.py").unlink()              # 只删 provider，清单不动
        sampler._tick()

        sample = sampler.snapshot()["cpu_temp"]
        assert sample.state == isl.STATE_UNSUPPORTED, \
            "文件没了就该如实报「组件暂缺」，而不是继续报旧读数"
        assert "provider.py" in sample.error_reason
    finally:
        sampler.close()


def test_rewriting_the_provider_content_takes_effect_without_resetting_the_cache(_clean_state):
    """清单不变、provider 内容整体换掉：运行中的解析必须换成新代码。"""
    directory = _write_component(_clean_state, reset_cache=False)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler._tick()
        assert sampler.snapshot()["cpu_temp"].text == "74"

        (directory / "provider.py").write_text(PROVIDER_REWRITTEN, encoding="utf-8")
        sampler._tick()

        sample = sampler.snapshot()["cpu_temp"]
        assert sample.text == "777", "改了内容的 provider 必须跑新代码（旧 callable 不许赖着）"
        assert sample.raw_value == 777
    finally:
        sampler.close()


# ---------------------------------------------------------------- A08：每段订阅代次
def test_resubscribing_rejects_the_sample_that_was_in_flight_before_the_unsubscribe():
    """退订 → 重订 = 新序列：退订前那次在飞的 ``read`` 回来不许被当成第一拍。

    真实线程 + 真实 Event：A 的第一次 ``read`` 卡在闸门里，B 保持订阅（线程因此不会
    被收掉——"重订不换代"的前提是线程与 epoch 还活着，不是 B 还在出读数）。放行旧
    ``read`` 之后**必须**等第二拍真的进了 ``read`` 再断言：那一刻旧读数已经走完
    发布路径，断言不会和线程赛跑。
    """
    gated = _GatedProvider(95, 99)
    steady = _GatedProvider(1)
    steady.release_all()
    sampler = _gated_sampler({"a": gated, "b": steady})
    sampler.subscribe("a")
    sampler.subscribe("b")
    try:
        assert sampler.start() is True
        assert gated.gates[0].entered.wait(10.0), "第一拍没能进组件的 read"
        assert sampler.subscriber_count == 2
        epoch_before = sampler.epoch

        sampler.unsubscribe("a")
        sampler.subscribe("a")
        assert sampler.epoch == epoch_before, "退订/重订本身不换代（宿主靠每段 token 判序列）"
        gated.gates[0].release.set()                      # 退订前那次 read 现在才返回
        assert gated.gates[1].entered.wait(10.0), "线程没有继续跑下一拍"

        assert "a" not in sampler.snapshot(), \
            "退订前那次在飞的读数不许冒充新序列的第一拍"
        gated.gates[1].release.set()
        assert _wait_for(lambda: sampler.snapshot().get("a") is not None, timeout=10.0), \
            "新序列的第一拍没到（重订之后这一段就再也没读数了）"
        assert sampler.snapshot()["a"].text == "99"
    finally:
        gated.release_all()
        steady.release_all()
        sampler.close()


# ---------------------------------------------------------------- A08：线程有界 + 接续
def test_stop_then_start_never_runs_two_workers_of_the_same_provider():
    """``stop()`` 杀不掉阻塞中的 read：那就不许再起第二条 worker，但必须能接续。

    负向断言（"这段时间里不许出现并发 2"）用宽预算；正向断言用 Event——放行旧 read
    之后同一条线程要接着采新订阅，否则就是"``start()`` 之后永远停"。
    """
    gated = _GatedProvider(1, 2)
    sampler = _gated_sampler({"a": gated})
    sampler.subscribe("a")
    try:
        assert sampler.start() is True
        assert gated.gates[0].entered.wait(10.0), "第一拍没能进组件的 read"
        first_epoch = sampler.epoch

        sampler.stop()                                    # 旧 read 还在飞
        assert sampler._thread is None, "stop 先摘掉线程引用（不吊住调用方）"
        assert sampler.running is False
        assert gated.inflight == 1, "stop 杀不掉阻塞中的线程（如实，不假装）"

        assert sampler.start() is True, \
            "stop→start 必须能接续（返回 False 之后永远停 = 用户重开这一段也没读数）"
        assert _wait_for(lambda: gated.max_inflight >= 2, timeout=1.0) is False, \
            "旧 worker 还卡在同一个 provider 里时，start 不许再起第二条 worker"

        gated.release_all()
        assert _wait_for(lambda: (sampler.snapshot().get("a") is not None
                                  and sampler.snapshot()["a"].epoch == sampler.epoch),
                         timeout=10.0), "接续之后的第一个新样本没到"
        assert sampler.epoch != first_epoch, "接续要换代（旧代在途结果一律作废）"
        assert gated.max_inflight == 1, "同时只有一个 worker 在跑这个 provider"
        assert len(_live_workers()) == 1, "线程数有界：一条采样线程"
    finally:
        gated.release_all()
        sampler.close()
    assert _wait_for(lambda: not _live_workers(), timeout=5.0), "close 之后不许留残留线程"


def test_stop_then_start_resumes_sampling_when_nothing_is_blocked():
    """worker 停在等节拍那里时 ``stop()`` → ``start()``：接回来的那条要继续出读数。

    这是隐藏→显示、折叠→展开的真实顺序（worker 没卡在 ``read`` 里）。若采纳了"已经
    决定退出"的线程，采样器会看起来在跑、其实永远不采——读数停在 ``--``，而 ``start()``
    返回的还是 True，调用方无从察觉。
    """
    gated = _GatedProvider(7)
    gated.release_all()
    sampler = _gated_sampler({"a": gated}, interval=0.05)
    sampler.subscribe("a")
    try:
        assert sampler.start() is True
        assert _wait_for(lambda: sampler.snapshot().get("a") is not None, timeout=10.0)
        first_epoch = sampler.epoch

        sampler.stop()                       # 紧接着 start：worker 还没来得及退出循环
        assert sampler.start() is True
        assert sampler.epoch != first_epoch
        assert _wait_for(lambda: (sampler.snapshot().get("a") is not None
                                  and sampler.snapshot()["a"].epoch == sampler.epoch),
                         timeout=10.0), "立刻 stop→start 之后必须继续出读数"
        assert sampler.running is True
        assert len(_live_workers()) == 1, "接回来的是同一条线程（不是第二条）"
    finally:
        gated.release_all()
        sampler.close()
    assert _wait_for(lambda: not _live_workers(), timeout=5.0)


# ---------------------------------------------------------------- O1：provider 缓存有界
def test_provider_cache_keeps_only_the_newest_version_of_one_file(_clean_state):
    """同一个 provider 文件被改过多次：只留最新版本的模块（不随编辑次数增长）。"""
    directory = _write_component(_clean_state)
    path = directory / "provider.py"
    for value in (11, 222, 3333, 44444, 555555):
        path.write_text(
            "def read_celsius():\n"
            f"    return {value}\n\n\n"
            "def format_celsius(value):\n"
            "    return str(value)\n",
            encoding="utf-8")
        read, _format = host.load_provider(host.declaration_for("cpu_temp"))
        assert read() == value, "改了内容就得 import 新版本"
    keys = [key for key in host._PROVIDER_CACHE if key[0] == str(path)]
    assert len(keys) == 1, "同一路径只留最新版本——改一次多一条就是随编辑次数无界增长"


def test_provider_cache_is_bounded_across_many_component_files(_clean_state, monkeypatch):
    """多个组件文件：provider 缓存总量有上限，淘汰最久没用到的那个。"""
    monkeypatch.setattr(host, "PROVIDER_CACHE_LIMIT", 3)
    for index in range(5):
        _write_component(_clean_state, f"component_{index}",
                         manifest=dict(id=f"probe_{index}", label="探头",
                                       max_text="99", provider="provider.py",
                                       read="read_celsius", format="format_celsius"),
                         reset_cache=False)
        host.load_provider(host.declaration_for(f"probe_{index}"))
    assert len(host._PROVIDER_CACHE) <= 3, "provider 缓存必须有上限"
    newest = str(_clean_state / "component_4" / "provider.py")
    assert newest in [key[0] for key in host._PROVIDER_CACHE], "最新用到的那个不许被淘汰"
