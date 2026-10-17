# -*- coding: utf-8 -*-
"""子宠设置页的配置读写路由：per-slot 键走 slot 文件，其余键走主配置。

问题（死开关）：overlay 拓扑下右键子宠 → 桌宠设置拉起的是**独立设置进程**
（``--instance slot-N`` → ``Config(instance_id="slot-N")`` → 读写
``config-slot-N.json``），而运行期只有 ``PER_SLOT_SETTING_KEYS`` 里的键逐宠取源
（``overlay_shell._SpriteConfig.get``），**其余键一律读主配置**。于是子宠页上
「语音报时 / 节日 / 待办 / 灵动岛 / 菜单外观 / 窗口级 7 键 …」约百行设置：保存
成功、回显正确、运行期零效果。窗口级 7 行还写着「（对所有桌宠生效）」——实际对
所有桌宠都不生效。

修法：给设置页的配置面套上读写路由（:func:`route_settings_config`），按 key 分流：

- 分流键（:data:`SLOT_ROUTED_KEYS`）→ slot 文件，交给
  ``pet_profiles.write_profile_settings`` 批量锁内合并；
- 其余键 → 主 ``config.json``，保留 Config 的编辑基线，交给
  ``Config.save`` 锁内重读并递归合并真实变更。

所有拓扑的slot身份（含slot0）同样分流；空身份（主宠的设置会话）也按 slot0
分流（#271：不分流时整表保存把每宠键当退役键清掉，写入静默蒸发）。
运行legacy窗口复用本视图，不维护第二份每宠真值。

全局身份恒显式传空字符串，不受DSH_PET_INSTANCE或--slot选择影响。
父进程拓扑只描述捕获运行形态，不决定设置所有权。

``PER_SLOT_SETTING_KEYS`` 的单一来源在 ``overlay_spawn_state``（零 Qt 轻模块）：
独立设置进程禁止导入 ``overlay_shell``（会连带素材库/ffmpeg/托盘），而壳侧只有
取源这一处读点，故键集本体放两侧都能 import 的轻模块。

**G1b：分流键的读写真正接到新档案存储**（``pet/pet_profiles``）——这里是本次接线的
四条口径（旧实现的分歧逐条列在下面）：

1. **写 = 一批 + 锁内合并**：pending 的每宠键**一次**
   ``pet_profiles.write_profile_settings``（目录锁内重读合并 + 一次原子替换）落进
   ``config-slot-N.json``。旧路径逐键 ``overlay_spawn_state.write_slot_setting``：
   每个键各自"读整份 → 改一个键 → 原子替换"，无锁、N 个键 N 次 replace，并发写者
   （另一个设置进程 / 壳的几何自存）会被整表覆盖吞掉。**写不成功不清 pending**。
2. **坏档案不覆盖**：档案损坏/非对象/非法 UTF-8 时写入被拒（``refused-corrupt``）、
   一个字节都不写、``save()`` 如实返回 False。旧路径把坏文件当空 dict 读再整表重写，
   等于静默销毁用户手改坏的文件。
3. **读 = 同源默认**：档案里缺键（或档案不可信）→ ``pet_profiles.same_source_default``
   （``Config`` 出厂默认），**不再回落主宠当前值**——否则"把这宠的偏好重置掉"之后页面
   立刻又显示主宠的值，重置看起来没生效。快照经 ``normalize_pet_profile_snapshot``
   纯迁移清洗，不构造 Config、不重读文件，不在这里另建默认值/范围表。
   档案数据与代次 token **同一把锁内读出**（``pet_profiles.read_slot_snapshot``）。
4. **reset = ``pet_profiles.reset_slot_profile``**：保身份、**保位置**
   （``rx/ry/screen_name``）、偏好（含 ``facing``）回同源默认、别宠与全局一个字节不动，
   并推进该槽代次（作废迟到的旧几何写）。``partial``（代次已推进、档案没写成）**返回
   False**——不谎称成功，也不清 pending；下一次 reset 重新取快照，正常收敛。

**失败描述（2026-10-10 实机 §4 之后）**：``save()`` 返回的 bool 只说"成没成"，说不清
"为什么"。两条失败处置差得很远——代次落后是**保护生效**（旧表单不许覆盖新档案），
要做的是重开设置页；写盘失败才要查磁盘/锁。所以 ``save()`` 现在同时把这次失败记进
:attr:`SlotRoutedConfig.last_save_failure`（:class:`SaveFailure`），界面据此挑文案与
出口（见 ``pet/settings_close_flow``）。类别词表在本模块，界面侧的文案不在。

**放弃（discard，2026-10-11 CF01）**：失败后用户明确选「放弃未保存的改动并关闭」时，只关窗
**不够**——``Config.set()`` 改的是配置面那份内存，草稿留在 ``data`` 与本视图的两块待写区
里；进程内 fallback 下这份视图在设置窗关掉之后仍被运行期使用（``PetInstance.config``），
下一次**无关**保存会把它一起 flush 落盘。所以
:meth:`SlotRoutedConfig.discard_unsaved_drafts` 作废两路待写区、作废档案快照、把主配置内存
退回成功基线（不写盘、不洗编辑代次、不回滚已经写成功的那一半）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from . import overlay_settings_command, pet_profiles
from .config import Config, chat_secret_carryover, normalize_pet_profile_snapshot

logger = logging.getLogger(__name__)

__all__ = [
    "SAVE_FAILURE_AUTOSTART",
    "SAVE_FAILURE_DISK",
    "SAVE_FAILURE_LAYOUT",
    "SAVE_FAILURE_PROFILE",
    "SAVE_FAILURE_STALE",
    "SAVE_FAILURE_UNKNOWN",
    "SLOT_LOCAL_KEYS",
    "SLOT_ROUTED_KEYS",
    "SaveFailure",
    "SlotRoutedConfig",
    "classify_slot_status",
    "global_config_of",
    "is_main_identity",
    "route_settings_config",
    "save_failure_of",
]

#: 「这次编辑还没有基线代次」的哨兵：与 ``None``（**查过了，但没有可信代次**）区分开。
#: 用 ``object()`` 而不是 ``None``——`_pinned_edit_generation` 为 `None` 是"档案不可信，
#: 这次保存必须失败"的确定结论，不能跟"还没查"混成一个值。
_UNPINNED = object()

#: 该写进 slot 文件的键 = per-slot 取源集（``overlay_spawn_state`` 的单一来源）
#: **加上** ``collision_enabled``。
#:
#: 为什么多这一键：它的逐宠读点不在 ``_SpriteConfig.get`` 的第一条分支，而在紧随
#: 其后的专属分支（``_sprite_collision_enabled`` → ``read_slot_collision_enabled``：
#: 读这只宠的 slot 文件、缺文件继承主配置）。也就是说运行期对它的读法**就是**
#: per-slot 的——写进主配置既改不动这只宠（落种时 slot 文件里已有一份旧值压着），
#: 又会顺手改掉其它宠的继承值。
SLOT_ROUTED_KEYS = pet_profiles.PER_PET_KEYS

#: slot 文件的**本地**键：只有落种/身份逻辑读它，运行期一个都不看。
#: 写进主配置会凭空多出第三个真值，故与 per-slot 键同走 slot 文件。
SLOT_LOCAL_KEYS = frozenset({"user_customized"})

#: 保存失败的**类别**：界面按它挑文案与出口。``pet_profiles`` 的 ``status`` 原文只进
#: 日志与正文诊断，**不**当界面分支判据——"stale-generation" 这种词给用户看等于没说话，
#: 而把它当写盘失败更是把排查方向指错（实机 §4 的就是这一条）。
#:
#: ``SAVE_FAILURE_STALE``：代次落后（旧表单想覆盖已经被合法写者推进过的档案），保护生效，
#: 不是故障；``SAVE_FAILURE_PROFILE``：档案/代次本身不可信，为避免覆盖它**不写这只桌宠的
#: 档案**（全局那一路写没写是另一回事，由 ``SaveFailure.slot_written`` / ``global_written``
#: 如实回答）；``SAVE_FAILURE_LAYOUT``：写盘之前的表单校验没过（菜单布局无效）；
#: ``SAVE_FAILURE_AUTOSTART``：设置已经落盘、是落盘之后的开机自启收尾出了错；
#: ``SAVE_FAILURE_DISK``：锁/写盘一类 IO 侧失败；``SAVE_FAILURE_UNKNOWN``：界面层拿到
#: 未预期异常（是否已部分写入无法确认，文案必须比"一项都没写"更弱）。
SAVE_FAILURE_STALE = "stale-generation"
SAVE_FAILURE_PROFILE = "profile-untrusted"
SAVE_FAILURE_LAYOUT = "invalid-layout"
SAVE_FAILURE_AUTOSTART = "autostart-failed"
SAVE_FAILURE_DISK = "disk"
SAVE_FAILURE_UNKNOWN = "unknown"

#: 归档「哪些 ``pet_profiles`` 状态属于哪一类」。词表本体在 ``pet_profiles``（公共失败
#: 词表），这里只做归类；不认识的一律按写盘失败如实上报，不猜成别的处境。
_STALE_STATUSES = frozenset({"stale-generation"})
_PROFILE_STATUSES = frozenset({
    "refused-corrupt", "refused-unsupported-version", "refused-legacy-v1",
    "backup-invalid", "corrupt",
})


def classify_slot_status(status: str) -> str:
    """把档案写入的失败 ``status`` 归成 :data:`SAVE_FAILURE_STALE` 等类别。"""
    if status in _STALE_STATUSES:
        return SAVE_FAILURE_STALE
    if status in _PROFILE_STATUSES:
        return SAVE_FAILURE_PROFILE
    return SAVE_FAILURE_DISK


@dataclass(frozen=True)
class SaveFailure:
    """一次保存未成功的**如实**描述：界面文案与出口的唯一数据源。

    ``kind`` 是文案分支判据；``status`` / ``reason`` 是 ``pet_profiles`` 的原文（进日志
    与正文诊断）；``slot_written`` / ``global_written`` 回答"本次已经落盘的是哪一半"
    ——**没有待写内容的那一路不算写入**。放弃草稿时这两项必须如实告诉用户，否则界面
    等于在许诺一个做不到的"整次取消"（失败的 ``save()`` 会先写档案再写主配置，档案那
    一路被代次门挡住时，全局那一半可能已经落盘了）。
    """

    kind: str
    status: str = ""
    reason: str = ""
    path: str = ""
    slot_written: bool = False
    global_written: bool = False


class SlotRoutedConfig:
    """子宠设置页的配置视图：按 key 把读写分到这只宠的档案或主配置。

    与 ``_SpriteConfig`` 共用每宠键集与 ``normalize_pet_profile_snapshot``：
    历史迁移、脏值清洗、缺键默认都来自同一规则；清洗不会重读档案或改变代次。

    非路由属性（``chat_settings`` / 角色别名 / 点击台词 / 密钥）一律转发**主配置**：
    它们本就是进程级数据，子宠页以前读的是 slot 文件里的陈旧副本（AI 服务商列表
    显示成落种那一刻的样子），本身就是同一个 bug 的另一种表现。``instance_id`` /
    ``dir`` / ``path`` 仍取 slot 身份那份——"我是哪只桌宠"以及报错文案里的路径都
    以它为准（两份文件同目录，路径同源）。
    """

    def __init__(self, instance_config, main_config, slot_id: int) -> None:
        self._instance = instance_config
        self._main = main_config
        self._slot_id = int(slot_id)
        # 档案快照（数据 + 代次，同一把锁内读出）按需读一次；pending 是"用户这次改了什么"，
        # 与磁盘快照是两件事——``reload()`` 只作废快照，不丢 pending。
        self._slot_snapshot: pet_profiles.SlotSnapshot | None = None
        self._slot_reader: dict | None = None
        self._pending_slot: dict = {}
        self._pending_main: dict = {}
        # 这次编辑的代次基线（表单打开时首读的那一代；见 :meth:`_flush_slot`）：
        # 磁盘上有人推进过该槽代次（外部 reset / 主进程复活重建，都是合法写者）时，
        # 本次表单里的**旧控件值**不许再以新代次落盘——否则重置/重建被静默抹掉。
        self._pinned_edit_generation: object = _UNPINNED
        # 这份视图是**为哪份视图开的表单**（``None`` = 它自己就是被创建的那一份）：
        # 表单成功落盘后要作废运行期那份的只读快照，否则"即时生效"的每宠键（点击音效
        # 这类改一下立刻写盘的开关）在运行期要等设置窗关闭才看见新值。
        self._runtime_view: "SlotRoutedConfig | None" = None
        # 最近一次 ``save()`` 的失败描述（成功为 ``None``）；界面据此选文案与出口。
        # ``_last_slot_failure`` 是档案那一路的中间态，由 ``save()`` 组装进前者。
        self.last_save_failure: SaveFailure | None = None
        self._last_slot_failure: SaveFailure | None = None

    # ------------------------------------------------------------ 身份 / 路径面
    @property
    def instance_id(self) -> str:
        return str(getattr(self._instance, "instance_id", "") or "")

    @property
    def dir(self):
        return self._instance.dir

    @property
    def path(self):
        return self._instance.path

    @property
    def data(self) -> dict:
        """有效配置数据 = 主配置（``get`` 的读源，非 per-slot 键都从这里来）。"""
        return {**self._main.data, **self._cleaned_slot_data(), **self._pending_slot}

    # ------------------------------------------------------------ 读写路由
    def get(self, key, default=None):
        """每宠键与运行期共用纯快照迁移清洗；缺键不继承主宠当前值。

        pending 是本次编辑，优先于捕获的档案快照；全局键仍从主配置读取。
        """
        if key in SLOT_ROUTED_KEYS:
            if key in self._pending_slot:
                return self._pending_slot[key]
            return self._cleaned_slot_data().get(key, default)
        if key in SLOT_LOCAL_KEYS:
            # 档案本地键（``user_customized``）不是每宠偏好：同源默认表里没有它，缺省按
            # 主配置那份读（与改动前逐位一致，不引入第三个真值）。
            if key in self._pending_slot:
                return self._pending_slot[key]
            value = self._slot_values().get(key)
            return value if value is not None else default
        return self._main.get(key, default)

    def set(self, key, value):
        """写一个设置键：分流键 → slot 文件（延后到 :meth:`save` 原子写），其余 → 主配置。

        主配置先写内存并记 pending；落盘由 Config.save 按编辑基线递归合并。
        """
        if key in SLOT_ROUTED_KEYS or key in SLOT_LOCAL_KEYS:
            self._slot_values()
            self._pending_slot[key] = value
            return
        self._pending_main[key] = value
        self._main.set(key, value)

    def set_chat_settings(self, settings) -> None:
        """chat 直写主配置并记 pending，确保路由 save 不跳过这份草稿。"""
        self._main.set_chat_settings(settings)
        self._pending_main["chat"] = self._main.data.get("chat")

    def set_click_talk_bindings(self, character_id: str, bindings: dict) -> None:
        """按 Config 同源清洗写入台词绑定，立即提交并维护路由 pending 状态。"""
        profiles = self._main.data.get("character_profiles")
        profiles = dict(profiles) if isinstance(profiles, dict) else {}
        profile = profiles.get(str(character_id))
        profile = dict(profile) if isinstance(profile, dict) else {}
        profile["click_talk_bindings"] = bindings
        profiles[str(character_id)] = profile
        self._main.set("character_profiles", profiles)
        self._pending_main["character_profiles"] = self._main.data.get("character_profiles")
        self._flush_main()

    def reload(self):
        """从磁盘重读两份配置（主配置 + 这只宠的档案快照）。

        主配置有草稿时保留编辑基线；Config.save 自行吸收最新磁盘。
        """
        if not self._pending_main:
            self._main.reload()
        self._invalidate_slot_snapshot()

    def save(self) -> bool:
        """落盘：每宠键一批写档案，主配置按原编辑基线递归合并。

        两路各自如实返回；任一路失败就返回 False（**草稿都留着**，见两个 ``_flush_*``），
        并把这次失败记进 :attr:`last_save_failure`。

        档案那一路先写、主配置那一路后写，所以失败也可能**一半已经落盘**（档案被代次门
        挡住时全局那半通常已经写进去了）——:class:`SaveFailure` 的 ``slot_written`` /
        ``global_written`` 如实记下是哪一半，界面才有资格谈"放弃"的边界。
        """
        wrote_slot = bool(self._pending_slot)
        wrote_main = bool(self._pending_main)
        ok_slot = self._flush_slot()
        ok_main = self._flush_main()
        self.last_save_failure = None
        if not (ok_main and ok_slot):
            failure = self._last_slot_failure or SaveFailure(
                kind=SAVE_FAILURE_DISK, status="main-config",
                reason="全局配置未能写入磁盘", path=str(self._main.path))
            self.last_save_failure = replace(
                failure, slot_written=wrote_slot and ok_slot,
                global_written=wrote_main and ok_main)
        return bool(ok_main and ok_slot)

    def discard_unsaved_drafts(self) -> tuple[bool, str]:
        """作废本视图两路待写草稿，并把只读视图与共享内存退回**可信落盘状态**。

        与 ``Config`` 上的同名方法分工：那里处置主配置那份内存草稿，这里额外处置
        本视图自己两块待写区（``_pending_slot`` / ``_pending_main``）。四件不做的事：
        不写盘、不推进/洗掉代次、不回滚已经写成功的那一半、不碰另一个写者。

        - **两边待写区清空**：这次窗口里还没写成功的东西全部作废。已经写成功的那一半
          在磁盘上、也在 ``_instance.data`` 内存镜像里（``_flush_slot`` 只在成功时同步），
          不动它——"放弃"的边界是"还没写成功"，不是"整次取消"。
        - **档案快照作废**：下一次读重新在同一把目录锁内取盘上那一份，共享视图因此回到
          磁盘现状（而不是停在表单首读的那一份）。
        - **主配置那份内存草稿**交给 ``Config.discard_unsaved_drafts``（退回成功基线 +
          重读核对）。**只有真的有全局待写草稿时才碰它**：没有草稿时连重读都不做，
          不给外部写者制造无谓的重读/迁移副作用。
        - **``_pinned_edit_generation`` 不动**：它是这张表单的编辑代次基线。洗掉它等于
          让旧控件值获准盖到新代次上（保护失效，实机 §4 那类"重置被静默抹掉"就会回来）。

        返回 ``(是否与磁盘核对过, 没能核对时的配置文件路径)``（主配置那一份的路径），
        见 ``Config.discard_unsaved_drafts``；调用方**必须**把未核对如实告诉用户。
        """
        had_main = bool(self._pending_main)
        self._pending_slot.clear()
        self._pending_main.clear()
        self.last_save_failure = None
        self._last_slot_failure = None
        self._invalidate_slot_snapshot()
        if not had_main:
            return True, ""
        return self._main.discard_unsaved_drafts()

    def reset_to_defaults(self) -> bool:
        """子宠页的「恢复默认设置」：只重置这只宠的档案，**主配置一个键都不碰**。

        闸门所在：``Config.reset_to_defaults`` 把整份内存换成出厂默认再**整表**落盘。
        在子宠页上（本视图已把非路由键接到主配置）那份"整表"就是所有桌宠共用的主
        配置——点一次会把主宠与其它子宠的设置一起抹掉。子宠页能重置的只有它自己那份
        档案（``pet_profiles.reset_slot_profile``）。

        作用域 = 本方法这条闸，确认框文案必须与它一致（R08）：设置页侧按
        ``settings_reset_defaults.reset_scope_is_slot``（判据就是**本视图这个类型**）
        选子宠页专属文案。**G1b 起范围变了**（规范 §五）：保身份、**保位置**、偏好
        （含朝向）回同源默认——所以文案也跟着改成"位置保留"，两处必须同时改。

        代次 token 的取法（G1a 契约）：每次 reset 都**新取一份快照**
        （``read_slot_snapshot``，档案 + 代次同一把锁内读出），拿到就直接交给
        ``reset_slot_profile``；**不复用**启动时缓存的那份——上一次 reset 若只完成了
        "推进代次"（``partial``），复用旧代次会被自己的门挡住，用户永远重置不了。

        返回值如实反映是否真的重置：``partial``（代次已推进、档案没写成）与坏档案
        都是 **False**（调用方据此告警且**不关窗**）。失败**不清 pending**：用户在这次
        会话里改过、还没落盘的草稿不能因为一次失败的重置而消失；成功才作废草稿
        （重置后的控件值已无意义，调用方随即走 ``_close_after_external_write`` 跳过
        整表写回）。

        编辑基线（``_pinned_edit_generation``）在两条结局上都解钉：重置**成功**时代次
        已经推进，基线作废；重置**失败**时用户还要在这个窗口里继续改并保存，抱着一个
        刚被自己作废的基线会让他此后每一次保存都被判"过期"——那是把用户关在门外，不是
        保护。解钉后下一次读按当前磁盘代次重新取基线。
        """
        snapshot = pet_profiles.read_slot_snapshot(self._instance.dir, self._slot_id)
        if snapshot.status != "ok":
            logging.warning(
                "恢复默认（本宠）：档案或代次不可信（%s），未做任何改动: %s",
                snapshot.status, snapshot.reason)
            self._pinned_edit_generation = _UNPINNED
            self._invalidate_slot_snapshot()
            return False
        result = pet_profiles.reset_slot_profile(
            self._instance.dir, self._slot_id, generation=snapshot.generation)
        self._pinned_edit_generation = _UNPINNED
        self._invalidate_slot_snapshot()
        if result.status not in ("ok", "no-profile"):
            logging.warning("恢复默认（本宠）未生效：%s（%s）", result.status, result.reason)
            return False
        if result.status == "no-profile":
            logging.info("恢复默认（本宠）：该槽本来就没有档案（已在默认值上），"
                         "只推进代次作废迟到写")
        self._pending_slot.clear()
        self._pending_main.clear()
        self._invalidate_slot_snapshot()
        # 档案侧已经回同源默认，本视图那份"slot 身份的 Config"内存也要落到同一个答案上：
        # 两份表示不该各说各话（否则任何仍读它的人拿到的是重置前的旧值）。
        for key in pet_profiles.PER_PET_PREFERENCE_KEYS:
            self._instance.data[key] = pet_profiles.same_source_default(key)
        self._refresh_runtime_view()
        return True

    def save_geometry(self, geometry, generation) -> bool:
        """几何自存：**直写档案**的旁路（不经 pending，也不动编辑基线）。

        写成功后必须作废只读快照：``_invalidate_slot_snapshot`` 在有未写草稿时按
        「表单显示口径」故意保留快照，而这条旁路写完自己的文件、下一次 ``reload()``
        也解不开那份旧快照——于是 ``get('scale')`` 读回**写入之前**的尺寸，窗口照着它
        弹回去（#304 的「闪一下」）。只刷新只读视图（``refresh_profile_view`` 同一
        接缝），用户草稿与 ``_pinned_edit_generation`` 一个都不碰。
        """
        ok = pet_profiles.write_profile_geometry(
            self.dir, self._slot_id, geometry, generation=generation).ok
        if ok:
            self.refresh_profile_view()
        return ok

    def refresh_profile_view(self) -> None:
        """作废这只宠档案的**只读运行视图**缓存（不清草稿、不动编辑基线）。

        角色同步边界（``PetInstance.sync_chat_character``）用的就是它：换角色写的是
        ``config-slot-N.json``，而本视图读一次就把档案快照缓存住了——不刷新的话
        **尚未构造**的聊天面（首次岛对话气泡按 ``AppShell._chat_identity_config``
        取角色）拿到的还是旧角色，只有已建面被同步（2026-10-10 终审 F01）。

        与 :meth:`reload` 的分野：``reload`` 还会重读主配置，这里只碰这只宠那份档案的
        显示快照。与 ``_invalidate_slot_snapshot`` 的分野：那个在有待写草稿时故意保留
        快照（表单显示口径），而"运行只读视图"必须始终反映磁盘——两者都**不碰**
        ``_pending_slot``（用户草稿）与 ``_pinned_edit_generation``（表单首读钉住的
        编辑代次），所以刷新不会把表单里那份旧草稿放行到新代次上。
        """
        self._slot_snapshot = None
        self._slot_reader = None

    def new_form_view(self) -> "SlotRoutedConfig":
        """给**一张新的设置表单**开一份独立编辑视图：同一身份、同一持久真值，草稿与编辑
        基线各自一份。

        独立到什么程度（2026-10-11 astra CF01-GLOBAL 收口）：
        - 每宠面：``_pending_slot`` 与 ``_pinned_edit_generation`` 归本视图（旧表单钉住的
          代次不许被新会话洗掉，"重开设置"必须是新基线——见下）；
        - **全局面**：本视图拿到的是 :func:`_form_global_config` 开的**另一份 ``Config``
          实例**（同一 config.json、同一目录锁、同一条 reset 侧车），不是运行期那份对象。
          全局草稿真正的存放处是 ``Config.data`` 这份内存，不是 ``_pending_main`` 登记表：
          共用同一个 Config 时，表单的全局 ``set`` 会立刻写进运行期内存（运行期读到没保存
          的值），运行期之后任何一次无关保存都会把共享 Config 的**全部**差异一起提交（把
          表单没写成的草稿落盘），反向的 discard 又会把运行期自己的合法待写值整份回退掉。

        为什么必须是新基线：进程内 fallback 下设置窗拿到的是**运行期那份**路由对象
        （``route_settings_config`` 对已路由对象原样返回），而"编辑代次基线"是**这张表单
        第一次读盘时的代次**。外部合法写者推进过该槽代次之后，旧基线一直留在那个共享对象
        上：用户按产品提示"关闭本窗口后重新打开设置"，拿到的还是同一条旧基线，于是每次保存
        都被判 ``stale-generation``——重开永远救不回来（死路）。

        ``save()``/``reload()`` **不**重钉基线（那正是"旧控件值获准写上新代次"的口子），
        重钉只发生在新视图的第一次读——也就是"新表单开始编辑"那一刻。

        新视图记着自己的来处（``_runtime_view``）：成功落盘后作废那份的只读快照并让全局面
        吸收盘上的值（``_refresh_runtime_view``），"即时生效"的行在运行期立刻可见，而**运行期
        自己的未写草稿一个都不动**。
        """
        view = SlotRoutedConfig(self._instance, _form_global_config(self._main), self._slot_id)
        view._runtime_view = self
        return view

    def absorb_persisted_globals(self) -> None:
        """让本视图跟上盘上现值——**有本视图自己的未写差异时一律不动**。

        为什么不重放本地草稿（上一版写法，2026-10-11 父级复核抓回的正是这条）：本视图的
        ``_pending_main`` 记的是**整值**。``reload()`` 会把 ``_save_baseline`` 换成盘上那份，
        于是本地差异只能靠整值重放恢复——而整值重放会把**本视图没碰过的同字典兄弟叶**按旧
        快照盖回内存（例：本视图对 ``dynamic_island`` 只改了 ``opacity``，另一份视图改了同一
        个字典的 ``accent``；重放整字典会把 ``accent`` 盖回旧值，本视图下一次 ``save()`` 的
        递归合并再把它当"本地差异"提交，对方的 ``accent`` 就被静默覆盖）。这正是 astra 之前
        修过的 nested 同字典异叶丢更新，不能因为换了函数名重引一遍。另外，**直接改
        ``_main.data``（不经过 ``_pending_main`` 登记表）的差异**登记表根本保护不到。

        正确口径 = 两分：
        - **脏**（``has_local_changes``）：**什么都不做**。本视图的 ``data`` / ``_save_baseline``
          一个字节都不动，交给它自己下一次 :meth:`Config.save`——那才是既有实现：目录锁内重读
          盘上那份，按 ``_save_baseline`` 的相对差异**递归合并**，只有它真正改过的叶被提交，
          兄弟叶与别人的新值原样保留；reset 侧车代次也照旧由那道门判（脏 + 代次变了 = 拒绝写，
          不洗 token）。
        - **干净**（内存 == 基线，没有可丢的差异）：``reload()`` 吸收盘上现值，让只读视图尽快
          跟上外部已落盘的值（也能吃到外部 reset 的新代次）。

        **read 契约（显式说明）**：本视图脏的时候，``get()`` 读的是**本视图自己的未写草稿**
        （以及它构造/上次 reload 时的盘上值）——它**不会**临时反映别人刚写成功的同名字段；
        那要等它自己保存（锁内合并）或下一次 reload。干净的时候则紧跟盘上现值。宁可短期
        read view 滞后，也不动编辑基线：丢别人的更新或丢自己的草稿，都比滞后一次读更坏。
        """
        if self._main.has_local_changes:
            return
        self._main.reload()

    def profile_generation(self):
        self._slot_values()
        return self._slot_snapshot.generation

    def __getattr__(self, name):
        """其余属性面一律转发主配置（chat / 角色别名 / 点击台词 / 密钥 / 域入口）。"""
        return getattr(self._main, name)

    # ------------------------------------------------------------ 清洗读源
    def _cleaned_slot_value(self, key, raw_value):
        return self._cleaned_slot_data().get(key, raw_value)

    def _cleaned_slot_data(self) -> dict:
        """只清洗已捕获的原值；缓存随档案快照与 generation 一起作废。"""
        raw = self._slot_values()
        if self._slot_reader is None:
            self._slot_reader = normalize_pet_profile_snapshot(raw)
        return self._slot_reader

    # ------------------------------------------------------------ 落盘内部
    def _invalidate_slot_snapshot(self) -> None:
        """作废档案快照与清洗读源（落盘/重读后按需重取，两者同代）。"""
        if not self._pending_slot:
            self._slot_snapshot = None
            self._slot_reader = None

    def _slot_values(self) -> dict:
        """锁内快照只读一次；不可信时显示默认，不另做无锁读取。

        **首读同时钉住这次编辑的代次基线**：读到的快照就是设置页控件取值的那一份，
        两件事必须同代（否则控件显示 A 代的偏好、落盘却带着 B 代的 token，重置/重建
        会被静默抹掉）。之后 ``reload()`` 作废快照重读只影响**显示**，不影响这个基线
        ——写路径只认基线（见 :meth:`_flush_slot`）。
        """
        if self._slot_snapshot is None or self._slot_snapshot.status != "ok":
            self._slot_snapshot = pet_profiles.read_slot_snapshot(
                self._instance.dir, self._slot_id)
            self._slot_reader = None
            if (self._pinned_edit_generation is _UNPINNED
                    and self._slot_snapshot.status == "ok"):
                self._pinned_edit_generation = self._slot_snapshot.generation
        profile = self._slot_snapshot.profile
        data = getattr(profile, "data", None)
        return data if isinstance(data, dict) else {}

    def _pin_edit_generation(self) -> int | None:
        """取（必要时现钉）这次编辑的代次基线；**钉不上**时返回 ``None``（不写、留草稿）。

        关键区别：``None`` 不是"永久钉死为不可用"，而是"此刻钉不上"。读失败（坏档案、
        锁超时）**不**写进 ``_pinned_edit_generation``——否则用户把档案修好、或锁释放之后
        这一次编辑再也存不进去（"草稿必须还在：修好档案后一次保存就能落盘"是既有契约）。
        """
        self._slot_values()
        pinned = self._pinned_edit_generation
        return None if pinned is _UNPINNED else pinned

    def _flush_slot(self) -> bool:
        """把 pending 的每宠键**一批**落进档案（锁内重读合并，保留该身份的其它键）。

        ``user_customized`` 与每宠键走同一路：它是档案的本地键（运行期不看它，只有落种
        逻辑看），写进主配置会是第三个真值。本次落盘带着它就置位（用户在这只子肥鱼
        自己的设置页保存过 ⇒ 下次生成不被主设置刷新）；pending 里没有该键时传 None
        保持原值。

        **写用的是钉住的编辑基线代次，不是"落盘那一刻的最新代次"**：表单里的控件值来自
        打开设置窗时读到的那一代，谁在这期间推进了该槽代次（外部 reset、主进程重启复活
        同一槽），旧控件值就不再有资格写回——否则重置/重建被静默抹掉，而用户以为自己
        只是"保存了刚才改的一项"。被拒时 ``save()`` 如实返回 False、**草稿留着**，
        调用方据此提示"外部改动先到，本次未保存"。

        **失败不清 pending**：坏档案、锁被占、代次过期、写盘失败都返回 False 并让草稿留在
        待写区，下一次保存/重置再落一次——"点了保存、什么都没发生、改动还被吞掉"是这里最
        不能出的错。**"钉不上"与"钉死了"必须分开**：读失败时基线仍是未钉状态，档案修好 /
        锁释放之后下一次保存照常收敛。写盘成功才同步身份那份 Config 的内存视图（视图与
        文件是同一份身份的两种表示）。

        两个失败分支都往 ``self._last_slot_failure`` 记一条 :class:`SaveFailure`（``save()``
        据此组装对界面的描述）：**代次落后**与坏档案是两种完全不同的处境，界面文案与出口
        都按这个类别分（见 ``pet/settings_close_flow``）。
        """
        self._last_slot_failure = None
        pending = dict(self._pending_slot)
        if not pending:
            self._invalidate_slot_snapshot()
            return True
        flag = bool(pending.pop("user_customized")) if "user_customized" in pending else None
        generation = self._pin_edit_generation()
        if generation is None:
            snapshot = self._slot_snapshot
            status = snapshot.status if snapshot is not None else ""
            self._last_slot_failure = SaveFailure(
                kind=classify_slot_status(status), status=status,
                reason=str(getattr(snapshot, "reason", "") or "档案或代次不可信"),
                path=str(pet_profiles.slot_profile_path(self._instance.dir, self._slot_id)))
            logging.warning(
                "保存这只桌宠的独立设置失败：档案或代次不可信，一个字节都没写（%s）",
                status or "unknown")
            if flag is not None:
                pending["user_customized"] = flag
            self._pending_slot = {**pending, **self._pending_slot}
            return False
        result = pet_profiles.write_profile_settings(
            self._instance.dir, self._slot_id, pending, user_customized=flag,
            generation=generation)
        if result.status != "ok":
            self._last_slot_failure = SaveFailure(
                kind=classify_slot_status(result.status), status=result.status,
                reason=result.reason, path=str(result.path or ""))
            logging.warning("保存这只桌宠的独立设置失败：%s（%s）", result.status,
                            result.reason)
            if flag is not None:
                pending["user_customized"] = flag
            # 新的编辑优先（单线程里不会有，防御而已）
            self._pending_slot = {**pending, **self._pending_slot}
            self._invalidate_slot_snapshot()
            return False
        self._pending_slot.clear()
        self._invalidate_slot_snapshot()
        for key, value in pending.items():
            self._instance.data[key] = value
        if flag is not None:
            self._instance.data["user_customized"] = flag
        self._refresh_runtime_view()
        return True

    def _refresh_runtime_view(self) -> None:
        """落盘成功后让**运行期那份**视图立刻看到刚写下的值（表单视图才有这一层）。

        表单与运行期是两份视图，各自缓存/内存都不共享，所以要分两件事：
        - 每宠面：作废它的只读快照（``refresh_profile_view``）——"即时生效"的每宠键
          （点击音效这类 ``toggled`` 直连写盘的开关）就地可见，不必等设置窗关闭；
        - 全局面：``absorb_persisted_globals``——读盘吸收刚写成功的值，但**保留运行期
          自己的未写草稿**（运行期菜单里的改动不能被这次吸收洗掉）。
        """
        runtime = self._runtime_view
        if runtime is None or runtime is self:
            return
        runtime.refresh_profile_view()
        runtime.absorb_persisted_globals()

    def _flush_main(self) -> bool:
        """沿用编辑基线交给 Config 锁内递归合并；失败保留 pending。

        不在提交前 reload 或重放整字典：这会把外部更新变成本地差异，
        也会将 reset 前的草稿错误绑定到新代次。

        落盘成功后让运行期那份视图吸收盘上现值（含本次写成功的键）——但不动它自己的
        未写草稿，见 :meth:`absorb_persisted_globals`。
        """
        if not self._pending_main:
            return True
        ok = bool(self._main.save())
        if ok:
            self._pending_main.clear()
            self._refresh_runtime_view()
        return ok


def save_failure_of(config) -> SaveFailure | None:
    """这份配置面上一次 ``save()`` 的失败描述；没失败或**不适用**（普通 ``Config``）→ ``None``。

    只有 :class:`SlotRoutedConfig` 维护这个描述：它才有"档案 + 主配置两路"的失败面。
    非 slot 身份/legacy 整表身份（原样返回的那份 ``Config``）没有这一层信息，界面按
    "未能写入磁盘"如实上报——**不猜**类别（猜错就是把用户引向错误的方向）。
    """
    if isinstance(config, SlotRoutedConfig):
        return config.last_save_failure
    return None


def is_main_identity(instance_id) -> bool:
    """这个实例身份是不是**主宠身份**（``""`` 或 ``slot-0``）。

    G1 起 slot0 有显式身份 ``slot-0``，"主身份"不再等于"空身份"：按字符串真值猜
    会让主宠自己的设置页把自己当成子宠（「一键退出子肥鱼」据此写出 ``target=0``，
    两个消费者都把它当子槽去查 → 主 sprite 不在 ``_spawned`` 里 → **静默 no-op**，
    按钮看着能用、点下去什么都不发生）。判据与 ``AppShell`` 的
    ``if self.slot_id in (None, 0) else None`` 同源：slot 号，不是身份字符串。
    """
    if instance_id is None or instance_id == "":
        return True
    return overlay_settings_command.slot_from_instance_id(instance_id) == 0


def global_config_of(config):
    """路由视图背后的**全局**配置面（显式全局身份：``config.json``，不带 slot）。

    非路由视图（全局页 / legacy ``--slot N`` 的整表身份）原样返回：它们本来编
    辑的就是手上那一份，不另造对象。

    为什么取同一个对象而不是新建 ``Config``：规范 §五「只有一处真值」——全局
    reset 必须落在设置窗正在显示的那份全局配置上，另造一份实例会让两边各持一份
    内存视图，落盘时互相盖回去。``SlotRoutedConfig`` 的 ``_main`` 就是
    ``route_settings_config`` 给它的那一份全局配置（表单视图那份由
    :func:`_form_global_config` 开），落点逐字相同：同一个 ``config.json``、同一把
    目录锁、同一条 reset 侧车。
    """
    if isinstance(config, SlotRoutedConfig):
        return config._main
    return config


def _form_global_config(runtime_main: Config) -> Config:
    """给一张表单开一份**独立的全局配置内存视图**：同一目录、同一身份，初值只从盘上取。

    为什么必须独立（2026-10-11 astra CF01-GLOBAL）：全局草稿真正的存放处是 ``Config.data``
    这份内存，不是路由视图的 ``_pending_main`` 登记表。两份视图共用一个 ``Config`` 时：

    - 表单的全局 ``set`` 立刻写进运行期那份内存 → 运行期读到还没保存的值；
    - 运行期之后任何一次无关保存提交的是**共享 Config 的全部差异** → 把表单没写成的草稿
      一起落盘（用户明确"继续编辑"的东西被写进去了）；
    - 反向：表单 discard 回退整份 ``data`` → 运行期自己合法的待写值被无声抹掉。

    "同一持久真值"要求的是**一个文件 + 一把锁 + 一条 reset 侧车**，不要求"多个编辑者共用
    一份 dirty 缓冲"。所以这里开一份新 ``Config``：它自带 ``data`` / ``_save_baseline`` /
    ``_reset_generation``，落盘仍走 :meth:`Config.save` 的既有实现（锁内重读 + 递归合并
    + 代次门），**不新建第二套保存实现**。

    初值只取盘（不复制运行期的内存草稿）；盘上没有的明文凭据（keyring 不可用时只存内存）
    按既有密钥策略从运行期那份**克隆**进来，只进内存——``Config._redacted_data`` 落盘时
    照旧剔除，密钥"只进不出"的纪律不因为多了一份视图而改变。
    """
    fresh = Config(base=runtime_main.dir.parent, instance_id=runtime_main.instance_id)
    carried = chat_secret_carryover(runtime_main.data.get("chat"))
    if not carried:
        return fresh
    providers = fresh.data.setdefault("chat", {}).setdefault("providers", {})
    for provider_id, kept in carried.items():
        provider = providers.get(provider_id)
        if not isinstance(provider, dict):
            # 盘上没有这个 provider 的条目（非默认服务商只存在于运行期内存）：
            # 建一个只装凭据的最小容器，与 reset 的保留口径一致。
            provider = {"name": kept.get("name") or provider_id}
            providers[provider_id] = provider
        for field, value in kept.items():
            provider.setdefault(field, value)
    return fresh


def route_settings_config(config):
    """给设置页的配置面套上读写路由；不适用时**原样返回同一个对象**。

    配置身份是slot-N且有配置目录即分流，含slot0，拓扑不改变归属。
    空身份（主宠的设置会话）同样按 slot0 分流：运行期每宠键只从
    ``config-slot-0.json`` 读，空身份直写在整表保存时被当退役键 pop 掉
    （写入蒸发但 save 报成功，#271）。非slot诊断身份原样返回。

    幂等：已套过路由的配置原样返回。
    """
    if config is None or isinstance(config, SlotRoutedConfig):
        return config
    identity = str(getattr(config, "instance_id", "") or "").strip()
    slot_id = overlay_settings_command.slot_from_instance_id(identity)
    base = getattr(config, "dir", None)
    if base is None:
        return config
    if slot_id is None:
        if identity:
            return config
        # 空身份 = 主宠设置会话（#271）：传入的那份指向全局 config.json，
        # 不能当每宠面用——另建 slot0 档案脸，全局面照旧新建主配置。
        # 注意：这条分支不使用传入对象（其内存草稿/未落盘改动不会带入表单）；
        # 生产调用方（独立设置进程）传进来的本来就是刚从盘上读的。
        # 先迁移后路由（审查 P1）：未迁移目录直接进设置会话时，若让默认档案
        # 抢先落盘，Config.save() 的迁移分支会被跳过，旧每宠值被当退役键
        # 清掉且没有备份；迁移不可信时退化为不分流，不在坏地基上盖楼。
        migration = pet_profiles.migrate_slot0_profile(base)
        if not migration.ok:
            logging.warning("slot0 迁移不可信，空身份设置会话不分流：%s", migration)
            return config
        slot_id = 0
        config = Config(base=base.parent, instance_id="slot-0")
    main_config = Config(base=base.parent, instance_id="")
    return SlotRoutedConfig(config, main_config, slot_id)
