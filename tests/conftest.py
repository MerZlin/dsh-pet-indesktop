# -*- coding: utf-8 -*-
"""pytest 全局夹具。

1) 全局测试静音：跑测试时不许真实发声。
   play_sound 的取证日志照常记录（测试仍可断言"调用了播放"），但
   QSoundEffect/QMediaPlayer 的 play 被替换为空操作——测试套件在任何
   机器上跑都不应该让喇叭出声。

2) 无人值守环境（CI/自动化）下，模态 QMessageBox 弹窗会永久阻塞或直接崩溃
   （Fatal: Aborted）。设置对话框（如 modern_settings_dialog）保存开机自启失败时会弹模态
   QMessageBox.warning，所有调用 _save() 的测试在 CI 上都会因此卡死
   （定位手段：pytest -o timeout_method=thread --timeout=90 可 dump 出
   卡住的线程堆栈）。这里用 autouse fixture 全局把 QMessageBox 的静态弹窗
   方法替换为 no-op——任何测试都不会因模态弹窗卡死。需要断言弹窗行为的
   测试可自行 monkeypatch.setattr 覆盖。

   收窄（批 6-8a）：warning/information/critical/about 的返回值在产品代码中
   无分支用途，保持 no-op 即可；question() 是分支型 API，返回 None 不是合法
   StandardButton、会改变调用方分支语义，因此默认返回 StandardButton.No
   （安全拒绝，等同用户点“否”）。需要 Yes/No 特定答案的测试必须局部
   monkeypatch（test_agent_link / test_proactive 已如此）。
"""

import sys

import pytest


# 开发机 Qt 环境污染消毒（issue #323）：QT_PLUGIN_PATH 指向的旧版 Qt 插件会让
# pytest 每个进程首扫插件打一条"Windows fatal exception"噪声——在 import 任何
# 被测代码之前清掉（conftest 的 pytest 加载时机早于测试模块）。
try:
    from pet.qt_plugin_sanitizer import sanitize_plugin_path as _spp

    _spp()
except Exception:  # 消毒失败不挡测试
    pass


@pytest.fixture
def non_macos_overlay_topology(monkeypatch):
    """同 DPR 单窗算法族显式选择非 macOS，平台分支由 macOS 专项覆盖。"""
    import pet.overlay_shell as overlay_mod

    monkeypatch.setattr(overlay_mod, "_IS_MACOS", False, raising=False)


@pytest.fixture(autouse=True)
def _mute_qt_audio(monkeypatch):
    try:
        from PySide6.QtMultimedia import QMediaPlayer, QSoundEffect
    except Exception:
        return
    monkeypatch.setattr(QSoundEffect, "play", lambda self: None)
    monkeypatch.setattr(QMediaPlayer, "play", lambda self: None)
    # 阻止 QSoundEffect 真正异步加载音频源：headless CI 上即使不 play，
    # setSource 后的异步加载与 processEvents 也可能在 QtMultimedia 后端触发
    # 原生 access violation。把 status 直接置 Ready 也让预热等待循环零泵事件。
    monkeypatch.setattr(QSoundEffect, "setSource", lambda self, source: None)
    monkeypatch.setattr(QSoundEffect, "status", lambda self: QSoundEffect.Status.Ready)
    # Windows headless 上 WAV 之外的音效预热会启动真实 QAudioDecoder 异步解码，
    # 其 processEvents 等待循环可能原生崩溃（macOS/Linux 无此现象）。此全局
    # 打桩只作用于 Windows，避免影响其它平台 QtMultimedia 退出时的析构顺序。
    if sys.platform == "win32":
        # 测试如需验证音效逻辑，应像 test_click_sound 一样局部替换
        # click_sound._pool.qt_multimedia_classes 为假类。
        try:
            from pet import click_sound as _click_sound_mod
        except Exception:
            return
        monkeypatch.setattr(_click_sound_mod._pool, "qt_multimedia_classes", lambda: None)


@pytest.fixture(autouse=True)
def _mute_winmm_audio(monkeypatch):
    """winmm 直放后端在测试里必须"不可用"（对齐 _mute_qt_audio 的口径）。

    pet/sound_winmm.py 是真实触碰声卡的路径：不拦的话，"设置里点试听"这类
    用例会在跑测试时真的把 wav 交给 waveOut 发声，还会在无人值守 runner 上
    打开真实音频设备。默认把默认后端置为不可用，产品代码因此走与改动前
    逐位一致的 Qt 路径；要验证 winmm 语义的用例自行注入替身后端
    （tests/test_winmm_sound.py 用 _new_winmm_pool / WinmmSoundPool(api=...)）。
    """
    try:
        from pet import sound_winmm
    except Exception:
        return
    monkeypatch.setattr(sound_winmm, "default_api", lambda: None)


@pytest.fixture(autouse=True)
def _reset_feature_activation_snapshot():
    """功能模块激活快照是进程级全局：走 main() 的用例会真冻结（全新目录 =
    新装纯核心 []），不回收会污染同进程后续全部用例（设置页模块行整批缺席，
    期 5 全量曾因此 88 红）。每个用例前后都恢复到「未冻结 = 全激活」默认态；
    门控用例在测试体内自行 freeze_snapshot_for_tests。
    """
    from pet import feature_activation as fa

    fa.reset_snapshot_for_tests()
    yield
    fa.reset_snapshot_for_tests()


@pytest.fixture(autouse=True)
def _no_modal_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    # 纯防卡桩：warning/information/critical/about 的返回值无分支用途，统一 no-op。
    for method in ("warning", "information", "critical", "about"):
        monkeypatch.setattr(QMessageBox, method, staticmethod(lambda *a, **k: None))
    # question() 是分支型 API（调用方按返回值走 Yes/No 分支，如 pet/agent_link.py
    # set_enabled），返回 None 不是合法 StandardButton、会改变分支语义。这里默认
    # 返回 StandardButton.No（安全拒绝，等同用户点“否”）；需要 Yes/No 特定答案的
    # 测试必须局部 monkeypatch（test_agent_link / test_proactive 已如此）。
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )


@pytest.fixture(autouse=True)
def _no_modal_save_failure_choice(monkeypatch):
    """保存失败出口的模态框同样必须"不弹"（同 _no_modal_message_boxes 的理由）。

    ``pet/settings_close_flow`` 的失败框是**自建** QMessageBox + ``exec()``，不在上面
    那组静态方法桩的覆盖范围内：任何"关窗时保存失败"的用例都会卡在这个模态框上
    （与旧实现里设置对话框弹模态 warning 卡住 CI 是同一类事故）。

    默认答案 = 继续编辑（等同用户不放弃），对产品行为的影响与改动前逐位一致
    （改动前保存失败也不关窗）。要断言文案/按钮语义或验证"用户选择放弃"的用例
    自行 monkeypatch 这个 seam——``tests/test_settings_stale_form_exit.py`` 就是用它
    取产品真实构造的框。
    """
    try:
        from pet import settings_close_flow
    except Exception:
        return
    monkeypatch.setattr(settings_close_flow, "confirm_discard_unsaved",
                        lambda parent, failure: False)


@pytest.fixture(autouse=True)
def _no_real_dsh_profile_write(monkeypatch):
    """禁止测试触发对**真实** ~/.dsh/profiles 的桥接 link 自检。

    产品在启动路径会自检桥接 link（陈旧则跑 `pnpm add` 刷成当前构建，见
    DshMonitor.schedule_link_refresh_check）；测试若启用 dsh 联动又没打桩，
    那个后台线程会读到开发者/CI 机器上的真实 profile 并真的执行 pnpm，
    改写用户配置——测试绝对不许有这种副作用。这里只拦"起真线程"这一步，
    自检逻辑本身仍可测（用例自行注入 spawn，或直接调 refresh_stale_bridge_links）。
    """
    try:
        from pet.agent_link import DshMonitor
    except Exception:
        return
    monkeypatch.setattr(
        DshMonitor, "_spawn_link_check", staticmethod(lambda target: None),
    )


@pytest.fixture(autouse=True)
def _isolate_topology_config(tmp_path, monkeypatch):
    """拓扑判定的**配置输入**每测隔离到临时目录（T4 Phase A）。

    渲染拓扑自 T4 起会读主配置 ``stream_capture_mode``（``overlay_settings_command``
    的 ``_default_base()/APP_DIR_NAME/config.json``）。测试进程若真去读，结果就
    取决于跑测试这台机器上的用户配置：开发机勾过捕获模式 ⇒ 所有"默认 overlay"
    的断言在这种机器上变红，而 CI 上永远绿——测试失去意义。故把目录解析这一层
    钉到 ``tmp_path``（空目录 ⇒ 配置缺失 ⇒ 默认 overlay），并清掉快照缓存。

    钉的是 ``overlay_settings_command`` 复用的那个 seam（``pet.config._default_base``
    经本模块的模块级绑定），所以用例仍可自行覆盖：测试体内再
    ``monkeypatch.setattr(cmd, "_default_base", ...)`` 或直接替换
    ``cmd.topology_config_path``（后打的补丁生效）。
    """
    try:
        from pet import overlay_settings_command as cmd_mod
    except Exception:
        return
    monkeypatch.setattr(cmd_mod, "_default_base", lambda: tmp_path)
    cmd_mod.reset_topology_snapshot()
    yield
    cmd_mod.reset_topology_snapshot()


@pytest.fixture(autouse=True)
def _close_session_writers():
    """会话异步写盘（B8）：每个测试结束后关闭所有后台 writer，
    避免守护线程在 tmp_path 已清理后继续写盘（WinError 145 之类的 teardown 竞态）。"""
    yield
    try:
        from pet.chat import session_store
        session_store.reset_writers_for_tests()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clear_click_sound_pool():
    """每测后清空点击音效池（测试债防线）。

    conftest 只静音了 play()，但设置保存等路径的 warm_click_sound_effects
    会真实创建 QSoundEffect/QMediaPlayer/QAudioOutput。这些 QtMultimedia
    原生对象跨测试累积后，在共享 QApplication 下随机 access violation /
    Fatal abort（全量套件崩溃点会漂移：click_sound 预热循环、气泡图片
    processEvents 均观测到）。每测后 clear() 复位原生对象缓存。
    """
    yield
    try:
        from pet import click_sound
        click_sound._pool.clear()
        click_sound._reset_caches_for_tests()
    except Exception:
        pass
    # 用例可能自建 winmm 池（替身后端也持句柄/可选 timer）：一并收口。
    try:
        from pet import sound_winmm
        sound_winmm._clear_live_pools_for_tests()
    except Exception:
        pass


def drop_stale_chime_quiet_listeners() -> None:
    """摘掉共享免打扰表里残留的 ``VoiceChimeService`` 回调（用既有 ``remove_listener``）。

    唯一实现：全局 autouse fixture ``_no_stale_chime_quiet_listeners`` 用的就是它；
    要自证收口效果的用例直接调这一条（``test_voice_chime_service`` /
    ``test_overlay_app_hosts``），不在测试里各写一份。

    按身份摘（``__func__`` 命中本模块的 ``_on_quiet_state_changed``）：不碰别人的订阅，
    也不改 ``quiet_mode`` 状态机——``reset_shared_quiet_state()`` 只复位状态，监听表归
    订阅方管（产品侧 ``stop()`` 才摘），所以残留只能在测试边界上由收口手段清。
    """
    try:
        from pet import quiet_mode
        from pet.voice.chime_service import VoiceChimeService
    except Exception:
        return
    try:
        state = quiet_mode.shared_quiet_state()
        target = VoiceChimeService._on_quiet_state_changed
        for callback in list(state._listeners):
            if getattr(callback, "__func__", None) is target:
                state.remove_listener(callback)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_stale_chime_quiet_listeners():
    """用例边界上摘掉"构造即挂"留下的免打扰回调（共享表是**进程级**的）。

    ``VoiceChimeService`` 构造就注册 ``quiet_mode`` 监听（复审 #6：手动报时/试听通道
    从不 ``start()``，也必须在免打扰开启时被切），而大量用例构造后从不 ``stop()``
    （只测一拍内的行为）。回调对空闲服务是无害 no-op，但进程级共享订阅表会随用例数
    线性增长、并持有服务与它的播放器不放；``reset_shared_quiet_state()`` 只复位状态、
    不动监听表。这条防线因此必须全局面：它此前只长在 ``test_voice_chime_service.py``
    里，模块级 autouse 不跨文件，``test_overlay_app_hosts.py`` 那种"构造完就不管"的
    域照旧给共享表添残留。
    """
    drop_stale_chime_quiet_listeners()
    yield
    drop_stale_chime_quiet_listeners()


@pytest.fixture(autouse=True)
def _no_filesystem_player_scan(monkeypatch):
    """音乐播放器路径扫描（#140 的启动/菜单预热）绝不允许在测试里真实发生。

    ``_search`` 会对 C:/、D:/ 等盘根做三层浅扫（每 root 最多 200 目录）；
    测试进程里 netease/qqmusic 两条预热线程并发扫盘曾在 Windows CI 上触发
    C 级 access violation（GC 与 scandir 交叠，PR #147 取证 dump：一线程
    Garbage-collecting、一线程 _shallow_scan，exit -1073741819），且崩溃点
    随套件进度漂移。需要真实扫描语义的用例自行 monkeypatch
    （test_music_player_cache.py 全部用例自带打桩，不受影响）。
    """
    from pet.music import players as music_players

    monkeypatch.setattr(music_players, "_search", lambda key: None)
    yield


@pytest.fixture(autouse=True)
def _close_qt_top_level_widgets():
    """在测试后收口仍存活的应用级后台资源与 collision IPC 会话。"""
    yield
    # 内存取证（DSPET_MEM_DEBUG）是进程级开关 + 常驻 ticker 线程：用例打开后
    # 不复位会让后续用例凭空多一条 60s 日志线（且 reader 登记表跨用例残留）。
    try:
        from pet import mem_debug as _mem_debug_mod
        _mem_debug_mod._reset_for_tests()
    except Exception:
        pass
    # 免打扰是**进程级单例**（issue #208）：某个用例开启后不复位，后续用例会
    # 凭空进入"普通提醒被拦下"的状态，失败点离真因很远。
    try:
        from pet.quiet_mode import reset_shared_quiet_state
        reset_shared_quiet_state()
    except Exception:
        pass
    # webm_clip 的「会话结束」闸门是进程级 latch（issue #111）：测试里置位后
    # 不复位会让后续用例静默拒绝一切 reader 启动（报错点离真因很远）。
    try:
        from pet import webm_clip as _webm_clip_mod
        _webm_clip_mod._reset_session_ending_for_tests()
        # 首帧**跨库共享表**持有 QImage 强引用（同进程多库共用的首帧）：不逐用例
        # 清空会让上一个用例的首帧常驻到预算耗尽（内存噪声 + 命中计数被前序用例
        # 污染，用例断言随之不再自洽）。
        _webm_clip_mod.reset_first_frame_share()
    except Exception:
        pass
    # collision IPC：4.4b 随多进程多宠退役层删除——collision_ipc 会话不再存在，
    # 对应的 `_stop_live_sessions_for_tests` 收口一并移除。
    try:
        from pet.agent_link import AgentLinkManager, BaseAgentMonitor
        AgentLinkManager._shutdown_live_for_tests()
        BaseAgentMonitor._shutdown_live_for_tests()
    except Exception:
        pass
    # AppShell / 多窗共享子系统：待办服务的无主 QTimer 与共享 proactive 的
    # timer/bridge 从 Qt C++ 侧强引用住整个 shell 对象图（Python gc 回收不掉），
    # 解释器退出 GC 才最终化 → 原生访问违规（test_single_process_shared 的
    # flag_on 族逐用例单独跑亦复现，崩溃点 "Garbage-collecting / no Python
    # frame"）。按同族防线逐对象停表 + 过继 QApplication。
    try:
        from pet.multi_window_shared import SharedSubsystems
        SharedSubsystems._shutdown_live_for_tests()
    except Exception:
        pass
    try:
        from pet.app import AppShell
        AppShell._shutdown_live_for_tests()
    except Exception:
        pass
    try:
        from pet.library import MovieLibrary
        MovieLibrary._shutdown_live_for_tests()
    except Exception:
        pass
    # OverlayShell：壳的 tick 驱动器/自言自语计时/监视器/配图加载线程/素材库
    # 全部要逐测试收口——否则它们漂过整个套件活到进程退出，撞上 Qt 对象树
    # 拆除就是原生段错误（macOS CI 3/3 同点复现 + Windows 本地 2/12 退出崩；
    # 崩点随套件进度漂移，与本防线的既有记录同族）。
    try:
        from pet.overlay_shell import OverlayShell
        OverlayShell._shutdown_live_for_tests()
    except Exception:
        pass
    # 销毁残留顶层窗口（QDialog/QWidget）：只用 deleteLater，绝不用 close()。
    # close() 会触发 closeEvent → _write_config → warm_click_sound_effects 的
    # 副作用（t4 曾因此崩溃）；deleteLater 走 DeferredDelete，Qt 安全销毁且不
    # 触发 closeEvent。清理掉泄漏的 C++ 对话框，避免其悬空事件在后续测试的
    # processEvents 引爆（崩溃点漂移、access violation）。
    # 逐对象定向派发自己排的 DeferredDelete，绝不调用共享 QApplication 的全局
    # processEvents()。全局冲刷会把其他测试遗留的排队事件（跨线程 queued 调用、
    # 历史 timer、别处排的删除任务）一并派发到正在销毁/已销毁的原生对象上，
    # 把历史 QObject 生命周期集中引爆在当前测试 —— 这正是全量套件偶发
    # 0xC0000005 access violation、且崩溃点随当前测试漂移的机制
    # （docs/QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md §2、§4：全局冲刷与
    # 进程级 sendPostedEvents 都已被否决；本轮崩溃点就是这里的 app.processEvents()）。
    try:
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            _diag_sweep_reset()
            for widget in list(app.topLevelWidgets()):
                try:
                    if not shiboken6.isValid(widget):
                        continue  # C++ 侧已销毁的半死窗口：跳过
                    _diag_sweep(widget, 'pre')  # 只读取证：见 _diag_sweep（默认不写）
                    widget.deleteLater()
                    # 接收者定向：只处理这一个对象的 DeferredDelete，不触碰共享队列里
                    # 其他对象的删除任务与排队事件。
                    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
                    _diag_sweep(widget, 'post')
                except RuntimeError:
                    pass
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _close_webm_readers_at_session_end():
    """session 结束强收口所有 webm reader（测试债 #2 防线）。

    全量套件偶发 Windows access violation 的根因是 webm_clip `_reader`
    线程/ffmpeg 进程在测试收尾时未死干净、与解释器进程退出竞态。本 fixture
    在所有测试 teardown 之后做有界收口，目标：套件结束时无 webm-reader-*
    线程存活。只做收口，不改产品代码行为。

    收口对象（两类都覆盖）：
    1. 孤儿注册表（_ORPHAN_REGISTRY.holders()）：模块级强引用持有所有
       「退役池非空」的 clip —— 对每个 clip 调公开 cleanup()；
    2. 仍存活的 webm-reader-* 线程（clip 从未 stop、不在注册表中）：从
       threading 枚举反向定位其 owner clip（reader 线程的 _target 是
       clip._reader 绑定方法，__self__ 即 clip），再调公开 cleanup()。

    每轮 cleanup 后 reap 注册表（有界 join，正常 terminate 后毫秒级退出），
    轮数只作病态场景兜底；仍有存活线程则告警（防线不因自身变红）。
    """
    yield
    import logging
    import threading

    logger = logging.getLogger("pytest.conftest.webm")
    try:
        from pet import webm_clip as webm_clip_mod

        registry = webm_clip_mod._ORPHAN_REGISTRY

        def _clips_with_live_readers():
            clips = set(registry.holders())
            for t in threading.enumerate():
                if t.is_alive() and t.name.startswith("webm-reader-"):
                    owner = getattr(getattr(t, "_target", None), "__self__", None)
                    if owner is not None:
                        clips.add(owner)
            return clips

        for _ in range(5):
            clips = _clips_with_live_readers()
            if not clips:
                break
            for clip in clips:
                try:
                    clip.cleanup()
                except Exception:
                    pass  # clip 已随 Qt C++ 侧销毁等：交由 GC/产品自身兜底
            registry.reap()
        survivors = [
            t.name for t in threading.enumerate()
            if t.is_alive() and t.name.startswith("webm-reader-")
        ]
        if survivors:
            logger.warning(
                "session 结束仍有 %d 个 webm reader 线程存活: %s",
                len(survivors), survivors,
            )
    except Exception:
        pass  # 防线 fixture：任何异常都不应让套件本身变红
    # 会话结束闸门是进程级 latch（issue #111）：在最靠后的收口点再复位一次，
    # 保证无论哪个用例置位过都不会串到后续用例（那会让 reader 静默拒绝启动，
    # 报错点离真因很远）。
    try:
        from pet import webm_clip as _webm_clip_mod
        _webm_clip_mod._reset_session_ending_for_tests()
    except Exception:
        pass


# ------------------------------------------- 原生崩溃取证位（CI 诊断，非门禁）
#
# 原生崩溃杀掉整个 pytest 进程后，`-q` 的失败详情与进度点都还在缓冲里：既不知道
# "死在哪一例哪一阶段"，也看不到"崩之前已经红了哪些用例"。不碰 pytest 自己的
# faulthandler、不装信号处理器（faulthandler.register 对 SIGSEGV/SIGABRT 这类致命
# 信号直接 RuntimeError 拒绝，见 CPython Modules/faulthandler.c 的 check_signum）。
# 开关：DSPET_TEST_DIAG_DIR=<目录>；GH Actions 自动落 $RUNNER_TEMP/dspet-pytest-diag/；
# 未开启时两个钩子第一行返回、清扫 fixture 里的取证行也不写：不建文件、不写一行。
_diag_crumb = None
# 清扫 fixture 的逐对象取证（见 _diag_sweep）：本轮已写行数 + 每轮上限。
_diag_sweep_n = 0
_DIAG_SWEEP_LINES = 12
# Py_None 引用计数采样（见 _diag_none_sample）：只落行计数 + 上一例取值，无线程。
_DIAG_NONEREF_LINES = 10
_DIAG_NONEREF_DROP = 5000
_diag_none_seen = 0
_diag_none_last = None
_diag_none_lines = 0


def _diag_open():
    import os
    import tempfile
    base = os.environ.get('DSPET_TEST_DIAG_DIR', '').strip()
    if not base and os.environ.get('GITHUB_ACTIONS') == 'true':
        base = os.path.join(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir(),
                            'dspet-pytest-diag')
    if not base:
        return None
    try:
        os.makedirs(base, exist_ok=True)
        return open(os.path.join(base, 'pytest-breadcrumb.txt'), 'w',
                    encoding='utf-8', buffering=1)
    except OSError:
        return None


def _diag_note(line):
    """落一行取证。诊断写入失败不得改变测试结果。"""
    if _diag_crumb is None:
        return
    try:
        _diag_crumb.write(line + '\n')
    except (OSError, ValueError):
        pass


def _diag_none_sample(nodeid):
    """逐测试采样一次 Py_None 引用计数，只在诊断开关打开时被调用（**无线程**）。

    ``none_dealloc`` 只在 None 的 ob_refcnt 掉到 0 时触发，且只可能出现在 CPython
    ≤3.11（3.12 起 None 是 immortal，同类引用计数损坏被静默——见报告 §5：那是掩盖，
    不是修复）。落行规则：

    * 第一条（基线）必落；
    * 之后每 100 例落一条（看趋势）；
    * 或相对**上一例**下降 ≥ ``_DIAG_NONEREF_DROP`` 立即落一条（逐测试下降，直接
      指名是哪个测试干的）；
    * 总行数只对基线与每 100 例的周期行封顶 ``_DIAG_NONEREF_LINES``；下降行**不受**
      封顶——它是唯一能指名肇事测试的行，被封顶挡掉 = 把诊断本来要的信号自己扔掉。
    * 落下的每一行都走**双通道**：crumb 照旧，另写一份到 ``sys.__stderr__``（workflow
      只 ``tail -n 30`` 面包屑，几行历史样本必被尾窗丢掉；stderr 不受该窗口限制）。
    """
    global _diag_none_seen, _diag_none_last, _diag_none_lines
    value = sys.getrefcount(None)
    _diag_none_seen += 1
    drop = None if _diag_none_last is None else _diag_none_last - value
    _diag_none_last = value
    spike = drop is not None and drop >= _DIAG_NONEREF_DROP
    if not (spike or drop is None or _diag_none_seen % 100 == 0):
        return
    if not spike and _diag_none_lines >= _DIAG_NONEREF_LINES:
        return
    _diag_none_lines += 1
    delta = '' if drop is None else f' d={-drop:+d}'
    line = f'[none-refcount] {value}{delta} #{_diag_none_seen} {nodeid}'
    _diag_note(line)
    if _diag_crumb is not None and sys.__stderr__ is not None:
        try:
            print(line, file=sys.__stderr__, flush=True)
        except (OSError, ValueError):
            pass


def _diag_sweep_reset():
    global _diag_sweep_n
    _diag_sweep_n = 0


def _diag_sweep(widget, phase):
    """清扫每个顶层窗口的**前/后**各写一行 ``[qt-sweep/{phase}] 类名(objName)``。

    只在取证开关打开时写，且每轮最多 ``_DIAG_SWEEP_LINES`` 行（workflow 只
    ``tail -n 30`` 面包屑，不封顶会被大户用例挤掉真崩点）。收口只在"包装还活着"时
    生效：包装若被别处强引用（典型：``monkeypatch.setattr`` 打在 QWidget *实例*上，
    undo 把旧值 bound method 写回实例字典形成自环），C++ 会先在这里被删掉而包装继续
    活着——这两行就是用来区分"清扫删掉了什么"和"删完还剩什么"的。
    """
    global _diag_sweep_n
    if _diag_crumb is None or _diag_sweep_n >= _DIAG_SWEEP_LINES:
        return
    _diag_sweep_n += 1
    try:
        try:
            name = repr(widget.objectName())
        except RuntimeError:
            name = '<C++ 已删>'
        _diag_note(f'[qt-sweep/{phase}] {type(widget).__name__}({name})')
    except Exception:
        pass


def pytest_configure(config):
    global _diag_crumb
    _diag_crumb = _diag_open()


def pytest_runtest_logstart(nodeid, location):
    if _diag_crumb is not None:
        try:
            _diag_crumb.write(f'[start] {nodeid}\n')
        except (OSError, ValueError):
            pass  # 诊断写入失败不得改变测试结果。


def pytest_runtest_logreport(report):
    if _diag_crumb is None:
        return
    # 阶段结束即落盘（buffering=1 逐行 flush）：最后一行记录最后完成的阶段。
    try:
        _diag_crumb.write(f'[{report.when}] {report.nodeid}\n')
        if report.failed and report.longrepr is not None and sys.__stderr__ is not None:
            # 失败详情默认攒到会话末尾才打（进程被原生杀死就全丢）：改成立刻回显。
            print(f'[diag-fail/{report.when}] {report.nodeid}\n{report.longrepr}',
                  file=sys.__stderr__, flush=True)
    except (OSError, ValueError):
        pass  # 磁盘满或输出关闭时，保留 pytest 本身的判定和报告。
    if report.when == 'teardown':
        # 一例结束采一次 Py_None 计数（threading 0，落行规则见 _diag_none_sample）。
        try:
            _diag_none_sample(report.nodeid)
        except Exception:
            pass  # 取证绝不能让用例变红
