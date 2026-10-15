# -*- coding: utf-8 -*-
"""设置页「网络信息 / 灵动岛指标」的行/依赖/保存契约（issue #172 + P1 Slice C）。

断言走真实 ``ModernSettingsDialog``（设置窗口这个公开 seam），不碰私有调用顺序：
默认值呈现、指标四开关（含 P3 的 GPU 占位行）与 ``island_metrics`` 的对应、
依赖显隐（岛关 → 信息槽关 → 指标行）、保存回写（既有顺序保留 + 新勾选按典范序追尾）、
脏值不炸设置页、子宠页可编辑（值落主配置）、**窗口打开后组件清单变动**（保存不许
断链，表单没覆盖的 id 保留磁盘偏好）。

``island_metrics`` 是**唯一**的指标真值（旧键 ``island_network_enabled`` 只在归一化
阶段单向迁移一次，设置页不再写它）。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pet.modern_settings_dialog as settings_mod
from pet import island_extension_host as host
from pet import net_monitor
from pet.config import Config
from pet.island_segments import CPU, GPU, MEM, NETWORK
from tests.test_island_extension_host import MANIFEST, _write_component

app = QApplication.instance() or QApplication([])


def _dialog(tmp_path, monkeypatch, **network):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    cfg = Config(base=tmp_path)
    for key, value in network.items():
        cfg.set(key, value)
    # #271：空身份会话按 slot0 分流，全局键的读源是路由视图另建的主配置（初值只取盘）
    # ——预置必须落盘才进得了对话框（缺省形态不必写盘）。
    if network:
        assert cfg.save() is True
    dialog = settings_mod.ModernSettingsDialog(cfg, include_ai=True)
    return dialog, cfg


def _row(dialog, key):
    row = dialog.findChild(settings_mod.SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


def test_network_rows_exist_with_documented_copy(tmp_path, monkeypatch):
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        toggle = _row(dialog, "island_network")
        interval = _row(dialog, "network_probe_interval")
        menu = _row(dialog, "network_info_menu")
        text = toggle.hint_label.text() + interval.hint_label.text()
        assert "0.16 KB" in text, "必须写明单次探测的流量成本"
        assert "MB/天" in text, "必须写明按间隔折算的日流量"
        # 2026-10-07 第一刀：间隔行不再写死 223.5.5.5（IP 属实现细节），改为如实说明
        # 「检测目标是固定的境内服务（阿里云）」，语义要求不变：用户要知道不默认打国外。
        assert "检测目标是固定的境内服务（阿里云）" in interval.hint_label.text(), (
            "必须写明探测目标是境内服务（阿里云），不默认打国外"
        )
        assert "不是你的访问网站" in interval.hint_label.text()
        assert "1 KB" in menu.hint_label.text() and "不轮询" in menu.hint_label.text()
    finally:
        dialog.close()


def test_network_rows_defaults_match_config(tmp_path, monkeypatch):
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        assert dialog.island_network_check.isChecked() is False
        assert dialog.island_cpu_check.isChecked() is False
        assert dialog.island_mem_check.isChecked() is False
        assert dialog.network_info_menu_check.isChecked() is True
        assert dialog.network_probe_interval_spin.value() == 1.0
        assert dialog.network_probe_interval_spin.minimum() == 0.5
        assert dialog.network_probe_interval_spin.maximum() == 60.0
    finally:
        dialog.close()


def test_metric_toggles_reflect_the_configured_list(tmp_path, monkeypatch):
    """开关初值 = ``island_metrics`` 成员关系（不是旧键）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch, island_metrics=[CPU, MEM])
    try:
        assert dialog.island_cpu_check.isChecked() is True
        assert dialog.island_mem_check.isChecked() is True
        assert dialog.island_network_check.isChecked() is False
    finally:
        dialog.close()


def test_legacy_network_key_shows_up_checked_after_migration(tmp_path, monkeypatch):
    """老用户升级：配置里只有旧键 True → 迁移进列表，设置页照常显示"网络已勾选"。"""
    dialog, cfg = _dialog(tmp_path, monkeypatch, island_network_enabled=True)
    try:
        assert cfg.get("island_metrics") == [NETWORK]
        assert dialog.island_network_check.isChecked() is True
        assert dialog.island_cpu_check.isChecked() is False
    finally:
        dialog.close()


def test_interval_row_is_keyed_to_the_network_toggle(tmp_path, monkeypatch):
    """间隔行只跟「网络」开关走：CPU / 内存开关与探测间隔无关。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_enabled_check.setChecked(True)
        dialog.island_info_check.setChecked(True)
        assert _row(dialog, "network_probe_interval").isHidden()

        dialog.island_cpu_check.setChecked(True)
        dialog.island_mem_check.setChecked(True)
        assert _row(dialog, "network_probe_interval").isHidden(), "CPU/内存不产生探测包"

        dialog.island_network_check.setChecked(True)
        assert not _row(dialog, "network_probe_interval").isHidden()

        dialog.island_network_check.setChecked(False)
        assert _row(dialog, "network_probe_interval").isHidden()
    finally:
        dialog.close()


def test_dependency_visibility_chains_island_info_network(tmp_path, monkeypatch):
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        island_on = dialog.island_enabled_check
        info_on = dialog.island_info_check
        net = dialog.island_network_check
        island_on.setChecked(True)
        info_on.setChecked(True)
        assert not _row(dialog, "island_network").isHidden()
        assert not _row(dialog, "island_cpu").isHidden()
        assert not _row(dialog, "island_mem").isHidden()
        assert _row(dialog, "network_probe_interval").isHidden(), "网络槽关着：间隔行隐藏"

        net.setChecked(True)
        assert not _row(dialog, "network_probe_interval").isHidden()
        # 网络段**不再接管**信息槽内容（P1 Slice D1 已删掉接管，info_mode 始终生效）：
        # 内容选择行保持可见——把它隐掉才是真正的"改了没反应"（P1 Slice D2 修正）
        assert not _row(dialog, "dynamic_island_info_mode").isHidden()
        # 「自定义短文本」只跟内容选择走（选 custom 才显示），与网络开关无关
        dialog.island_info_mode_select.setCurrentIndex(
            dialog.island_info_mode_select.findData("custom"))
        assert not _row(dialog, "dynamic_island_custom_text").isHidden()
        net.setChecked(False)
        assert not _row(dialog, "dynamic_island_custom_text").isHidden(), \
            "网络开关不再影响内容选择两行"
        dialog.island_info_mode_select.setCurrentIndex(
            dialog.island_info_mode_select.findData("time"))
        assert _row(dialog, "dynamic_island_info_mode").isHidden() is False
        assert _row(dialog, "network_probe_interval").isHidden()

        info_on.setChecked(False)
        assert _row(dialog, "island_network").isHidden()
        assert _row(dialog, "island_cpu").isHidden()
        assert _row(dialog, "island_mem").isHidden()
        info_on.setChecked(True)
        assert not _row(dialog, "island_network").isHidden()

        island_on.setChecked(False)
        assert _row(dialog, "island_network").isHidden()
        assert _row(dialog, "island_cpu").isHidden()
        assert _row(dialog, "island_mem").isHidden()
    finally:
        dialog.close()


def test_interval_hint_tracks_the_chosen_value(tmp_path, monkeypatch):
    """日流量数字跟着真实间隔走：1 秒的数字必须大于 30 秒的数字。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog, "network_probe_interval")

        def per_day():
            text = row.hint_label.text()
            return float(text.split("约 ")[1].split(" MB/天")[0])

        dialog.network_probe_interval_spin.setValue(1.0)
        fast = per_day()
        dialog.network_probe_interval_spin.setValue(30.0)
        slow = per_day()
        assert fast > slow > 0
    finally:
        dialog.close()


def test_interval_spin_is_not_clipped_at_larger_fonts(tmp_path, monkeypatch):
    """最长值 "60.0 秒" 在默认字体下就占满基类的 92px 固定宽度（零余量），
    放大字体必裁——本项解除固定宽度并按字体度量给足最小宽度。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        spin = dialog.network_probe_interval_spin
        spin.setValue(spin.maximum())
        spin.show()
        app.processEvents()
        line = spin.lineEdit()
        assert line.width() >= line.fontMetrics().horizontalAdvance(line.text()), "默认字体下就裁了"
        assert spin.maximumWidth() > 1000, "宽度不得被基类的 92px 固定值钉死"

        for scale in (1.5, 2.0):
            font = spin.font()
            font.setPointSizeF(max(1.0, font.pointSizeF()) * scale)
            spin.setFont(font)
            line.setFont(font)
            app.processEvents()
            needed = line.fontMetrics().horizontalAdvance(line.text())
            assert spin.sizeHint().width() >= needed + 24, (scale, spin.sizeHint().width(), needed)
    finally:
        dialog.close()


def test_save_writes_metrics_and_reloads(tmp_path, monkeypatch):
    """保存写 ``island_metrics``（不再写旧键），落盘后重启读回同一列表。"""
    dialog, cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_network_check.setChecked(True)
        dialog.network_probe_interval_spin.setValue(3.5)
        dialog.network_info_menu_check.setChecked(False)
        dialog._write_config()
    finally:
        dialog.close()

    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [NETWORK]
    assert cfg.get("network_probe_interval_seconds") == 3.5
    assert cfg.get("network_info_menu_enabled") is False
    assert cfg.get("island_network_enabled") is False, "旧键不再是第二写入来源"
    restored = Config(base=tmp_path)
    assert restored.get("island_metrics") == [NETWORK]
    assert restored.get("network_probe_interval_seconds") == 3.5
    assert restored.get("network_info_menu_enabled") is False


def test_save_writes_the_canonical_metric_order(tmp_path, monkeypatch):
    """配置里本来没有指标列表：全按典范序 [network, cpu, mem, gpu] 产出。

    复选框不表达顺序（拖动排序是后续版本的事），所以"勾了哪几个"必须落成唯一
    可预测的列表——顺序随勾选先后漂移会让岛上的段序在保存后莫名变化。
    配置里已经有列表时保留原序，那是下一条用例的前半段。
    """
    dialog, cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_mem_check.setChecked(True)
        dialog.island_network_check.setChecked(True)
        dialog._write_config()
    finally:
        dialog.close()
    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [NETWORK, MEM], "勾选先后不影响写回顺序"

    dialog2, cfg2 = _dialog(tmp_path / "all", monkeypatch)
    try:
        dialog2.island_mem_check.setChecked(True)
        dialog2.island_cpu_check.setChecked(True)
        dialog2.island_network_check.setChecked(True)
        dialog2._write_config()
    finally:
        dialog2.close()
    cfg2.reload()
    assert cfg2.get("island_metrics") == [NETWORK, CPU, MEM]


def test_save_keeps_the_order_already_in_the_config(tmp_path, monkeypatch):
    """保存不得把用户排好的顺序压回典范序（列表序 = 用户优先级 = 尾部先折叠）。

    复选框只表达"在/不在"，顺序只存在于配置里——按固定序重写，等于用户手改
    一次顺序后只要开过设置页保存就被改回去（折叠优先级是岛上的可见后果）。
    契约：已有且仍勾选的按原序在前，新勾选的按 ``_METRIC_ORDER`` 追到末尾。
    """
    dialog, cfg = _dialog(tmp_path, monkeypatch, island_metrics=[MEM, CPU, NETWORK])
    try:
        dialog._write_config()
    finally:
        dialog.close()
    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [MEM, CPU, NETWORK], "没碰过这几行的保存不得改顺序"

    dropped, cfg_dropped = _dialog(tmp_path / "dropped", monkeypatch,
                                  island_metrics=[MEM, CPU, NETWORK])
    try:
        dropped.island_cpu_check.setChecked(False)
        dropped._write_config()
    finally:
        dropped.close()
    cfg_dropped.reload()
    assert cfg_dropped.get("island_metrics") == [MEM, NETWORK], "取消勾选的在原位淘汰"

    added, cfg_added = _dialog(tmp_path / "added", monkeypatch,
                               island_metrics=[MEM, NETWORK])
    try:
        added.island_gpu_check.setChecked(True)
        added._write_config()
    finally:
        added.close()
    cfg_added.reload()
    assert cfg_added.get("island_metrics") == [MEM, NETWORK, GPU], "新勾选的追到末尾"


def test_save_with_every_toggle_off_writes_an_empty_list(tmp_path, monkeypatch):
    dialog, cfg = _dialog(tmp_path, monkeypatch, island_metrics=[NETWORK, CPU, MEM])
    try:
        for toggle in (dialog.island_network_check, dialog.island_cpu_check,
                       dialog.island_mem_check, dialog.island_gpu_check):
            toggle.setChecked(False)
        dialog._write_config()
    finally:
        dialog.close()
    cfg.reload()
    assert cfg.get("island_metrics") == []


def test_dirty_interval_does_not_break_settings_page(tmp_path, monkeypatch):
    """手改 config.json 塞进越界间隔：设置页仍能构建，控件落在合法区间内。"""
    dialog, _cfg = _dialog(
        tmp_path, monkeypatch,
        network_probe_interval_seconds=net_monitor.MAX_INTERVAL_SECONDS * 100,
    )
    try:
        assert dialog.network_probe_interval_spin.value() == net_monitor.MAX_INTERVAL_SECONDS
    finally:
        dialog.close()


def test_network_rows_are_searchable(tmp_path, monkeypatch):
    """行进了设置搜索索引（objectName 约定），否则用户搜不到。

    搜索命中面 = 行 label + hint + objectName，所以「灵动岛指标」必须落在 label
    上；CPU / 内存各自一行，用户搜「CPU」「内存」都能命中。
    """
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        names = {row.objectName() for row in dialog._search_rows}
        assert "settingRow_island_network" in names
        assert "settingRow_island_cpu" in names
        assert "settingRow_island_mem" in names
        assert "settingRow_network_probe_interval" in names
        assert "settingRow_network_info_menu" in names

        labels = [row.label.text() for row in dialog._search_rows
                  if row.objectName().startswith("settingRow_island_")]
        assert all("灵动岛指标" in label for label in labels)
        hits = [row for row in dialog._search_rows if "cpu" in row.label.text().lower()]
        assert [row.objectName() for row in hits] == ["settingRow_island_cpu"]
        assert [row.objectName() for row in dialog._search_rows
                if "内存" in row.label.text()] == ["settingRow_island_mem"]
    finally:
        dialog.close()


# ---------------------------------------------------------------- 子宠页作用域
def test_network_controls_are_editable_on_the_child_page(tmp_path, monkeypatch):
    """子宠页：指标与网络键可编辑，保存落**主配置**（采样器/岛/菜单读的就是它）。

    这些键不在 ``PER_SLOT_SETTING_KEYS`` 里（子宠页曾经置灰：写了 slot 文件、
    运行期读主配置 = 死开关）。独立设置进程现在按 key 分流到主配置，故恢复可用。
    """
    master, _cfg = _dialog(tmp_path, monkeypatch)
    child = settings_mod.ModernSettingsDialog(
        Config(base=tmp_path, instance_id="slot-1"), include_ai=True)
    try:
        master_controls = (master.island_network_check, master.island_cpu_check,
                           master.island_mem_check, master.network_probe_interval_spin,
                           master.network_info_menu_check)
        child_controls = (child.island_network_check, child.island_cpu_check,
                          child.island_mem_check, child.network_probe_interval_spin,
                          child.network_info_menu_check)
        for control in master_controls + child_controls:
            assert control.isEnabled() is True
            assert control.toolTip() != "仅主桌宠可设置"

        child.island_cpu_check.setChecked(True)
        child.network_info_menu_check.setChecked(False)
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的是
        # 全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert "cpu" in on_disk["island_metrics"], "子宠页改的键必须真正生效"
        assert on_disk["network_info_menu_enabled"] is False
    finally:
        for dlg in (master, child):
            dlg.close()
            dlg.deleteLater()
        app.processEvents()


# ---------------------------------------------------------------- GPU 行（P3 F1 占位 → F2 真接线）
def test_gpu_row_exists_and_is_searchable(tmp_path, monkeypatch):
    """GPU 行与 cpu/mem 同款：一行一开关、可搜索、文案说清真实口径与代价。

    它跟 cpu/mem 走**同一份** ``_METRIC_ROWS``（行装配/依赖/搜索置灰全部复用），
    所以这里断言的是"它真的进了那张表"，而不是另开一条平行通道；文案则必须与
    接线后的真实行为一致（1 秒采样、最忙引擎不是总算力、看不见就不采、首拍 --）。
    """
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog, "island_gpu")
        assert "灵动岛指标" in row.label.text()
        assert "GPU" in row.label.text()
        hint = row.hint_label.text()
        assert "最忙" in hint and "总算力" in hint, "口径必须说清不是整卡总算力"
        assert "1 秒" in hint, "采样节拍如实写（后台共享线程，不进动画帧）"
        assert "不采样" in hint, "隐藏/折叠/关掉都不采样（零开销）"
        assert "--" in hint, "首拍/读不到时是 --，不是 0"
        assert "不占位" in hint and "原因" in hint, "不支持的平台：不占位 + 卡片写原因"

        names = {r.objectName() for r in dialog._search_rows}
        assert "settingRow_island_gpu" in names
        assert [r.objectName() for r in dialog._search_rows
                if "gpu" in r.label.text().lower()] == ["settingRow_island_gpu"]
    finally:
        dialog.close()


def test_gpu_row_defaults_off_and_follows_the_same_dependency_chain(tmp_path, monkeypatch):
    """默认关闭；显隐跟「岛开关 × 信息槽开关」走（与 cpu/mem 逐行同款）。"""
    dialog, _cfg = _dialog(tmp_path, monkeypatch)
    try:
        assert dialog.island_gpu_check.isChecked() is False
        dialog.island_enabled_check.setChecked(True)
        dialog.island_info_check.setChecked(True)
        assert not _row(dialog, "island_gpu").isHidden()
        dialog.island_info_check.setChecked(False)
        assert _row(dialog, "island_gpu").isHidden()
        dialog.island_info_check.setChecked(True)
        dialog.island_enabled_check.setChecked(False)
        assert _row(dialog, "island_gpu").isHidden()
    finally:
        dialog.close()


def test_gpu_round_trips_through_save_and_reopen(tmp_path, monkeypatch):
    """勾 GPU → 保存 → 重启读回：它在 ``island_metrics`` 里，且再开设置页仍是勾选态。"""
    dialog, cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_gpu_check.setChecked(True)
        dialog._write_config()
    finally:
        dialog.close()
    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [GPU], "典范序里 gpu 排在三个内置 id 之后"
    assert Config(base=tmp_path).get("island_metrics") == [GPU]

    again, _cfg2 = _dialog(tmp_path, monkeypatch)
    try:
        assert again.island_gpu_check.isChecked() is True
    finally:
        again.close()


def test_save_writes_gpu_after_the_other_metrics(tmp_path, monkeypatch):
    """四个复选框折成一个列表：典范序 [network, cpu, mem, gpu]。

    gpu 排在**末尾**是刻意的：老用户的三项相对次序不许因为加了一行而被重排
    （列表序 = 用户优先级，保存一次就把段序改掉是用户看不见的破坏）。
    """
    dialog, cfg = _dialog(tmp_path, monkeypatch)
    try:
        dialog.island_gpu_check.setChecked(True)
        dialog.island_mem_check.setChecked(True)
        dialog.island_cpu_check.setChecked(True)
        dialog.island_network_check.setChecked(True)
        dialog._write_config()
    finally:
        dialog.close()
    cfg.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert cfg.get("island_metrics") == [NETWORK, CPU, MEM, GPU]


def test_gpu_row_is_editable_on_the_child_page(tmp_path, monkeypatch):
    """子宠页：gpu 与另三个指标开关同属进程级键 → 同规可编辑，保存落主配置。"""
    import json

    master, _cfg = _dialog(tmp_path, monkeypatch)
    child = settings_mod.ModernSettingsDialog(
        Config(base=tmp_path, instance_id="slot-1"), include_ai=True)
    try:
        assert master.island_gpu_check.isEnabled() is True
        assert child.island_gpu_check.isEnabled() is True
        assert child.island_gpu_check.toolTip() != "仅主桌宠可设置"

        child.island_gpu_check.setChecked(True)
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的是
        # 全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert GPU in on_disk["island_metrics"], "子宠页改的键必须真正生效"
    finally:
        for dlg in (master, child):
            dlg.close()
            dlg.deleteLater()
        app.processEvents()


# ---------------------------------------------------------------- 窗口打开后清单变动
def _isolated_extension_root(tmp_path, monkeypatch):
    """把可信组件根指到用例自己的临时目录（不读用户真实目录）。"""
    root = tmp_path / "island-extensions"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(host.ENV_DIRS, str(root))
    host.reset_cache()
    return root


def test_save_survives_a_component_that_appears_after_the_form_was_built(tmp_path, monkeypatch):
    """窗口开着时清单多了组件：保存不许抛，也不许顺手删掉表单没覆盖的偏好。

    设置页的控件是**建窗那一刻**按清单建的（用户装组件不需要重启设置进程）。
    保存时再问一次 ``metric_order()`` 就会拿到一个没有控件的 id——无默认值的
    ``getattr`` 直接抛 AttributeError，整条保存链（网络 → 音乐 → 静音 → 天气 →
    ``config.save()``）在写入之前断掉：这次保存一个键都没落盘，而窗口还照常关。
    """
    root = _isolated_extension_root(tmp_path, monkeypatch)
    try:
        dialog, cfg = _dialog(tmp_path, monkeypatch, island_metrics=[NETWORK])
        try:
            assert dialog.island_network_check.isChecked() is True
            assert "probe_late" not in dialog._island_metric_controls, \
                "控件快照 = 建窗那一刻真的建过控件的 id"
            # 窗口开着的时候：用户往可信目录装了一个合法组件，别处（主进程也是同一份
            # config.json）已经把它写进了 island_metrics——表单里根本没有它的控件。
            _write_component(root, "late",
                             manifest=dict(MANIFEST, id="probe_late", label="新组件"))
            cfg.set("island_metrics", [NETWORK, "probe_late"])

            assert dialog._write_config() is True, "清单变动不许把整条保存链打断"
            assert cfg.get("island_metrics") == [NETWORK, "probe_late"], \
                "表单没覆盖的 id 保留磁盘偏好，表单覆盖的 id 按控件走"
        finally:
            dialog.close()
    finally:
        host.reset_cache()


def test_save_keeps_the_preference_of_a_declared_component_whose_provider_is_missing(
        tmp_path, monkeypatch):
    """组件暂缺（清单在、provider 文件不在）：行仍在表单里 → 勾选态就是偏好，保存不删。"""
    root = _isolated_extension_root(tmp_path, monkeypatch)
    try:
        _write_component(root, "cpu_temp", provider=None)     # 只落清单
        dialog, cfg = _dialog(tmp_path, monkeypatch, island_metrics=["cpu_temp"])
        try:
            assert dialog.island_cpu_temp_check.isChecked() is True
            assert dialog._write_config() is True
            assert cfg.get("island_metrics") == ["cpu_temp"], \
                "暂缺 ≠ 卸载：这份偏好要留着，文件回来即恢复"
        finally:
            dialog.close()
    finally:
        host.reset_cache()
