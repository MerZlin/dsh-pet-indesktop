# -*- coding: utf-8 -*-
"""档案代次被外部推进后，设置窗必须**关得掉**（2026-10-10 实机报告 §4 的收口）。

复现链（真实持久副本，代次不用 mock）：打开子宠设置窗（表单首读钉住当时的档案代次）→
这只宠被退出又重新生成（``pet_profiles.begin_slot_generation``，正是壳在生成新住客时
写的那一步）→ 旧窗口点「保存并退出」/ X / Esc：代次防覆盖门拒绝旧表单落盘。旧实现到
这里就是死路——窗口关不掉、提示还把"代次落后"说成"配置未能写入磁盘"，用户只能杀进程。

产品口径（父级决定，逐条钉在下面的用例里）：

1. 旧代次**不许**覆盖新档案（这道门不动，它是保护不是故障）；
2. 默认保留草稿——不自动覆盖、不自动刷新、不把窗口刷成新值；
3. 但必须有**明确可达**的出口「放弃未保存的改动并关闭」；
4. 代次冲突的说明是"这只桌宠已重新生成 / 设置在别处更新过，请重新打开"，**不是**磁盘失败；
5. 普通写失败（锁/写盘）同样可安全放弃；X / Esc 与「保存并退出」同契约、同出口；
6. 放弃只放弃**还没写成功**的部分：档案被代次门挡住时全局那一半可能已经落盘，文案必须
   如实说出来（不许说"已回滚"）；
7. Qt 只 finalize 一次：同一件事不弹两个框，X 关闭不把整表写回两遍。

测试用真对话框 + 真槽档案文件 + 真 ``settings.lock``；只有"用户在消息框里点了哪个按钮"
这一处是可注入的 seam（``settings_close_flow.confirm_discard_unsaved``），且替身里用的是
**产品真实构造**的框（文案/按钮/默认钮都是真的）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet import pet_profiles
from pet import settings_close_flow
from pet import settings_slot_config
from pet.config import Config

SLOT = 1
KEEP_LABEL = "继续编辑"
DISCARD_LABEL = "放弃未保存的改动并关闭"
#: 产品那份 `confirm_discard_unsaved`（conftest 的 autouse 挡板会在用例里把它换成
#: "继续编辑"；需要跑真 exec 的用例把它装回去）。
_REAL_CONFIRM_DISCARD = settings_close_flow.confirm_discard_unsaved
#: 产品那份 `QMessageBox.warning`（同样在 conftest 的防卡挡板之前取下来——在用例里
#: 现取会拿到挡板那个 no-op，见 ``test_discard_unverified_notice_is_a_real_visible_box``）。
_REAL_WARNING = settings_close_flow.QMessageBox.warning


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------ 夹具 / 工具
def _config_dir(tmp_path) -> Path:
    """真实配置目录（``Config.dir`` = base / APP_DIR_NAME；槽档案与代次都住这里）。"""
    return Config(base=tmp_path).dir


def _main_config(tmp_path) -> Config:
    """主配置（全局键那份），并落一个非默认的全局值当基线。"""
    main = Config(base=tmp_path)
    main.set("pet_opacity", 55)
    assert main.save() is True
    return main


def _slot_path(tmp_path) -> Path:
    return pet_profiles.slot_profile_path(_config_dir(tmp_path), SLOT)


def _profile_exists(tmp_path, values: dict) -> None:
    assert pet_profiles.write_profile_settings(
        _config_dir(tmp_path), SLOT, values, user_customized=True).status == "ok"


def _dialog(tmp_path, *, standalone: bool = True):
    from pet.modern_settings_dialog import ModernSettingsDialog

    return ModernSettingsDialog(
        Config(base=tmp_path, instance_id=f"slot-{SLOT}"),
        include_ai=False, standalone=standalone)


def _rebuild_pet(tmp_path) -> int:
    """真实持久副本：这只宠被退出又重新生成（壳在生成新住客时做的就是这一步）。"""
    result = pet_profiles.begin_slot_generation(_config_dir(tmp_path), SLOT)
    assert result.ok, result
    return result.generation


def _answer_with(monkeypatch, *, discard: bool, seen: list, note=None):
    """替掉 exec：框本体走**产品真实构造**（文案/按钮/默认钮都是真的）。

    与 ``test_settings_reset_defaults`` 的 ``answer_yes`` 同规——只替换"用户点了哪个
    按钮"这一处，任何文案/按钮语义的错配都会在断言里露出来。
    """
    def answer(parent, failure):
        box, keep, discard_button = settings_close_flow.build_save_failure_box(parent, failure)
        try:
            seen.append({
                "failure": failure,
                "title": box.windowTitle(),
                "text": box.text(),
                "info": box.informativeText(),
                "default": box.defaultButton(),
                "escape": box.escapeButton(),
                "keep": keep,
                "discard": discard_button,
            })
        finally:
            box.deleteLater()
        if note is not None:
            note()
        return discard

    monkeypatch.setattr(settings_close_flow, "confirm_discard_unsaved", answer)


def _copy_of(entry: dict) -> str:
    return "\n".join((entry["title"], entry["text"], entry["info"]))


def _drop(dialog) -> None:
    """收尾：对话框带 WA_DeleteOnClose，被真关掉时 C++ 对象当场销毁（判活着再删）。"""
    import shiboken6

    if shiboken6.isValid(dialog):
        dialog.deleteLater()


def _close_with_escape(dialog, app) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    app.processEvents()


def _run_realmodal_driver(tmp_path, mode: str) -> dict:
    """在干净子进程里跑真模态框流程（驱动器：tests/settings_realmodal_driver.py）。

    主套件共享进程里的 quitNow 残留会让真模态 exec 进入即 -1 秒退（qtbase 6.9
    qeventloop.cpp / qcoreapplication.cpp:1512），进程内清旗又必须泵全局事件队列
    （conftest 明令禁止的全局冲刷）——所以真框验证走进程边界（AGENTS.md：Qt 回归
    用真实事件循环和进程边界），子进程里 Qt 状态干净、防卡桩为零，框全是产品原件。
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable,
         str(Path(__file__).parent / "settings_realmodal_driver.py"),
         mode, str(tmp_path), str(SLOT)],
        capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, f"驱动器异常退出 {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    lines = [line[len("RESULT "):] for line in proc.stdout.splitlines()
             if line.startswith("RESULT ")]
    assert len(lines) == 1, f"驱动器必须恰好打一行 RESULT:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(lines[0])


# ------------------------------------------------------------ 1. 死路消失
def test_rebuilt_pet_keeps_the_draft_by_default_and_can_still_be_closed(
        tmp_path, app, monkeypatch):
    """代次冲突：默认保留草稿留在窗口里，但「放弃未保存的改动并关闭」必须可达。

    红在修复前：``_write_config`` 返回 False → ``closeEvent`` ignore，Esc/X 一律关不掉，
    用户只能杀进程；且提示把代次冲突说成"配置未能写入磁盘"。
    """
    import shiboken6

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=False, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()
    try:
        assert float(dialog.scale_combo.currentData()) == pytest.approx(0.8)
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        generation = _rebuild_pet(tmp_path)

        _close_with_escape(dialog, app)

        assert finished == [], "用户选「继续编辑」时不得关窗"
        assert shiboken6.isValid(dialog) and dialog.isVisible(), "窗口必须还在"
        assert float(dialog.scale_combo.currentData()) == pytest.approx(1.0), "草稿必须还在"

        entry = seen[0]
        failure = entry["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_STALE, (
            "代次落后必须与写盘失败分开归类，才能给对文案与出口")
        assert failure.status == "stale-generation"
        copy = _copy_of(entry)
        assert "重新生成" in entry["text"] or "已更新" in entry["text"]
        assert "重新打开" in copy, "必须告诉用户怎么恢复（重开设置页）"
        assert "配置未能写入磁盘" not in copy, "代次冲突不许冒充磁盘失败"
        assert entry["default"] is entry["keep"] and entry["escape"] is entry["keep"], (
            "非破坏性动作占默认位：误触回车/Esc 只留在窗口里")
        assert entry["keep"].text() == KEEP_LABEL
        assert entry["discard"].text() == DISCARD_LABEL, "出口必须写明放弃的是什么"

        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8), (
            f"旧表单不得覆盖代次 {generation} 的档案")

        # 第二次关闭必须**重新**走一遍保存与选择：选过「继续编辑」不等于"下次直接放行"
        _close_with_escape(dialog, app)
        assert len(seen) == 2, "每次关闭都要重新问一次"
        assert finished == [] and dialog.isVisible(), "仍然不许关窗（草稿还在控件里）"
    finally:
        _drop(dialog)


# ------------------------------------------------------------ 2. 放弃出口真的能出去
def test_discard_closes_once_without_another_save(tmp_path, app, monkeypatch):
    """选「放弃未保存的改动并关闭」：窗口真的关（只 finalize 一次），且不再写第二次。"""
    from PySide6.QtWidgets import QDialog

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    attempts: list = []
    real_write = pet_profiles.write_profile_settings

    def spy(config_dir, slot, values, **kwargs):
        attempts.append(int(slot))
        return real_write(config_dir, slot, values, **kwargs)

    monkeypatch.setattr(pet_profiles, "write_profile_settings", spy)
    seen: list = []
    marks: list = []
    _answer_with(monkeypatch, discard=True, seen=seen,
                 note=lambda: marks.append(len(attempts)))
    dialog = _dialog(tmp_path)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        _rebuild_pet(tmp_path)
        baseline = _slot_path(tmp_path).read_bytes()

        _close_with_escape(dialog, app)

        assert finished == [int(QDialog.DialogCode.Rejected)], (
            "放弃后 Esc 照常关窗，且只 finalize 一次")
        assert len(seen) == 1, "一次关闭只弹一个框（不许递归/重复）"
        assert marks and len(attempts) == marks[0], (
            "用户做出放弃决定之后不得再写第二次")
        assert _slot_path(tmp_path).read_bytes() == baseline, (
            "新代次的档案一个字节都不许被这张旧表单碰")
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)


def test_discard_from_the_save_button_also_closes(tmp_path, app, monkeypatch):
    """「保存并退出」按钮与 X/Esc 同契约：失败时给同一组选择，选放弃照样关窗。"""
    from PySide6.QtWidgets import QDialog

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    dialog = _dialog(tmp_path)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        _rebuild_pet(tmp_path)

        dialog.save_exit_button.click()
        app.processEvents()

        assert len(seen) == 1
        assert seen[0]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert finished == [int(QDialog.DialogCode.Accepted)], "放弃后按钮也要能关窗"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)


def test_x_close_offers_the_same_discard_exit(tmp_path, app, monkeypatch):
    """WM_CLOSE（点 X）与 Esc 同契约：冲突时同样给出口，选放弃同样只关一次窗。

    实机 §4 第 5 步就是这条：X 一样走不起来（旧实现 `closeEvent` ignore，窗口卡死）。
    """
    from PySide6.QtWidgets import QDialog

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    dialog = _dialog(tmp_path)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        _rebuild_pet(tmp_path)

        dialog.close()                       # 等价于窗口管理器发 WM_CLOSE（点 X）
        app.processEvents()

        assert len(seen) == 1, "X 也必须给「继续编辑 / 放弃未保存的改动并关闭」"
        assert seen[0]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert finished == [int(QDialog.DialogCode.Rejected)], (
            "放弃后 X 照常关窗，且 Qt 只 finalize 一次")
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)


def test_product_confirm_discard_reads_the_real_button_click(tmp_path):
    """**产品真实**的 ``confirm_discard_unsaved``：真 exec + 真点按钮（干净子进程）。

    其余用例为了能断言文案而替掉了 ``exec``；这一条不替——真框、真按钮、真点击，
    证明 ``exec`` → ``clickedButton()`` → 关窗这段接线成立，按钮文案与动作没有错位。
    子进程化的原因见 ``_run_realmodal_driver``：套件共享进程的 quitNow 残留会让
    真模态 exec 秒退，这不是产品缺陷，是测试进程级污染，只能进程边界隔开。
    """
    from PySide6.QtWidgets import QDialog

    result = _run_realmodal_driver(tmp_path, "confirm-discard")

    assert result["clicked"] == [DISCARD_LABEL], "真框里必须有一个可点的「放弃未保存的改动并关闭」"
    assert result["finished"] == int(QDialog.DialogCode.Rejected), "点了放弃就必须关窗"
    assert len(result["boxes"]) == 1, "一次关闭只弹一个确认框"
    assert result["disk_scale"] == pytest.approx(0.8)


def test_corrupt_profile_with_a_written_global_half_is_described_honestly(
        tmp_path, app, monkeypatch):
    """坏档案 + 全局那一半**真写成功**：文案不许说"全局一字未写"，也不许说"已回滚"。

    实机审查点：``save()`` 先写档案、再写主配置，所以坏档案挡住档案那一路时，全局那半
    可能已经落盘。旧文案说"本次一个字节都没有写入"，与同一段正文里"已经写入的部分不会
    撤回"自相矛盾——用户按它决策就是被两句话各骗一半。这一条取**真框文本** + **真盘上
    事实**，两边必须一致。
    """
    main = _main_config(tmp_path)
    config_dir = _config_dir(tmp_path)
    path = _slot_path(tmp_path)
    path.write_bytes(b'{ "scale": 1.0, broken')          # 坏档案：一个字节都不许被覆盖
    before = path.read_bytes()
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    try:
        dialog.pet_opacity_spin.setValue(35)             # 全局键（走主 config.json）

        _close_with_escape(dialog, app)

        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_PROFILE
        assert (failure.slot_written, failure.global_written) == (False, True), (
            "档案那一路没写、全局那一路真写成了")
        copy = _copy_of(seen[0])
        assert "未写入这只桌宠的档案" in copy, "只对这只桌宠的档案负责，不替全局下结论"
        assert "一个字节都没写" not in copy and "都没有写入" not in copy, (
            "全局那一半确实写进去了，不许说全程没写")
        assert "全局设置" in copy and "不会撤回" in copy, "写成的部分要如实点名"
        assert "已回滚" not in copy, "做不到的承诺不许写"
        assert json.loads(Path(main.path).read_text(encoding="utf-8"))["pet_opacity"] == 35, (
            "文案说的「全局已写入」必须与盘上事实一致")
        assert path.read_bytes() == before, "坏档案一个字节都不许动"
    finally:
        _drop(dialog)


def test_autostart_exception_does_not_hang_the_close(tmp_path, app, monkeypatch):
    """``_apply_autostart`` 抛异常：不许穿出关闭路径（点 X 没反应 = 又一个卡死的窗口）。

    边界核实（为什么这一支必须有出口）：``pet/autostart.py`` 的 ``enable/disable`` 只吞
    ``OSError``（231/248/271 行），``plistlib.dump`` / ``_win_command`` / 宿主或测试注入的
    ``set_enabled`` 替身抛出的其它异常会原样冒出来；``_apply_autostart`` 自己还读 Qt 控件。
    **没有**"永不抛"的保证，所以按同一条闸门收口，文案如实说"设置已保存、自启没成"。
    """
    from PySide6.QtWidgets import QDialog

    import pet.modern_settings_dialog as settings_mod

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))

    def explode(_enabled):
        raise RuntimeError("写登录项炸了（用例注入）")

    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", explode)
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.autostart_check.setChecked(not dialog._autostart_initial)   # 触发写入分支

        _close_with_escape(dialog, app)

        assert len(seen) == 1, "自启出错也要给「继续编辑 / 放弃…并关闭」，不许静默卡住"
        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_AUTOSTART
        copy = _copy_of(seen[0])
        assert "设置已经保存" in copy and "开机自启" in copy
        assert "一项都没写入" not in copy and "可能没有完整写入" not in copy, (
            "设置已经落盘，不许套用「改动可能没写入」那套话")
        assert finished == [int(QDialog.DialogCode.Rejected)], "答放弃/关窗必须真的走出去"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(1.0), (
            "设置本身必须已经落盘（异常只发生在自启这一步）")
    finally:
        _drop(dialog)


# ------------------------------------------------------------ 3. 放弃的边界（诚实）
def test_discard_reports_the_half_that_already_landed_instead_of_claiming_a_rollback(
        tmp_path, app, monkeypatch):
    """档案那一路被代次门挡住时，全局那一路可能已经写进 config.json——必须如实说。

    "放弃未保存的改动"只对**还没写成功**的部分成立：``SlotRoutedConfig.save()`` 先写档案
    再写主配置，档案被挡住时全局那半通常已经落盘。文案既不许说"已回滚"，也不许把已经
    生效的全局改动说成"一项都没写"。
    """
    main = _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    try:
        dialog.pet_opacity_spin.setValue(35)          # 全局键（走主 config.json）
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))  # 每宠键
        _rebuild_pet(tmp_path)

        _close_with_escape(dialog, app)

        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_STALE
        assert failure.global_written is True, "全局那一路确实落盘了"
        assert failure.slot_written is False, "档案那一路被代次门挡住，没写"
        copy = _copy_of(seen[0])
        assert "已经写入的部分" in copy and "全局设置" in copy
        assert "不会撤回" in copy
        assert "已回滚" not in copy and "全部撤回" not in copy, "做不到的承诺不许写"
        assert json.loads(Path(main.path).read_text(encoding="utf-8"))["pet_opacity"] == 35, (
            "文案说的「已写入」必须与盘上事实一致")
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)


# ------------------------------------------------------------ 4. 普通失败也能安全出去
def test_plain_disk_failure_can_be_discarded_and_closes(tmp_path, app, monkeypatch):
    """普通写盘失败（不是代次冲突）：文案说磁盘，出口一样有，选放弃照样关窗。"""
    from PySide6.QtWidgets import QDialog

    from pet import config_write_lock as cwl

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    baseline = _slot_path(tmp_path).read_bytes()
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))

    def boom(path, data):
        raise cwl.AtomicWriteFailed("磁盘写入失败（用例注入）")

    # 注入点放在夹具就绪之后：主配置落种那一步也走 pet_profiles 的原子写
    monkeypatch.setattr(cwl, "atomic_write_json", boom)
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))

        _close_with_escape(dialog, app)

        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_DISK
        assert failure.status == "write-failed"
        assert "配置未能写入磁盘" in seen[0]["text"]
        assert finished == [int(QDialog.DialogCode.Rejected)]
        assert _slot_path(tmp_path).read_bytes() == baseline, "写盘失败时一个字节都不许动旧档案"
    finally:
        _drop(dialog)


def test_unexpected_exception_asks_instead_of_dropping_the_draft(tmp_path, app, monkeypatch):
    """未预期异常不许"记一笔日志就当写成功"：窗口留着、草稿留着，选择交给用户。

    旧实现 ``except Exception: logging.exception(...)`` 之后 ``return True``——异常一律
    当成"可以关窗"，草稿静默消失。
    """
    import shiboken6

    from pet.file_interpret import settings as settings_file_interpret

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=False, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()

    def explode(_dialog):
        raise RuntimeError("页面保存炸了（用例注入）")

    monkeypatch.setattr(settings_file_interpret, "save_file_interpret_settings", explode)
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))

        _close_with_escape(dialog, app)

        assert finished == [], "异常不等于可以关窗（草稿会丢）"
        assert shiboken6.isValid(dialog) and dialog.isVisible()
        assert float(dialog.scale_combo.currentData()) == pytest.approx(1.0)
        entry = seen[0]
        assert entry["failure"].kind == settings_slot_config.SAVE_FAILURE_UNKNOWN
        copy = _copy_of(entry)
        assert "未预期的错误" in entry["text"]
        assert "无法确认" in copy, "异常之后是否已部分写入确实不知道，文案不许说死"
    finally:
        _drop(dialog)


def test_unexpected_exception_can_still_be_discarded(tmp_path, app, monkeypatch):
    """异常路径的出口同样可达：选放弃即可关窗（异常不是"必须留下"的理由）。"""
    from PySide6.QtWidgets import QDialog

    from pet.file_interpret import settings as settings_file_interpret

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))

    def explode(_dialog):
        raise RuntimeError("页面保存炸了（用例注入）")

    monkeypatch.setattr(settings_file_interpret, "save_file_interpret_settings", explode)
    try:
        _close_with_escape(dialog, app)
        assert seen[0]["failure"].kind == settings_slot_config.SAVE_FAILURE_UNKNOWN
        assert finished == [int(QDialog.DialogCode.Rejected)]
    finally:
        _drop(dialog)


def test_invalid_menu_layout_gets_the_same_exit(tmp_path, app, monkeypatch):
    """表单校验没过（菜单布局无效）也是"保存没成"：同样不能被一个关不掉的窗口困住。"""
    from PySide6.QtWidgets import QDialog

    from pet.context_menus.registry import MENU_ACTIONS
    from pet.menu_layout import resolve_menu_layout

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    deep = {"version": 1, "layout_id": "user", "nodes": [{
        "type": "submenu", "id": "user.outer", "label": "外层", "children": [{
            "type": "submenu", "id": "user.inner", "label": "内层",
            "children": [{"type": "action", "id": "chat"}]}]}]}
    validation = resolve_menu_layout(deep, registered_actions=MENU_ACTIONS.ids,
                                    available_actions=MENU_ACTIONS.ids)
    assert validation.source == "fallback", "前提：这份布局确实过不了校验"
    dialog = _dialog(tmp_path)
    monkeypatch.setattr(dialog.menu_layout_editor, "value", lambda: deep)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        _close_with_escape(dialog, app)
        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_LAYOUT
        assert "submenu-depth-exceeded" in failure.reason
        assert "submenu-depth-exceeded" in _copy_of(seen[0]), "诊断必须出现在用户看到的那段话里"
        assert finished == [int(QDialog.DialogCode.Rejected)]
    finally:
        _drop(dialog)


# ------------------------------------------------------------ 5. 分类与文案的对照
@pytest.mark.parametrize("status,kind", [
    ("stale-generation", settings_slot_config.SAVE_FAILURE_STALE),
    ("refused-corrupt", settings_slot_config.SAVE_FAILURE_PROFILE),
    ("corrupt", settings_slot_config.SAVE_FAILURE_PROFILE),
    ("refused-unsupported-version", settings_slot_config.SAVE_FAILURE_PROFILE),
    ("write-failed", settings_slot_config.SAVE_FAILURE_DISK),
    ("lock-timeout", settings_slot_config.SAVE_FAILURE_DISK),
    ("lock-unavailable", settings_slot_config.SAVE_FAILURE_DISK),
])
def test_slot_status_classification_matches_the_pet_profiles_vocabulary(status, kind):
    """``pet_profiles`` 的失败词 → 界面类别：只有真的写盘/锁失败才算磁盘失败。"""
    assert settings_slot_config.classify_slot_status(status) == kind


@pytest.mark.parametrize("kind", [
    settings_slot_config.SAVE_FAILURE_STALE,
    settings_slot_config.SAVE_FAILURE_PROFILE,
    settings_slot_config.SAVE_FAILURE_LAYOUT,
    settings_slot_config.SAVE_FAILURE_AUTOSTART,
    settings_slot_config.SAVE_FAILURE_DISK,
    settings_slot_config.SAVE_FAILURE_UNKNOWN,
])
def test_only_a_real_write_failure_is_described_as_a_disk_failure(app, kind):
    """文案对照：**只有** ``SAVE_FAILURE_DISK`` 那一支可以说"配置未能写入磁盘"。

    这就是实机 §4 里说错的那句话：代次冲突被说成写盘失败，把排查方向指到磁盘与锁上。
    """
    box, keep, discard = settings_close_flow.build_save_failure_box(
        None, settings_slot_config.SaveFailure(kind=kind, status=kind, path="C:/x/config-slot-1.json"))
    try:
        copy = "\n".join((box.windowTitle(), box.text(), box.informativeText()))
        assert ("配置未能写入磁盘" in copy) is (kind == settings_slot_config.SAVE_FAILURE_DISK)
        assert keep.text() == KEEP_LABEL and discard.text() == DISCARD_LABEL
        assert box.defaultButton() is keep and box.escapeButton() is keep
    finally:
        box.deleteLater()


# ------------------------------------------------------------ 6. finalize 纪律
def test_normal_close_writes_once_and_does_not_rewrite_on_reject(tmp_path, app, monkeypatch):
    """正常关窗（X）只走一遍 ``_write_config``。

    旧实现里 ``closeEvent`` 在保存成功后调 ``super().closeEvent()``，Qt 内部再转一次
    ``reject()`` → 又一次整表写回：一次点 X 写两遍。``_close_decided`` 记下"这次关闭
    已经做过决定"，重入只关窗（也就不会再弹第二个框）。
    """
    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    calls: list = []
    real = dialog._write_config

    def counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(dialog, "_write_config", counted)
    dialog.show()
    app.processEvents()
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.close()
        app.processEvents()
        assert len(calls) == 1, "一次关窗只落盘一次（重入不得再写整表）"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(1.0)
    finally:
        _drop(dialog)


# ------------------------------------------------------------ 7. 独立设置进程形态
def test_discard_in_the_settings_process_releases_the_lock_for_the_next_open(
        tmp_path, app, monkeypatch):
    """真进程形态（``pet/__main__._exec_settings``）：放弃关窗 → 事件循环退出 → 锁释放。

    "能重新打开设置页"的判据是这把锁真的放开了（主进程按它判断是否已有设置页在跑），
    而不是"对话框对象还在不在"。
    """
    from PySide6.QtCore import QLockFile, Qt, QTimer
    from PySide6.QtTest import QTest

    from pet import __main__ as entry
    from pet.modern_settings_dialog import ModernSettingsDialog

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    config = Config(base=tmp_path, instance_id=f"slot-{SLOT}")
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    problems: list = []

    def drive():
        for widget in app.topLevelWidgets():
            if not isinstance(widget, ModernSettingsDialog):
                continue
            if getattr(widget.config, "dir", None) != config.dir:
                continue
            widget.scale_combo.setCurrentIndex(widget.scale_combo.findData(1.0))
            _rebuild_pet(tmp_path)
            QTest.keyClick(widget, Qt.Key.Key_Escape)
            return
        problems.append("设置对话框没出现")

    QTimer.singleShot(0, drive)
    watchdog = QTimer(app)
    watchdog.setSingleShot(True)
    watchdog.setInterval(10000)
    watchdog.timeout.connect(app.quit)   # 兜底：绝不挂死在事件循环里（真挂住会在断言处红）
    watchdog.start()
    try:
        assert entry._exec_settings(app, config) == 0
    finally:
        # app 是进程级单例：正常跑完必须拆雷，否则 10s 后这一发 quit 会落在后面
        # 用例的模态 exec 里（曾把 test_discard_unverified_notice 的通知框打没）。
        watchdog.stop()
    assert problems == []
    assert len(seen) == 1
    assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    lock = QLockFile(str(config.dir / "settings.lock"))
    assert lock.tryLock(0), "放弃关窗后 settings.lock 必须已释放（否则设置页再也打不开）"
    lock.unlock()


# ------------------------------------------------------------ 8. 放弃 = 真的作废草稿（进程内表单视图）
#: astra 增量审查 CF01（``.scratch/product-final-review-astra-20261010/REVIEW-CLOSE-FLOW.md``）：
#: 上面那些用例都用**新建**的 Config 构造路由、且 ``standalone=True``，所以关窗之后没有
#: 任何人还持有那份路由——草稿跟着对象一起消失，缺口因此躲过了 27 项用例。产品真实支持的
#: 进程内 fallback（``settings_process_isolation=False``，或 ``startDetached`` 失败回退）不是
#: 这样：``app.py:1369``/``:3740`` 把 ``SlotRoutedConfig`` 直接挂在 ``PetInstance`` 上，
#: 而设置窗拿到的是它自己那份**表单视图**（``SlotRoutedConfig.new_form_view``，同一 ``_instance``
#: / ``_main``，草稿与编辑基线各自一份）：关窗只是关窗，草稿还活在这份视图里，下一次
#: **无关**保存（例如宠物菜单里改播放速度）会把它一起 flush 落盘。
#:
#: 下面这一组钉住的就是这条真实 seam：真 ``Config`` + 真 ``SlotRoutedConfig`` + 真关闭闸门
#: （``standalone=False``、运行期那份视图与表单视图**同时存在**），只有"这一次写盘失败"与
#: "用户点了哪个按钮"两处是注入的。


def _shared_route(tmp_path):
    """运行期那只手 + 主配置：进程内 fallback 的形态（设置窗关掉之后它仍然活着）。"""
    main = _main_config(tmp_path)
    identity = Config(base=tmp_path, instance_id=f"slot-{SLOT}")
    return settings_slot_config.SlotRoutedConfig(identity, main, SLOT), main


def _shared_dialog(shared):
    """开一张设置表单：**独立编辑视图 + 独立全局内存视图**，只有持久真值共用。

    对象 identity 不作为"隔离了"的判据（astra CF01-GLOBAL 的教训：登记表独立 ≠ 草稿独立）；
    这里只用它排除"又是同一个对象"，真正的隔离由第 8 节的行为交错用例钉住。
    """
    from pet.modern_settings_dialog import ModernSettingsDialog

    dialog = ModernSettingsDialog(shared, include_ai=False, standalone=False)
    form = dialog.config
    assert form is not shared, "表单必须是**独立**的编辑视图（草稿与编辑基线各自一份）"
    assert form._main is not shared._main, (
        "全局草稿真正的存放处是 Config.data：独立视图必须有自己的那一份（astra CF01-GLOBAL）")
    assert (str(form._main.path), form._main.instance_id) == (
        str(shared._main.path), shared._main.instance_id), "持久真值只有一处：同一个 config.json"
    assert (form.dir, form.instance_id) == (shared.dir, shared.instance_id)
    return dialog


def _notices(monkeypatch) -> list:
    """捕获"放弃没能与磁盘核对"那条可见诊断（``QMessageBox.warning``：通知型，无返回分支）。"""
    from PySide6.QtWidgets import QMessageBox

    seen: list = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *args, **kwargs: seen.append((args, kwargs))))
    return seen


def test_discard_revokes_the_slot_draft_before_a_later_unrelated_save(
        tmp_path, app, monkeypatch):
    """CF01：进程内共享路由上，放弃必须真的作废"还没写成功"的每宠草稿。

    复现（真配置面、真闸门，只注入"这次档案写失败"）：盘上 ``scale=0.8`` → 表单改成 1.0 →
    档案写失败 → 用户选「放弃未保存的改动并关闭」。旧实现只记了"这次关闭已经决定"：
    ``_pending_slot`` 里的 1.0 留在**运行期**那个对象上，之后用户改一个无关设置
    （播放速度，同为每宠键）保存时，那个 pending 会被一起写进档案——用户明确放弃的
    尺寸改动重新生效（astra 探针里的 ``disk_scale_after_later_save = 1.0``）。
    """
    from PySide6.QtWidgets import QDialog

    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _shared_dialog(shared)
    form = dialog.config
    config_dir = _config_dir(tmp_path)
    slot_settings = pet_profiles.slot_profile_path(config_dir, SLOT)
    real_write = pet_profiles.write_profile_settings

    def flaky(config_dir_, slot, values, **kwargs):
        if int(slot) == SLOT:
            return pet_profiles.ProfileResult(
                status="write-failed", path=slot_settings, reason="磁盘写入失败（用例注入）")
        return real_write(config_dir_, slot, values, **kwargs)

    monkeypatch.setattr(pet_profiles, "write_profile_settings", flaky)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    notices = _notices(monkeypatch)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        _close_with_escape(dialog, app)

        assert len(seen) == 1, "一次关闭只弹一个失败框"
        assert finished == [int(QDialog.DialogCode.Rejected)], "选了放弃就必须关窗"
        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_DISK
        assert failure.status == "write-failed"
        assert notices == [], '磁盘核对成功时不该出现「没能核对」的诊断'
        assert form._pending_slot == {}, "放弃之后本窗还没写成功的草稿必须清空"
        assert shared._pending_slot == {}, "运行期那份视图本来就不该承载表单草稿"
        assert shared.get("scale") == pytest.approx(0.8), (
            "运行视图必须回到可信落盘值（否则被放弃的值会先被运行时用上）")

        # 故障恢复后的无关保存（实机复现里的"改播放速度"）：
        monkeypatch.setattr(pet_profiles, "write_profile_settings", real_write)
        shared.set("playback_speed", 1.5)
        assert shared.save() is True
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(0.8), (
            '被放弃的 scale 不许被后续无关保存顺手写回（CF01 本体）')
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("playback_speed") == pytest.approx(1.5)
        assert shared.get("scale") == pytest.approx(0.8)

        # 再次打开设置：新的一份路由读到的就是盘上那份（没有草稿残留）
        reopened = _shared_dialog(shared)
        try:
            assert float(reopened.scale_combo.currentData()) == pytest.approx(0.8)
        finally:
            _drop(reopened)
    finally:
        _drop(dialog)


def test_discard_revokes_the_global_nested_draft(tmp_path, app, monkeypatch):
    """CF01 的主配置那一半：嵌套 dict 的全局草稿同样必须作废，且不许随后被合并写回。

    ``Config.save()`` 失败时只保证"磁盘没被改坏"——它把内存还原成**进入本次保存前**的
    那一份，而那份已经带着用户的草稿（``set()`` 改的就是同一份内存）。所以"清空待写区"
    不够：不把内存退回成功基线，下一次无关保存会把这个嵌套草稿按叶键合并写回去。
    """
    import sys

    from PySide6.QtWidgets import QDialog

    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    assert main.get("dynamic_island")["enabled"] is True, "前提：盘上灵动岛是开着的"
    dialog = _shared_dialog(shared)
    form = dialog.config
    main_path = Path(main.path)
    real_payload = Config._write_save_payload

    def boom(self, path, payload):
        if Path(path) == main_path:
            raise OSError("磁盘写入失败（用例注入：只顶主配置这一次）")
        return real_payload(self, path, payload)

    monkeypatch.setattr(Config, "_write_save_payload", boom)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    notices = _notices(monkeypatch)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        assert hasattr(dialog, "island_enabled_check"), "灵动岛开关是嵌套全局键 dynamic_island 的写入口"
        if sys.platform != "win32":
            pytest.skip("灵动岛开关在非 Windows 上不建控件（与产品分支一致）")
        dialog.island_enabled_check.setChecked(False)      # 嵌套草稿：dynamic_island.enabled
        _close_with_escape(dialog, app)

        assert len(seen) == 1 and finished == [int(QDialog.DialogCode.Rejected)]
        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_DISK
        assert failure.status == "main-config"
        assert (failure.slot_written, failure.global_written) == (True, False), (
            "档案那一路写成了、主配置那一路没写成")
        assert notices == [], '主配置读得到，不该报「没能核对」'

        monkeypatch.setattr(Config, "_write_save_payload", real_payload)
        assert form._pending_main == {}, "放弃之后全局草稿必须清空"
        assert shared._pending_main == {}, "运行期那份视图本来就不该承载表单草稿"
        assert shared.get("dynamic_island")["enabled"] is True, "运行视图必须回到盘上那份"

        shared.set("pet_opacity", 44)
        assert shared.save() is True
        disk = json.loads(main_path.read_text(encoding="utf-8"))
        assert disk["dynamic_island"]["enabled"] is True, (
            "被放弃的嵌套草稿不许被后续无关保存按叶键合并写回（CF01 主配置半边）")
        assert disk["pet_opacity"] == 44, "无关的那一项照常写成功"

        # 再次打开设置：盘上是什么，新窗口就显示什么
        fresh = _shared_dialog(_shared_route(tmp_path)[0])
        try:
            assert fresh.island_enabled_check.isChecked() is True
        finally:
            _drop(fresh)
    finally:
        _drop(dialog)


def test_discard_keeps_the_half_that_already_landed(tmp_path, app, monkeypatch):
    """放弃的边界：已经写成功的那一半**不许撤回**（两个方向各验一次）。

    ``SlotRoutedConfig.save()`` 先写档案、再写主配置，所以失败可能一半落盘：
    档案被代次门挡住时全局那半常常已经写进去了；反过来主配置写失败时，档案那半
    也已经生效。放弃只作废"还没写成功"的草稿，不碰已经在盘上、也已经在运行视图里的
    那一半——"已回滚"是做不到的承诺。
    """
    from PySide6.QtWidgets import QDialog

    # —— 方向一：档案被代次门挡住（没写），全局写成了 ——
    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _shared_dialog(shared)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.pet_opacity_spin.setValue(35)
        _rebuild_pet(tmp_path)
        _close_with_escape(dialog, app)

        assert finished == [int(QDialog.DialogCode.Rejected)]
        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_STALE
        assert (failure.slot_written, failure.global_written) == (False, True)
        assert shared.get("pet_opacity") == 35, "已经写成功的那一半必须留在运行视图里"
        assert shared.get("scale") == pytest.approx(0.8), "没写成的草稿必须被作废"
        # 后续无关保存（全局键）：不撤回已写成的，也不写回被放弃的
        shared.set("show_dock_icon", False)
        assert shared.save() is True
        disk = json.loads(Path(_main.path).read_text(encoding="utf-8"))
        assert disk["pet_opacity"] == 35, "已落盘的那一半不许被撤回"
        assert disk["show_dock_icon"] is False
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)

    # —— 方向二：档案写成了（保留），主配置写失败（草稿作废）——
    shared2, main2 = _shared_route(tmp_path)
    dialog2 = _shared_dialog(shared2)
    form2 = dialog2.config
    real_payload = Config._write_save_payload
    main_path2 = Path(main2.path)

    def boom(self, path, payload):
        if Path(path) == main_path2:
            raise OSError("磁盘写入失败（用例注入：只顶主配置这一次）")
        return real_payload(self, path, payload)

    monkeypatch.setattr(Config, "_write_save_payload", boom)
    seen2: list = []
    _answer_with(monkeypatch, discard=True, seen=seen2)
    finished2: list = []
    dialog2.finished.connect(lambda code: finished2.append(int(code)))
    try:
        dialog2.scale_combo.setCurrentIndex(dialog2.scale_combo.findData(1.0))
        dialog2.pet_opacity_spin.setValue(35)
        _close_with_escape(dialog2, app)

        assert finished2 == [int(QDialog.DialogCode.Rejected)]
        failure2 = seen2[0]["failure"]
        assert (failure2.slot_written, failure2.global_written) == (True, False)
        monkeypatch.setattr(Config, "_write_save_payload", real_payload)
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(1.0), (
            "档案那一路已经写成功，放弃不许把它撤回来")
        assert shared2.get("scale") == pytest.approx(1.0), "写成功的那一半也要留在运行视图里"
        assert json.loads(main_path2.read_text(encoding="utf-8"))["pet_opacity"] != 35, (
            "主配置那一半没写成，不许出现在盘上")
        assert shared2._pending_main == {}, "没写成的那一半草稿必须作废"
        assert form2._pending_main == {}, "表单视图里的草稿同样必须作废"
    finally:
        _drop(dialog2)


def test_discard_does_not_revert_an_external_newer_profile_value(tmp_path, app, monkeypatch):
    """代次被合法写者推进、值也换了：放弃既不许回退磁盘，也不许把旧代次放行。

    实机场景：设置窗开着的时候这只宠被重新生成 / 被别的合法写者改过档案（代次推进、
    值也换了）。旧表单的保存被代次门挡住 → 用户放弃 → 盘上必须是**外部那一份**，
    运行视图也要回到它；而这张旧表单自己再保存一次仍然要被挡住（编辑代次不许被"放弃"
    洗掉，否则旧控件值会盖到新代次上）。
    """
    from PySide6.QtWidgets import QDialog

    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _shared_dialog(shared)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    config_dir = _config_dir(tmp_path)
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))

        # 外部合法写者：先按当前代次改值（0.8 → 0.9），再推进代次（壳重建/别处更新
        # 就是这两步：写入是合法写者，代次推进让旧表单失去写资格）
        snapshot = pet_profiles.read_slot_snapshot(config_dir, SLOT)
        external = pet_profiles.write_profile_settings(
            config_dir, SLOT, {"scale": 0.9}, generation=snapshot.generation)
        assert external.status == "ok", external
        _rebuild_pet(tmp_path)
        assert pet_profiles.read_slot_snapshot(config_dir, SLOT).generation != snapshot.generation

        _close_with_escape(dialog, app)

        assert finished == [int(QDialog.DialogCode.Rejected)]
        assert seen[0]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(0.9), (
            "外部写者的新值不许被回退（既不写 1.0，也不写回 0.8）")
        assert shared.get("scale") == pytest.approx(0.9), "运行视图必须回到盘上那份（外部新值）"

        # 后续无关的全局保存：只动主配置，与档案代次无关 → 照常成功；外部的档案值不动
        shared.set("show_dock_icon", False)
        assert shared.save() is True
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(0.9)

        # 还在这张旧表单里时：再点一次关闭仍然被代次门挡住，一个字节都不写
        _answer_with(monkeypatch, discard=False, seen=seen)      # 这次选「继续编辑」
        _close_with_escape(dialog, app)
        assert seen[-1]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert float(dialog.scale_combo.currentData()) == pytest.approx(1.0), "草稿仍在控件里"
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(0.9), (
            '编辑代次不许被「放弃」洗掉（洗掉就等于放行旧控件值）')
    finally:
        _drop(dialog)


def test_reopened_settings_pins_the_current_generation_and_can_save(
        tmp_path, app, monkeypatch):
    """重开设置必须从**新磁盘代次**取编辑基线（旧表单仍然被挡）——真实用户死路收口。

    死路复现（本次修复前）：外部推进代次后旧表单被判 stale，产品文案让用户"关闭本窗口后
    重新打开设置"；进程内 fallback 下设置窗拿到的是**同一个**路由对象，旧基线一直留在
    上面，重开之后每次保存仍然被判 stale——重开永远救不回来，用户只能重启应用。

    修法：每张表单一份**独立编辑视图**（``SlotRoutedConfig.new_form_view``），新视图第一次
    读盘就是它自己的编辑基线；旧表单那份基线不受影响（它还在手上时照样被挡）。
    """
    from PySide6.QtWidgets import QDialog

    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    config_dir = _config_dir(tmp_path)
    first = _shared_dialog(shared)
    seen: list = []
    _answer_with(monkeypatch, discard=False, seen=seen)          # 旧表单：选「继续编辑」
    first.show()
    app.processEvents()
    try:
        first.scale_combo.setCurrentIndex(first.scale_combo.findData(1.0))
        _rebuild_pet(tmp_path)                                   # 外部推进代次

        _close_with_escape(first, app)
        assert seen[-1]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert first.isVisible(), "选了继续编辑：旧表单必须留着"
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(0.8)

        # 关掉旧表单（放弃这次草稿），再打开设置
        _answer_with(monkeypatch, discard=True, seen=seen)
        _close_with_escape(first, app)
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(0.8)

        second = _shared_dialog(shared)
        try:
            assert float(second.scale_combo.currentData()) == pytest.approx(0.8), "新表单读的是盘上那份"
            second.scale_combo.setCurrentIndex(second.scale_combo.findData(1.0))
            second.close()                                        # 正常保存关窗（不再注入任何选择）
            app.processEvents()
            assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(1.0), (
                "重开之后必须能存进去（修复前这里永远被判 stale-generation）")
        finally:
            _drop(second)

        # 运行期那份视图刷新后读到新值（生产里由 PetInstance._modern_settings_finished 触发）
        shared.refresh_profile_view()
        assert shared.get("scale") == pytest.approx(1.0)
    finally:
        _drop(first)


def test_settings_write_through_is_visible_to_the_runtime_view_at_once(tmp_path, app, monkeypatch):
    """表单成功落盘后，**运行期那份视图**立刻读到新值（表单独立了，写盘要通知它）。

    两份视图的代价：表单写盘碰不到运行期那份的只读快照缓存。运行期先读过一次
    （缓存住旧值 = 真实形态），表单保存关窗后它必须重读——否则"设置保存成功但桌宠
    不变"，要等下一次别的原因重读才生效；"即时生效"行（``click_sound_enabled`` 这类
    ``toggled`` 直连写盘的开关）更是要求当场可见（``settings_default_marks`` 有专门用例）。
    """
    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    assert shared.get("scale") == pytest.approx(0.8)      # 运行期视图先读一次（缓存旧值）

    dialog = _shared_dialog(shared)
    form = dialog.config
    try:
        dialog.show()                        # 真关窗要有真窗口（closeEvent 才发）
        app.processEvents()
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.close()                       # 真关闭：保存成功
        app.processEvents()
        assert form._pending_slot == {} and form._pending_main == {}
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(1.0)
        assert shared.get("scale") == pytest.approx(1.0), (
            "运行期视图必须立刻读到新值（否则保存了但桌宠不变）")
    finally:
        _drop(dialog)


def test_discard_does_not_revert_an_external_reset(tmp_path, app, monkeypatch):
    """外部「恢复默认（本宠）」推进代次并重置偏好：旧表单放弃后盘上必须是重置那一份。

    场景：设置窗开着 → 用户从菜单/另一个入口对这只宠点了「恢复默认」（`reset_slot_profile`
    是合法写者，会推进代次） → 旧表单再关窗被判 stale → 放弃。放弃只作废**还没写成功**的
    草稿，不许把外部那次重置回滚掉，也不许把旧代次写回去。
    """
    from PySide6.QtWidgets import QDialog

    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    config_dir = _config_dir(tmp_path)
    default_scale = pet_profiles.same_source_default("scale")
    dialog = _shared_dialog(shared)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))

        snapshot = pet_profiles.read_slot_snapshot(config_dir, SLOT)
        reset = pet_profiles.reset_slot_profile(config_dir, SLOT, generation=snapshot.generation)
        assert reset.status in ("ok", "no-profile"), reset
        after_reset = pet_profiles.read_slot_snapshot(config_dir, SLOT)
        assert after_reset.status == "ok" and after_reset.generation != snapshot.generation
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(default_scale)

        _close_with_escape(dialog, app)

        assert finished == [int(QDialog.DialogCode.Rejected)]
        assert seen[0]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(default_scale), (
            "外部那次重置不许被回滚（既不写 1.0，也不写回 0.8）")
        assert pet_profiles.read_slot_snapshot(config_dir, SLOT).generation == after_reset.generation, (
            "旧代次不许被写回")
        assert shared.get("scale") == pytest.approx(default_scale), "运行视图回到盘上那份"

        # 后续无关保存照常成功，且不回滚档案
        shared.set("show_dock_icon", False)
        assert shared.save() is True
        assert pet_profiles.read_slot_profile(config_dir, SLOT).value("scale") == pytest.approx(default_scale)
    finally:
        _drop(dialog)


def test_discard_reports_when_the_disk_could_not_be_re_read(tmp_path, app, monkeypatch):
    """放弃时磁盘重读失败：草稿照样作废，但必须**可见地**说明「没能核对磁盘」。

    重读可能是锁被占用、也可能是文件读不到/内容不可信。此时内存退回的是最后一次
    成功保存/读取的基线（上面没有草稿），所以"被放弃的值不会再落盘"仍然成立；但
    "共享视图就是磁盘现状"这句话此时**不成立**，不能让用户以为放弃完全成功。
    """
    from PySide6.QtWidgets import QDialog

    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _shared_dialog(shared)
    form = dialog.config
    main_path = Path(main.path)
    good_config = main_path.read_text(encoding="utf-8")
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    notices = _notices(monkeypatch)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    try:
        dialog.pet_opacity_spin.setValue(35)
        main_path.write_text('{ "pet_opacity": 55, broken', encoding="utf-8")   # 主配置读不动了
        _close_with_escape(dialog, app)

        assert len(seen) == 1 and finished == [int(QDialog.DialogCode.Rejected)], "核对失败也要能关窗"
        failure = seen[0]["failure"]
        assert failure.kind == settings_slot_config.SAVE_FAILURE_DISK
        assert notices, "没能核对磁盘必须有可见诊断，不许只用日志代替"
        _args, _kwargs = notices[0]
        copy = "\n".join(str(item) for item in _args[1:])
        assert "没能核对磁盘" in copy or "没能确认" in copy, '诊断要说清「这次没能核对磁盘」'
        assert str(main_path) in copy, "诊断必须点名是哪个文件没读动"
        assert "已经作废" in copy, '草稿确实作废了，这一点也要说清（不许含糊成「没放弃」）'

        assert shared._pending_slot == {} and shared._pending_main == {}
        assert form._pending_slot == {} and form._pending_main == {}, "表单视图的草稿同样作废"
        assert main.data.get("pet_opacity") == 55, "内存退回成功基线：被放弃的 35 不许留着"
        assert shared.get("scale") == pytest.approx(0.8), "档案那一路不受影响（读得到，视图照旧）"

        # 修好文件之后：被放弃的值不许借"下一次无关保存"复活
        main_path.write_text(good_config, encoding="utf-8")
        shared.set("show_dock_icon", False)
        assert shared.save() is True
        disk = json.loads(main_path.read_text(encoding="utf-8"))
        assert disk["pet_opacity"] == 55, "被放弃的 35 不许被后续无关保存写回"
        assert disk["show_dock_icon"] is False
    finally:
        _drop(dialog)


def test_discard_unverified_notice_is_a_real_visible_box(tmp_path):
    """那条诊断是**真的弹出来**的（真确认框 + 真通知框、真点掉），不是只写日志。

    其余用例里 ``QMessageBox.warning`` 是 conftest 的防卡桩；这一条在干净子进程里
    跑产品原件（连确认框也是真的），证明"可见诊断"这条承诺真的兑现在界面上。
    子进程化的原因见 ``_run_realmodal_driver``。
    """
    result = _run_realmodal_driver(tmp_path, "unverified-notice")

    assert result["clicked"] == [DISCARD_LABEL], "确认真框里点了「放弃未保存的改动并关闭」"
    assert len(result["boxes"]) == 2, "先确认框、后通知框，各一个"
    notice = result["boxes"][1]
    # 标题不能单独当判据：macOS 的 QMessageBox 走系统原生对话框，``windowTitle()``
    # 在那边恒为空（仓库既有结论见 tests/test_harness_lifecycle.py）。「没能核对磁盘」
    # 这句话标题与正文都说了，按标题+正文拼起来的可见文案钉。
    assert "磁盘" in "\n".join((notice["title"], notice["text"]))
    assert "已经作废" in notice["text"] and result["main_path"] in notice["text"]


# ------------------------------------------------------------ 9. 设置窗挡住不可逆收口（ops P3-c）
#: 顺序纪律：设置页闸门必须在**任何**不可逆收口之前（存位置 / 停预热 / 关 Agent / 关会话
#: 写盘 worker / 摘 runtime 标记 / 关父窗 / 丢 instance）。用户选「继续编辑」→ 整次收口
#: **取消**，一个资源都不动；选「放弃」→ 收口照常走完。放在收口中途 return 只会留下半死的
#: 宠物，所以闸门在方法第一行（``_on_window_exit_requested`` 顶部）与用户退出应用入口
#: （``AppShell.request_app_quit``）。
#:
#: 这里用 ``AppShell.__new__`` 的最小桩（仓库既有测试桩做法）+ **真** ModernSettingsDialog +
#: 真关闭闸门 + 真配置目录，只有"用户点了哪个按钮"是注入的。


def _exit_host(tmp_path, app, monkeypatch, *, keep_editing: bool):
    """一只可被「退出这只」的窗 + 真设置页 + 只记调用序列的收口桩。

    返回 ``(shell, inst, dialog, calls, seen)``；``calls`` 里出现的每个名字都代表一次
    **不可逆**收口动作，闸门没拦住就会留下痕迹。
    """
    from types import SimpleNamespace

    from pet.app import AppShell

    shared, _main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _shared_dialog(shared)
    dialog.show()
    seen: list = []
    _answer_with(monkeypatch, discard=not keep_editing, seen=seen)
    calls: list = []

    shell = AppShell.__new__(AppShell)
    shell.app = SimpleNamespace(quit=lambda: calls.append("quit"))
    shell.instance = SimpleNamespace()                 # slot0 占位（被退的不是它）
    shell._instances = []
    shell._refresh_tray_menu = lambda: calls.append("refresh_tray")
    shell._close_instance_session_writer = lambda inst: calls.append("close_session_writer")
    shell._enqueue_heavy_teardown = lambda inst, jobs: calls.append("heavy_teardown")
    win = SimpleNamespace(
        save_position=lambda: calls.append("save_position"),
        lib=None, agent_link_manager=None, remove_runtime_marker=lambda: calls.append("remove_marker"),
        close=lambda: calls.append("win_close"),
    )
    inst = type("FakeInstance", (), {})()      # 支持 weakref（clear_spawned_pets 要）
    inst.win = win
    inst.slot_id = SLOT
    inst.modern_settings_dialog = dialog
    inst.config = shared
    inst.legacy_chat_window = None
    inst.modern_chat_window = None
    inst.quick_chat = None
    inst.chat_settings_dialog = None
    inst.chat_window = None
    inst.broker_facade = SimpleNamespace(shutdown=lambda: calls.append("broker_shutdown"))
    shell._instances.append(inst)
    # 让设置页这次保存必然失败（外部推进了这只宠的档案代次），失败框才有出口可点
    dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
    _rebuild_pet(tmp_path)
    app.processEvents()
    return shell, inst, dialog, calls, seen


def test_refusing_the_settings_close_cancels_the_pet_exit_untouched(tmp_path, app, monkeypatch):
    """用户在设置页里选「继续编辑」→ 整次「退出这只」取消，一个资源都不动。

    修复前：闸门在 ``_close_instance_subwindows`` 里（``dialog.close()`` 完全不看返回值），
    发现"关不掉"时位置已存、预热已停、Agent 已关、会话 worker 已关，父窗随即销毁——
    草稿窗成孤儿跟着消失（ops P3-c）。
    """
    shell, inst, dialog, calls, seen = _exit_host(
        tmp_path, app, monkeypatch, keep_editing=True)
    try:
        shell._on_window_exit_requested(inst)

        assert seen, "闸门必须真的把失败框弹出来（不能悄悄放行）"
        assert seen[-1]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert calls == [], "用户选了继续编辑 → 存位置/停预热/关服务/关窗/丢引用一律不许发生"
        assert inst.modern_settings_dialog is dialog and dialog.isVisible(), "草稿窗必须完好"
        assert inst in shell._instances, "instance 不许被摘掉"
        assert inst.win is not None, "父窗引用不许被清"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)


def test_discarding_the_settings_close_lets_the_pet_exit(tmp_path, app, monkeypatch):
    """用户在设置页里选「放弃未保存的改动并关闭」→ 收口照常走完（宠物真的退出、资源释放）。

    "继续动作"的另一半：接受放弃之后这次关闭请求必须**照常生效**，不能因为加了闸门就变成
    谁也关不掉；关完引用断开，下一次打开设置能新建一张表单（进程内形态的"锁重新可开"，
    独立设置进程那份 settings.lock 由第 7 节的真 QLockFile 用例守着）。
    """
    shell, inst, dialog, calls, seen = _exit_host(
        tmp_path, app, monkeypatch, keep_editing=False)
    try:
        shell._on_window_exit_requested(inst)

        assert seen[-1]["failure"].kind == settings_slot_config.SAVE_FAILURE_STALE
        assert "save_position" in calls and "win_close" in calls, "接受放弃之后收口照常"
        assert "broker_shutdown" in calls and "refresh_tray" in calls
        assert "close_session_writer" in calls or "heavy_teardown" in calls
        assert inst not in shell._instances, "instance 必须被摘掉"
        assert inst.modern_settings_dialog is None, "引用必须断开"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8), (
            "用户放弃的草稿不许落盘")
    finally:
        _drop(dialog)

    # 设置页重新可开：运行期那份视图还在，新开一张表单拿到各自独立的草稿/基线
    reopened = _shared_dialog(inst.config)
    try:
        assert float(reopened.scale_combo.currentData()) == pytest.approx(0.8)
    finally:
        _drop(reopened)


def test_app_quit_waits_for_the_settings_window(tmp_path, app, monkeypatch):
    """托盘/恢复窗的「退出应用」同样先过设置页闸门：继续编辑 → 取消退出；放弃 → 照常退出。"""
    shell, inst, dialog, calls, seen = _exit_host(
        tmp_path, app, monkeypatch, keep_editing=True)
    try:
        shell.request_app_quit()
        assert calls == [], "用户要留在设置页里 → app.quit 都不许调"
        assert dialog.isVisible() and inst.modern_settings_dialog is dialog
    finally:
        _drop(dialog)

    shell2, inst2, dialog2, calls2, seen2 = _exit_host(
        tmp_path, app, monkeypatch, keep_editing=False)
    try:
        shell2.request_app_quit()
        assert calls2 == ["quit"], "接受放弃之后退出应用照常"
    finally:
        _drop(dialog2)


def test_clear_spawned_pets_cancels_before_touching_any_pet(tmp_path, app, monkeypatch):
    """一键清除（链式退出多只）同样**整批前置**过闸门：任一被拒 → 一只都不退、链不启动。

    中途才发现的话，前面的宠已经退出去了，"整个关闭请求取消"就名不副实——所以闸门在
    ``clear_spawned_pets`` 起点，不在链的每一节里。
    """
    shell, inst, dialog, calls, seen = _exit_host(
        tmp_path, app, monkeypatch, keep_editing=True)
    try:
        shell._clear_spawned_pending = False
        shell.clear_spawned_pets()
        app.processEvents()

        assert calls == [], "被拒时链不许启动：一个资源都不动"
        assert dialog.isVisible() and inst.modern_settings_dialog is dialog
        assert inst in shell._instances
        assert shell._clear_spawned_pending is False, (
            '取消时不许留下「进行中」标记，否则下一次点击被静默忽略')
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        _drop(dialog)


def test_session_end_paths_keep_the_no_dialog_semantics():
    """OS 强制会话结束那条路径**不弹**任何阻塞对话框：闸门只接用户显式的退出入口。

    机器可检：``_on_session_end`` / ``_on_about_to_quit`` 的源码里不许出现设置页闸门
    （``_settings_window_releasable`` / ``_release_settings_windows`` / ``request_app_quit``）
    ——Windows 注销/关机时弹模态框会把关机流程卡住，这是既有语义，不许被本次修复改变。
    """
    import ast

    source = Path("pet/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    checked = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name in (
                    "_on_session_end", "_on_about_to_quit"):
                segment = ast.get_source_segment(source, item) or ""
                checked.append(item.name)
                assert "_settings_window_releasable" not in segment, (
                    f"{item.name} 不许接设置页闸门（强制会话结束不弹阻塞框）")
                assert "request_app_quit" not in segment
    assert checked == ["_on_session_end", "_on_about_to_quit"] or set(checked) == {
        "_on_session_end", "_on_about_to_quit"}, f"这两个落点必须都找到，实际 {checked}"


# ------------------------------------------------------------ 8d. 全局草稿隔离（astra CF01-GLOBAL）
#: `new_form_view` 只隔离登记表不够：全局草稿真正的存放处是 ``Config.data``。下面两条是
#: astra 最后那轮点名的**行为交错**（不靠对象 identity 宣称独立）。


def test_failed_form_global_draft_is_not_committed_by_a_runtime_save(tmp_path, app, monkeypatch):
    """① 表单全局保存失败（用户选「继续编辑」）→ 运行期读到的仍是盘上值；运行期随后的
    无关保存不得把那份没写成的草稿一起提交。

    修复前：表单与运行期共用同一个 ``Config``——表单的 ``set`` 直接写进运行期内存
    （运行期读到 35），运行期的保存提交的是共享 Config 的**全部**差异，把 35 写进盘。
    """
    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    assert main.get("pet_opacity") == 55, "前提：盘上（与运行期）是 55"
    dialog = _shared_dialog(shared)
    form = dialog.config
    main_path = Path(main.path)
    real_payload = Config._write_save_payload

    def boom(self, path, payload):
        if Path(path) == main_path:
            raise OSError("磁盘写入失败（用例注入）")
        return real_payload(self, path, payload)

    # 类级注入 = 打在 I/O 边界上：表单用的是**自己那份** Config 实例，实例级注入不再命中它
    monkeypatch.setattr(Config, "_write_save_payload", boom)
    seen: list = []
    _answer_with(monkeypatch, discard=False, seen=seen)      # 用户选「继续编辑」
    dialog.show()
    app.processEvents()
    try:
        dialog.pet_opacity_spin.setValue(35)
        _close_with_escape(dialog, app)

        assert seen[-1]["failure"].status == "main-config"
        assert dialog.isVisible(), "继续编辑：窗口与草稿留着"
        assert form._pending_main.get("pet_opacity") == 35, "草稿留在**表单**这一侧"
        assert shared.get("pet_opacity") == 55, "运行期不许读到表单没保存成功的值"
        assert json.loads(main_path.read_text(encoding="utf-8"))["pet_opacity"] == 55, "盘上仍是 55"

        # 故障恢复后，用户从运行期改另一个全局项并保存（真实入口 pet/window.py set+save）
        monkeypatch.setattr(Config, "_write_save_payload", real_payload)
        shared.set("no_move", True)
        assert shared.save() is True
        disk = json.loads(main_path.read_text(encoding="utf-8"))
        assert disk["pet_opacity"] == 55, "表单没写成的 35 不许被运行期无关保存顺带提交"
        assert disk["no_move"] is True, "运行期自己那一项照常落盘"
        assert shared.get("pet_opacity") == 55
    finally:
        _drop(dialog)


def test_form_discard_keeps_the_runtime_own_draft(tmp_path, app, monkeypatch):
    """② 运行期自己有合法待写值时，另一张表单的 discard 不许把它清掉。

    修复前：表单 discard 回退的是**共享** ``_main.data`` → 运行期那份待写值一起被抹掉，
    运行期随后保存也不会重放它（盘上仍是旧值）。
    """
    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _shared_dialog(shared)
    form = dialog.config
    main_path = Path(main.path)
    real_payload = Config._write_save_payload

    def boom(self, path, payload):
        if Path(path) == main_path:
            raise OSError("磁盘写入失败（用例注入）")
        return real_payload(self, path, payload)

    monkeypatch.setattr(Config, "_write_save_payload", boom)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    try:
        shared.set("no_move", True)          # 运行期的合法待写值（还没落盘）
        assert shared._pending_main == {"no_move": True}

        dialog.pet_opacity_spin.setValue(35)  # 表单改一项，保存失败
        _close_with_escape(dialog, app)
        assert seen[0]["failure"].status == "main-config"

        assert form._pending_main == {}, "表单放弃后它自己的草稿清空"
        assert shared._pending_main == {"no_move": True}, "运行期的合法待写值不许被表单的放弃洗掉"
        assert shared.get("no_move") is True, "运行期内存里也还在"

        # 运行期自己保存：把它自己那一项写进盘（合并语义照旧）
        monkeypatch.setattr(Config, "_write_save_payload", real_payload)
        assert shared.save() is True
        disk = json.loads(main_path.read_text(encoding="utf-8"))
        assert disk["no_move"] is True, "运行期的待写值必须真的落盘（修复前这里被抹掉）"
        assert disk["pet_opacity"] == 55, "表单放弃的 35 不许出现"
    finally:
        _drop(dialog)


def test_two_views_save_different_leaves_and_keep_external_deltas(tmp_path, app, monkeypatch):
    """两张视图各存不同的叶：都在盘上；后一张不会把前一张的值按旧快照写回去。

    这一层测的是 ``Config.save`` 的既有语义（目录锁内重读 + 递归合并 + 编辑基线）在两个
    **独立** ``Config`` 实例之间照样成立——两视图不再共享 dirty 缓冲，靠的就是它。
    （对话框的整表写回对"同一键被两个窗口都暴露"仍是 last-writer-wins，那是既有明文契约，
    不在本条口径里。）
    """
    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    main_path = Path(main.path)

    first = _shared_dialog(shared).config        # 读到的基线：opacity 55
    second = _shared_dialog(shared).config       # 同样读到 55（在 first 写之前构造）

    first.set("pet_opacity", 35)
    assert first.save() is True
    assert json.loads(main_path.read_text(encoding="utf-8"))["pet_opacity"] == 35

    second.set("no_move", True)                  # 只存自己那一项
    assert second.save() is True
    disk = json.loads(main_path.read_text(encoding="utf-8"))
    assert disk["no_move"] is True
    assert disk["pet_opacity"] == 35, "第二张不许拿自己构造时的旧快照把第一张的值盖回去"
    second.reload()                              # 视图看别处写下的值走既有 reload 口径
    assert second.get("pet_opacity") == 35, "重读后跟上盘上现值"


# ------------------------------------------------------------ 10. 会话结束（注销/关机）的关闭路径
#: ops P2：Qt 在 aboutToQuit 之后还会 closeAllWindows，设置窗照样进 closeEvent。会话结束
#: 标记用现成的进程级闸门 ``pet.webm_clip.session_ending``（issue #111 同一个标记，conftest
#: 每个用例后自动复位），这里用真标记置位/复位，不用 monkeypatch 假函数。


def test_session_end_close_never_pops_a_dialog_and_does_not_delay(tmp_path, app, monkeypatch):
    """会话结束 + 保存会失败：不许弹失败框（关机被模态循环挂住），照常无确认关窗。"""
    from PySide6.QtWidgets import QDialog

    from pet import webm_clip

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=False, seen=seen)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        _rebuild_pet(tmp_path)                      # 这次保存必然失败（代次门）
        webm_clip.set_session_ending(True)          # 真标记置位
        assert webm_clip.session_ending() is True

        _close_with_escape(dialog, app)

        assert seen == [], "会话结束不许弹任何失败框（会让关机挂在一个没人看的模态框上）"
        assert finished == [int(QDialog.DialogCode.Rejected)], "无确认强制关窗、不延迟"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(0.8)
    finally:
        webm_clip.set_session_ending(False)         # 真复位
        assert webm_clip.session_ending() is False
        _drop(dialog)


def test_session_end_close_saves_best_effort_and_skips_autostart(tmp_path, app, monkeypatch):
    """会话结束 + 保存能成功：设置 best-effort 落盘；开机自启收尾跳过（下次启动收敛）。"""
    import pet.modern_settings_dialog as settings_mod

    from pet import webm_clip

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=True, seen=seen)
    calls: list = []
    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled",
                        lambda enabled: calls.append(bool(enabled)) or True)
    dialog.show()
    app.processEvents()
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.autostart_check.setChecked(not dialog._autostart_initial)   # 用户改了自启
        webm_clip.set_session_ending(True)

        _close_with_escape(dialog, app)

        assert seen == [], "会话结束路径不走失败框（这里也没失败）"
        assert calls == [], "关机窗口里不碰系统登录项（配置已落盘，下次启动收敛）"
        assert pet_profiles.read_slot_profile(_config_dir(tmp_path), SLOT).value("scale") == pytest.approx(1.0), (
            "best-effort 落盘：能写成的就写成")
        import json as _json
        from pathlib import Path as _Path

        cfg = _json.loads((_Path(_config_dir(tmp_path)) / "config.json").read_text(encoding="utf-8"))
        assert cfg["autostart_wanted"] is (not dialog._autostart_initial), (
            "自启配置值本身落盘（只是 OS 侧收尾留到下次启动）")
    finally:
        webm_clip.set_session_ending(False)
        _drop(dialog)


def test_session_end_does_not_mask_a_normal_close(tmp_path, app, monkeypatch):
    """标记没置位时，普通关闭照旧弹失败框并允许「继续编辑」——会话结束分支不许外溢。"""
    from pet import webm_clip

    _main_config(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    dialog = _dialog(tmp_path)
    seen: list = []
    _answer_with(monkeypatch, discard=False, seen=seen)
    dialog.show()
    app.processEvents()
    try:
        assert webm_clip.session_ending() is False, "前提：不在会话结束窗口里"
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        _rebuild_pet(tmp_path)

        _close_with_escape(dialog, app)

        assert len(seen) == 1, "普通关闭必须照旧询问"
        assert dialog.isVisible(), "选继续编辑就留在窗口里"
    finally:
        _drop(dialog)


# ------------------------------------------------------------ 8e. absorb 不许重引 nested 丢更新
#: 2026-10-11 父级复核：上一版 ``absorb_persisted_globals`` 用 reload + 整值 ``_pending_main``
#: 重放，恰好把 astra 早先修过的"同字典异叶丢更新"重新引了一遍（换函数名不换错）。现在的口径
#: 是：本视图**有未写差异时什么都不做**（保住自己的 baseline，由 ``Config.save`` 的递归合并
#: 提交它自己的相对差异）；干净才 reload。下面三条钉住这件事，全走真实链路
#: （``new_form_view`` → ``set``/``save`` → 自动 absorb）。


def _island(shared) -> dict:
    value = shared.get("dynamic_island")
    return dict(value) if isinstance(value, dict) else {}


def test_form_save_of_one_nested_leaf_keeps_the_runtimes_other_leaf(tmp_path, app, monkeypatch):
    """同一嵌套字典里两个视图各改一个叶：后保存的那个不许把先保存的那个盖回去。

    runtime 只改 ``dynamic_island.opacity``（整值 pending 里 ``accent`` 仍是旧值）；另一张
    表单独立改了同一个字典的 ``accent`` 并保存成功（自动触 absorb）。runtime 随后保存：
    ``Config.save`` 按 ``_save_baseline`` 的相对差异递归合并 → 只提交 ``opacity``，
    ``accent`` 必须还是表单写进去的新值。

    上一版（reload + 整值重放）在这里会把 ``accent`` 按旧快照盖回内存，再被这次保存当成
    "本地差异"提交 → 表单刚写成功的 ``accent`` 被静默覆盖。
    """
    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    main_path = Path(main.path)
    base = _island(shared)
    old_accent = base.get("accent")

    runtime_island = {**base, "opacity": 0.6}          # 运行期：只改一个叶（整值登记）
    shared.set("dynamic_island", runtime_island)
    assert shared._pending_main["dynamic_island"]["opacity"] == pytest.approx(0.6)

    form = _shared_dialog(shared).config               # 另一张表单：改同一个字典的另一个叶
    form.set("dynamic_island", {**base, "accent": "purple"})
    assert form.save() is True                         # 成功 → 自动 absorb（上一版在这里出错）
    disk = json.loads(main_path.read_text(encoding="utf-8"))
    assert disk["dynamic_island"]["accent"] == "purple", "表单那一叶已经落盘"

    # read 契约：运行期脏的时候读的是**自己的草稿**（不临时反映别人的新值），
    # 但它自己的叶一个都不许被动过。
    assert shared.get("dynamic_island")["opacity"] == pytest.approx(0.6), "运行期的草稿还在"
    assert shared.get("dynamic_island")["accent"] == old_accent, (
        "脏视图的读契约 = 本地草稿优先（允许滞后，不允许被别人的值盖掉）")

    assert shared.save() is True                       # 运行期自己保存
    disk = json.loads(main_path.read_text(encoding="utf-8"))
    assert disk["dynamic_island"]["accent"] == "purple", (
        "运行期保存不许把表单写进去的 accent 盖回旧值（nested 同字典异叶丢更新）")
    assert disk["dynamic_island"]["opacity"] == pytest.approx(0.6), "运行期自己那一叶照常落盘"

    shared.reload()                                    # 干净之后重读：两叶都在
    assert _island(shared)["accent"] == "purple"
    assert _island(shared)["opacity"] == pytest.approx(0.6)


def test_form_save_keeps_a_direct_runtime_data_delta(tmp_path, app, monkeypatch):
    """运行期**直接改 ``_main.data``**（不经过 ``set``/``_pending_main``）的差异同样不许被抹掉。

    上一版只重放登记表里的键：直接改 data 的嵌套叶会被 reload 换掉（那条差异没进登记表，
    保护不到），随后运行期保存也不会重放它。
    """
    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    main_path = Path(main.path)
    base = _island(shared)
    shared._main.data["dynamic_island"] = {**base, "opacity": 0.7}   # 绕过 set()
    assert shared._pending_main == {}, "前提：这条差异不在登记表里"
    assert shared._main.has_local_changes is True, "但它是本视图的未写差异"

    form = _shared_dialog(shared).config
    form.set("dynamic_island", {**base, "accent": "purple"})
    assert form.save() is True

    assert shared.get("dynamic_island")["opacity"] == pytest.approx(0.7), "直接改的那一叶必须还在"
    shared.set("no_move", True)                        # 登记表里的另一项触发一次真实保存
    assert shared.save() is True
    disk = json.loads(main_path.read_text(encoding="utf-8"))
    assert disk["dynamic_island"]["accent"] == "purple", "表单那一叶不许被旧快照盖回去"
    assert disk["dynamic_island"]["opacity"] == pytest.approx(0.7), (
        "直接改的那一叶同样要被这次保存的相对差异合并提交（不能只保护登记表里的键）")
    assert disk["no_move"] is True


def test_external_global_reset_does_not_wash_the_runtime_dirty_token(tmp_path, app, monkeypatch):
    """外部全局 reset 期间，表单的每宠保存触发的 absorb 不许洗掉运行期那份的旧代次。

    运行期脏时 absorb 什么都不做：旧 token 与旧基线原封不动，它自己的保存仍被既有 reset 门
    拒绝（``配置快照早于 reset，需 reload 后重新编辑``），而不是被静默放行去覆盖刚重置的值。
    （这一条是**合同护栏**：修复前后都该绿——上一版的 reload 也有同一个内部守卫。）
    """
    from PySide6.QtWidgets import QDialog

    shared, main = _shared_route(tmp_path)
    _profile_exists(tmp_path, {"scale": 0.8})
    main_path = Path(main.path)
    shared.set("pet_opacity", 88)                      # 运行期脏
    old_token = main._reset_generation

    outsider = Config(base=tmp_path, instance_id="")   # 外部全局 reset（既有实现：整表 + 新代次）
    assert outsider.reset_to_defaults() is True
    assert outsider._reset_generation != old_token

    dialog = _shared_dialog(shared)
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()
    try:
        dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(1.0))
        dialog.close()                                  # 每宠保存成功 → 自动触 absorb
        app.processEvents()
        assert finished == [int(QDialog.DialogCode.Rejected)], "关窗照常走完（保存成功）"

        assert main._reset_generation == old_token, "运行期那份的旧 token 不许被洗新"
        assert shared.get("pet_opacity") == 88, "运行期的未写差异还在"
        assert shared.save() is False, "脏 + 代次变了 → 既有 reset 门必须拒绝写"
        assert json.loads(main_path.read_text(encoding="utf-8"))["pet_opacity"] != 88, (
            "被拒的保存不许把旧草稿写进重置后的配置")
    finally:
        _drop(dialog)
