# -*- coding: utf-8 -*-
"""第三方灵动岛组件的**稳定声明源**与**有界后台采样**（``pet/island_extension_host.py``）。

本文件守护四类面：

1. **静态声明**：可信目录里的标准库 JSON 清单 → 声明；非法清单（坏 JSON / 保留 id /
   多余字段 / provider 爬出目录 / 缺 ``max_text`` / id 重复）一律**明确拒收并留痕**，
  不半信半疑地加载；
2. **配置准入同源**：``allowed_metric_ids`` = 内置 ∪ 已声明（设置进程与主进程读同一份
   静态 metadata，**不需要**运行期注册）；
3. **设置进程只读**：只调 ``declarations()`` / ``allowed_metric_ids()`` 时，组件的
   provider **一次都不会被 import**（用一个"import 就写标记文件"的 provider 机器化验证）；
4. **采样边界**：原始值 + 状态 + monotonic 时刻 + 代次；格式化护栏（异常 / 巨型串 /
   多行 / None）；慢读熔断（不声称能杀线程，只停止再调它）；0 订阅零调用；一条线程
   管全部组件；退订后迟到结果不复活。

时序纪律：真实线程用**事件同步 + 宽预算**轮询（不赌固定 sleep），替身路径同步直调。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from pet.island import extension_host as host
from pet.island import segments as isl

READ_LOG = "read.log"
IMPORT_FLAG = "imported.flag"

MANIFEST = {
    "id": "cpu_temp",
    "label": "温度",
    "max_text": "88",
    "unit": "°C",
    "unit_full": "摄氏度",
    "source": "测试传感器",
    "detail": "CPU 封装温度",
    "thresholds": [70, 85],
    "provider": "provider.py",
    "read": "read_celsius",
    "format": "format_celsius",
}

#: 组件 provider：每次取值往组件目录追一行（跨进程可验），格式化函数故意朴素。
PROVIDER = '''
import json
import time
from pathlib import Path

VALUE = {"celsius": 74.0}


def _log(event):
    with Path(__file__).with_name("read.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\\n")


def read_celsius():
    _log({"t": time.monotonic()})
    return VALUE["celsius"]


def format_celsius(value):
    if value is None:
        return "--"
    return f"{round(float(value))}"
'''

#: "一 import 就写标记"的 provider：用来验证设置进程/静态路径**不执行**组件代码。
MARKER_PROVIDER = '''
from pathlib import Path

Path(__file__).with_name("imported.flag").write_text("1", encoding="utf-8")


def read_value():
    return 1


def format_value(value):
    return str(value)
'''


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """每个用例：一份独立可信目录（环境变量覆盖）+ 干净的进程级缓存与段注册表。"""
    root = tmp_path / "island-extensions"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(host.ENV_DIRS, str(root))
    host.reset_cache()
    isl.clear_segments()
    yield root
    for segment_id in [key for key in isl.ALERT_THRESHOLDS
                       if key not in isl.BUILTIN_SEGMENT_IDS]:
        isl.drop_thresholds(segment_id)
        isl.ALERT_THRESHOLDS.pop(segment_id, None)
    isl.clear_segments()
    host.reset_cache()


def _write_component(root: Path, name: str = "temp", *, manifest: dict | None = None,
                     provider: str | None = PROVIDER,
                     provider_name: str | None = None,
                     reset_cache: bool = True) -> Path:
    """在可信目录里落一个组件（清单 + provider），并让扫描缓存失效。

    ``reset_cache=False`` 时**不动**扫描缓存——那是用户真实的"装组件"路径
    （文件落下去，进程照常跑），缓存该不该失效是**产品**的判据，不是测试的拐杖。
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    data = dict(MANIFEST)
    if manifest:
        data.update(manifest)
    if provider_name:
        data["provider"] = provider_name
    (directory / host.MANIFEST_NAME).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if provider is not None:
        (directory / data["provider"]).write_text(provider, encoding="utf-8")
    if reset_cache:
        host.reset_cache()
    return directory


def _read_count(directory: Path) -> int:
    log = directory / READ_LOG
    if not log.is_file():
        return 0
    return len([line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()])


def _wait_for(predicate, *, timeout: float = 8.0) -> bool:
    """事件同步 + 宽预算轮询（不赌固定 sleep：CI 慢 runner 也是这个预算）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- 静态声明
def test_declaration_is_read_from_an_explicit_trusted_directory(_clean_state):
    root = _clean_state
    directory = _write_component(root)
    (declaration,) = host.declarations()
    assert declaration.segment_id == "cpu_temp"
    assert (declaration.label, declaration.max_text, declaration.unit) == ("温度", "88", "°C")
    assert (declaration.unit_full, declaration.source, declaration.detail) == (
        "摄氏度", "测试传感器", "CPU 封装温度")
    assert declaration.thresholds == (70.0, 85.0)
    assert declaration.provider == "provider.py" and declaration.provider_present is True
    assert declaration.directory == directory
    assert host.declared_metric_ids() == frozenset({"cpu_temp"})


def test_allowed_ids_are_builtins_plus_declared_components(_clean_state):
    """配置准入 = 内置 ∪ 已声明；**不看运行期注册表**（两个进程因此同源）。"""
    assert host.allowed_metric_ids() == isl.BUILTIN_SEGMENT_IDS
    _write_component(_clean_state)
    allowed = host.allowed_metric_ids()
    assert isl.BUILTIN_SEGMENT_IDS < allowed and "cpu_temp" in allowed

    isl.register_segment(id="runtime_only", format_value=lambda value: str(value),
                         max_text="88")
    assert "runtime_only" not in host.allowed_metric_ids(), \
        "运行期注册不许让 id 自动变成合法配置成员"


def test_settings_process_path_never_executes_component_code(_clean_state):
    """设置进程只读静态 metadata：声明与准入都不 import provider（机器化验证）。

    provider 的**模块级**代码一执行就写标记文件——静态路径跑完标记必须不在。
    """
    root = _clean_state
    _write_component(root, manifest={"id": "marker", "provider": "provider.py",
                                     "read": "read_value", "format": "format_value"},
                     provider=MARKER_PROVIDER)
    declarations = host.declarations()
    assert [item.segment_id for item in declarations] == ["marker"]
    assert "marker" in host.allowed_metric_ids()
    assert not (declarations[0].directory / IMPORT_FLAG).exists(), \
        "静态路径（设置进程/配置准入）不得执行组件代码"


def test_env_override_replaces_the_default_roots(_clean_state):
    """显式目录一旦给出就**只**扫它：不悄悄多扫别的目录。"""
    assert host.roots() == (_clean_state,)
    assert str(host.project_root()) not in [str(path) for path in host.roots()]


def test_declarations_follow_manifest_changes_without_restart(_clean_state):
    """装 / 卸组件不必重启进程：签名（目录名 + mtime + size）变了就重扫。"""
    root = _clean_state
    assert host.declarations() == ()
    directory = _write_component(root)
    assert [item.segment_id for item in host.declarations()] == ["cpu_temp"]

    (directory / host.MANIFEST_NAME).unlink()
    assert host.declarations() == (), "卸载（清单消失）= 声明消失"


def test_provider_missing_is_reported_as_temporarily_absent(_clean_state):
    """清单在、provider 文件不在 = 组件暂缺（与"provider 坏了"区分）。"""
    root = _clean_state
    _write_component(root, provider=None)
    declaration = host.declaration_for("cpu_temp")
    assert declaration is not None and declaration.provider_present is False
    assert "provider.py" in declaration.missing_reason()
    assert host.declared_provider_missing("cpu_temp") == declaration.missing_reason()
    assert host.declared_provider_missing("cpu") == "", "内置段不走组件那条判据"


# ---------------------------------------------------------------- 拒收面
@pytest.mark.parametrize("manifest,expected", [
    ({"id": "cpu"}, "非法"),                       # 内置保留 id
    ({"max_text": ""}, "非法"),                    # 缺盒宽契约
    ({"provider": "../escape.py"}, "相对路径"),     # provider 爬出组件目录
    ({"provider": "/abs/provider.py"}, "相对路径"),
    ({"provider": ""}, "provider"),
])
def test_invalid_manifests_are_rejected_with_a_reason(_clean_state, manifest, expected):
    _write_component(_clean_state, manifest=manifest, provider=None)
    assert host.declarations() == ()
    rejected = host.rejected()
    assert len(rejected) == 1 and expected in rejected[0].reason


def test_unknown_manifest_field_is_rejected(_clean_state):
    """拼错的字段名不许静默丢弃（与 ``register_segment`` 同一条纪律）。"""
    _write_component(_clean_state, manifest={"max_txt": "88"})
    assert host.declarations() == ()
    assert "未知字段" in host.rejected()[0].reason


def test_broken_json_manifest_is_rejected(_clean_state):
    root = _clean_state
    directory = root / "broken"
    directory.mkdir()
    (directory / host.MANIFEST_NAME).write_text("{不是 JSON", encoding="utf-8")
    host.reset_cache()
    assert host.declarations() == ()
    assert "JSON" in host.rejected()[0].reason


def test_duplicate_ids_keep_the_first_component(_clean_state):
    """同名 id 先到先得（用户目录在前），第二个如实记成拒收。"""
    root = _clean_state
    _write_component(root, "first")
    _write_component(root, "second", manifest={"label": "另一个温度"})
    assert [item.label for item in host.declarations()] == ["温度"]
    assert len(host.rejected()) == 1 and "重复" in host.rejected()[0].reason


def test_directory_without_a_manifest_is_ignored(_clean_state):
    (_clean_state / "noise").mkdir()
    assert host.declarations() == () and host.rejected() == ()


# ---------------------------------------------------------------- provider 导入
def test_provider_loads_on_demand_and_returns_its_entries(_clean_state):
    _write_component(_clean_state)
    read, format_value = host.load_provider(host.declaration_for("cpu_temp"))
    assert read() == 74.0 and format_value(74.4) == "74"


def test_missing_provider_raises_unavailable(_clean_state):
    _write_component(_clean_state, provider=None)
    with pytest.raises(host.ProviderUnavailable):
        host.load_provider(host.declaration_for("cpu_temp"))


@pytest.mark.parametrize("source,expected", [
    ("def read_celsius(:", "导入失败"),
    ("def other():\n    return 1\n", "入口"),
])
def test_broken_provider_raises_broken(_clean_state, source, expected):
    _write_component(_clean_state, provider=source)
    with pytest.raises(host.ProviderBroken) as excinfo:
        host.load_provider(host.declaration_for("cpu_temp"))
    assert expected in str(excinfo.value)


def test_provider_entry_can_be_renamed_by_the_manifest(_clean_state):
    source = PROVIDER.replace("read_celsius", "sample").replace("format_celsius", "render")
    _write_component(_clean_state, provider=source,
                     manifest={"read": "sample", "format": "render"})
    read, format_value = host.load_provider(host.declaration_for("cpu_temp"))
    assert read() == 74.0 and format_value(74.4) == "74"


# ---------------------------------------------------------------- 采样
def test_sampler_publishes_raw_value_state_time_and_epoch(_clean_state):
    directory = _write_component(_clean_state)
    sampler = host.ExtensionSampler(interval=0.5)
    sampler.subscribe("cpu_temp")
    try:
        assert sampler.start() is True
        assert _wait_for(lambda: sampler.snapshot().get("cpu_temp") is not None), \
            "采样线程没能发布第一拍"
        sample = sampler.snapshot()["cpu_temp"]
        assert sample.state == isl.STATE_OK
        assert sample.raw_value == 74.0, "原始值要留（阈值判定吃它，不吃文案）"
        assert sample.text == "74"
        assert sample.epoch == sampler.epoch > 0
        assert 0 < sample.sampled_at <= time.monotonic() + 1, "时刻是 monotonic 的"
        assert _read_count(directory) >= 1, "组件真的被调过"
        assert sampler.subscriber_count == 1
    finally:
        sampler.close()


def test_sampler_with_zero_subscribers_never_calls_the_component(_clean_state):
    """0 订阅 = 零开销：线程空转，一次 provider 调用都不发生。"""
    directory = _write_component(_clean_state)
    sampler = host.ExtensionSampler(interval=0.2)
    try:
        sampler.start()
        time.sleep(0.6)
        assert _read_count(directory) == 0
        assert sampler.snapshot() == {}
    finally:
        sampler.close()


def test_sampler_uses_one_thread_for_every_component(_clean_state):
    root = _clean_state
    _write_component(root, "one")
    _write_component(root, "two", manifest={"id": "cpu_fan", "label": "风扇",
                                            "max_text": "8888"})
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    sampler.subscribe("cpu_fan")
    try:
        sampler.start()
        assert _wait_for(lambda: len(sampler.snapshot()) == 2)
        named = [thread for thread in threading.enumerate()
                 if thread.name == host.THREAD_NAME]
        assert len(named) == 1, "第三方段无论多少个组件都只有一条采样线程"
    finally:
        sampler.close()
    assert not [t for t in threading.enumerate() if t.name == host.THREAD_NAME]


@pytest.mark.parametrize("provider,state,reason_part", [
    (PROVIDER.replace('    _log({"t": time.monotonic()})\n', '')
             .replace('    return VALUE["celsius"]',
                      '    raise OSError("传感器掉线")'),
     isl.STATE_ERROR, "OSError"),
    (PROVIDER.replace('    return f"{round(float(value))}"',
                      '    raise RuntimeError("格式化炸了")'),
     isl.STATE_ERROR, "RuntimeError"),
    (PROVIDER.replace('    return f"{round(float(value))}"', '    return None'),
     isl.STATE_ERROR, "None"),
])
def test_read_and_format_failures_degrade_to_error(_clean_state, provider,
                                                   state, reason_part):
    """取值/格式化失败 → error 态 + 原因；宿主不抛、线程不死。"""
    _write_component(_clean_state, provider=provider)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler.start()
        assert _wait_for(lambda: (sampler.snapshot().get("cpu_temp") is not None
                                  and sampler.snapshot()["cpu_temp"].state == state))
        sample = sampler.snapshot()["cpu_temp"]
        assert reason_part in sample.error_reason
        assert sample.text == ""
    finally:
        sampler.close()


def test_giant_and_multiline_values_are_guarded_before_reaching_the_host(_clean_state):
    """契约层的两道护栏在采样线程里生效（度量前截断 / 换行压平）。"""
    source = PROVIDER.replace('    return f"{round(float(value))}"',
                              '    return "9" * 5000 + "\\n第二行"')
    _write_component(_clean_state, provider=source)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler.start()
        assert _wait_for(lambda: sampler.snapshot().get("cpu_temp") is not None)
        sample = sampler.snapshot()["cpu_temp"]
        assert sample.state == isl.STATE_OK
        assert len(sample.text) <= isl.MAX_VALUE_CHARS
        assert "\n" not in sample.text
    finally:
        sampler.close()


def test_slow_read_is_circuit_broken_and_the_host_keeps_working(_clean_state):
    """慢读：记账 → 熔断 → 不再调它。**不声称杀了线程**（Python 杀不掉阻塞中的线程），
    界面侧的证据是"熔断后快照仍可读、宿主照常取数"。"""
    directory = _write_component(
        _clean_state,
        provider=PROVIDER.replace('    _log({"t": time.monotonic()})',
                                 '    _log({"t": time.monotonic()})\n    time.sleep(0.25)'))
    sampler = host.ExtensionSampler(interval=0.2, block_budget=0.05, block_limit=2)
    sampler.subscribe("cpu_temp")
    try:
        sampler.start()
        assert _wait_for(lambda: sampler.blocked_reason("cpu_temp") != "", timeout=10.0), \
            "连续慢读要熔断"
        assert "停止采样" in sampler.blocked_reason("cpu_temp")
        assert sampler.snapshot()["cpu_temp"].state == isl.STATE_ERROR
        calls = _read_count(directory)
        time.sleep(0.8)
        assert _read_count(directory) == calls, "熔断后不许再调它的 read"
        # 重新订阅 = 新序列：熔断记录清掉（用户重开这一段就是想再要它一次）
        sampler.subscribe("cpu_temp")
        assert sampler.blocked_reason("cpu_temp") == ""
    finally:
        sampler.close()


def test_unsubscribe_drops_late_results(_clean_state):
    """退订后到达的结果一律拒收（迟到不复活）。"""
    _write_component(_clean_state)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler.start()
        assert _wait_for(lambda: sampler.snapshot().get("cpu_temp") is not None)
        sampler.unsubscribe("cpu_temp")
        assert sampler.snapshot() == {}
        time.sleep(0.6)
        assert sampler.snapshot() == {}, "退订之后线程不许把它的读数再放回来"
    finally:
        sampler.close()


def test_stop_retires_the_epoch_so_old_samples_are_rejected(_clean_state):
    _write_component(_clean_state)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler.start()
        assert _wait_for(lambda: sampler.snapshot().get("cpu_temp") is not None)
        first_epoch = sampler.epoch
        sampler.stop(wait=True, timeout=2.0)
        sampler.start()
        assert sampler.epoch > first_epoch
        assert _wait_for(lambda: sampler.snapshot().get("cpu_temp") is not None)
        assert all(sample.epoch == sampler.epoch for sample in sampler.snapshot().values())
    finally:
        sampler.close()


def test_resolver_failure_is_published_as_a_diagnosable_sample(_clean_state):
    """清单说在、文件已经不在（竞态）：如实报 unsupported + 原因，不崩线程。"""
    _write_component(_clean_state, provider=None)
    sampler = host.ExtensionSampler(interval=0.2)
    sampler.subscribe("cpu_temp")
    try:
        sampler.start()
        assert _wait_for(lambda: sampler.snapshot().get("cpu_temp") is not None)
        sample = sampler.snapshot()["cpu_temp"]
        assert sample.state == isl.STATE_UNSUPPORTED
        assert "provider.py" in sample.error_reason
    finally:
        sampler.close()


def test_interval_is_clamped_and_nan_falls_back_to_default():
    assert host.clamp_interval(0.01) == host.MIN_INTERVAL_SECONDS
    assert host.clamp_interval(10_000) == host.MAX_INTERVAL_SECONDS
    assert host.clamp_interval(float("nan")) == host.DEFAULT_INTERVAL_SECONDS
    assert host.clamp_interval("nope") == host.DEFAULT_INTERVAL_SECONDS


def test_resolve_prefers_the_runtime_registry(_clean_state):
    """``register_segment`` 注册过的段就是权威（第三方在自己进程里交的声明）。"""
    registered = isl.register_segment(id="runtime_only", label="运行期",
                                      format_value=lambda value: str(value),
                                      max_text="88", read=lambda: 7)
    assert host.resolve_declaration("runtime_only") is registered
    with pytest.raises(host.ProviderUnavailable):
        host.resolve_declaration("nope_not_declared")


def test_manifest_declaration_becomes_a_contract_declaration(_clean_state):
    """清单 + provider → 契约层的 ``SegmentDeclaration``（护栏只有一份实现）。"""
    _write_component(_clean_state)
    resolved = host.resolve_declaration("cpu_temp")
    snapshot = resolved.sample()
    assert snapshot.state == isl.STATE_OK and snapshot.text == "74"
    assert snapshot.max_text == "88"
    assert isl.alert_thresholds("cpu_temp") is None, "阈值由宿主按声明源并入，不由解析顺手改表"


def test_file_paths_are_derived_not_hardcoded():
    """可信目录来自既有配置目录与项目目录——不硬编码任何机器路径。"""
    source = Path(host.__file__).read_text(encoding="utf-8")
    assert "Program Files" not in source and "AppData" not in source
    assert host.user_root().name == host.ROOT_DIR_NAME
    assert host.project_root().name == host.ROOT_DIR_NAME
    assert host.user_root() != host.project_root()
    assert host.project_root().parent == Path(host.__file__).resolve().parents[2]


def test_provider_cache_reuses_the_same_file(_clean_state):
    _write_component(_clean_state)
    declaration = host.declaration_for("cpu_temp")
    first = host.load_provider(declaration)
    second = host.load_provider(declaration)
    assert first[0] is second[0] and first[1] is second[1]
