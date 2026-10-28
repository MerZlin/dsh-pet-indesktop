from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .api import ApiPort
from .chat import ChatPort, QuickChatPort
from .http import HttpPort
from .screen import ScreenAskPort, ScreenPort
from .speech import SpeechPort


@dataclass(frozen=True, slots=True)
class AnimationState:
    """桌宠当前动画的可查询状态（``PresentationPort.animation_state()`` 的返回值）。

    ``name`` 是正在播的动画名（空串 = 读不到名字）；``loop`` 是核心是否仍持着
    ``play_animation(loop=True)`` 的循环标志——**核心会在探头/击飞/拖拽接管或改播
    别的素材时清掉它**，这也是「唱歌动作被打断后不再回来」的判据。``locked`` 表示
    此刻处于接管态（探头/击飞/拖拽），此时请求动画会被核心降级/立刻清掉，调用方
    应当等它结束，别去抢。
    """

    name: str = ""
    loop: bool = False
    locked: bool = False


class PresentationPort(Protocol):
    def say(
        self,
        text: str,
        *,
        duration_ms: int = 4000,
        force: bool = False,
        subtitle: str = "",
        owner: str = "",
    ) -> bool: ...
    def say_sequence(
        self,
        items: Sequence[tuple[str, str, int]],
        *,
        force: bool = False,
        on_show: Callable[[str], None] | None = None,
        owner: str = "",
    ) -> bool:
        """依次显示多条气泡；``items`` 是 ``(正文, 副标题, 停留毫秒)``。

        ``on_show`` 在每一条真正显示时回调（参数是正文），供调用方按条触发语音。

        ``owner`` 是调用方身份（缺省 = 匿名）：见 :meth:`begin_scene`。持有展示会话的
        owner 必须带上自己的 ``owner``，否则它自己的气泡也会被闸门挡下。
        """
        ...
    def begin_scene(self, owner: str) -> bool:
        """申请一段「我在占用气泡」的展示会话；返回是否由 ``owner`` 持有。

        同一 owner 重入会累加计数（必须成对 ``end_scene``）；另一个 owner 正持有时
        返回 ``False``，调用方应当让路。持有期间其它 owner 的 ``say()`` /
        ``say_sequence()`` 返回 ``False``（不弹气泡、不报错），``force=True`` 仍然可以
        显示（用户动作带来的反馈不该被吞掉）。
        """
        ...
    def end_scene(self, owner: str) -> None:
        """释放一次展示会话（与 ``begin_scene`` 成对）；别人的会话不受影响。"""
        ...
    def refresh_scene(self, owner: str) -> bool:
        """续期且不加重入计数：返回会话是否仍归 ``owner``（持有者按拍调用）。"""
        ...
    def animation_state(self) -> AnimationState | None:
        """当前动画状态；端口/窗口查不到时返回 ``None``（= 未知，别据此重试）。"""
        ...
    def for_slot(self, slot: int) -> PresentationPort: ...
    def show_image(
        self,
        path: Path | str,
        caption: str = "",
        *,
        duration_ms: int = 4000,
        scale: float = 1.0,
        force: bool = False,
        owner: str = "",
    ) -> bool:
        """图片气泡；``force`` / ``owner`` 的语义与 :meth:`say` 完全一致
        （歌词这类展示会话期间，别人的图片气泡同样要让路）。"""
        ...
    def play_animation(self, name: str, *, loop: bool = False) -> bool: ...
    def release_loop(self) -> bool: ...
    def play_event(self, group: str, variant: str | int | None = None) -> bool: ...
    def notify(self, title: str, message: str, *, force: bool = False,
               on_click: Callable[[], None] | None = None) -> bool: ...
    def visible(self) -> bool: ...


class PetHandle(Protocol):
    slot: int
    is_primary: bool
    config: ConfigPort

    def body_rect(self) -> tuple[int, int, int, int]: ...
    def velocity(self) -> tuple[float, float]: ...
    def move_by(self, dx: float, dy: float) -> None: ...
    def set_velocity(self, vx: float, vy: float) -> None: ...
    def show(self) -> None: ...
    def hide(self) -> None: ...
    def say(self, text: str, duration_ms: int = 4000, subtitle: str = "") -> None: ...
    def play_animation(self, name: str) -> bool: ...
    def play_event(self, group: str, variant: str | int | None = None) -> bool: ...
    def animations(self) -> tuple[str, ...]: ...


class PetRegistryPort(Protocol):
    def pets(self) -> tuple[PetHandle, ...]: ...
    def primary(self) -> PetHandle | None: ...
    def active(self) -> PetHandle | None: ...


class InteractionPort(Protocol):
    def on_click(self, owner: str, callback: Callable[[Any], None]) -> Callable[[], None]: ...
    def on_drop(self, owner: str, callback: Callable[[list[Path]], None]) -> Callable[[], None]: ...
    def on_tick(self, owner: str, callback: Callable[[float], None], interval_ms: int = 16) -> Callable[[], None]: ...
    def cancel_owner(self, owner: str) -> None: ...


class ReminderPort(Protocol):
    def deliver(self, owner: str, slot: int, key: str, target: PresentationPort,
                title: str, message: str, *, guard=None, on_click=None) -> str: ...
    def cancel_owner(self, owner: str) -> None: ...
    def cancel_slot(self, slot: int) -> None: ...


class QuietPort(Protocol):
    def set_quiet(self, seconds: float) -> None: ...
    def quiet_active(self) -> bool: ...
    def quiet_until(self) -> float | None: ...


class SchedulerPort(Protocol):
    """定时器端口。

    可从任意线程调用（包括 mod 的 worker 线程）；回调一律在 GUI 线程执行，
    因此回调里可以安全地碰 Qt 控件。
    """

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> str: ...
    def call_every(self, interval_ms: int, callback: Callable[[], None]) -> str: ...
    def cancel(self, handle: str) -> None: ...
    def cancel_owner(self, owner: str) -> None: ...


class AudioPort(Protocol):
    def play(self, path: Path | str, volume: float | None = None) -> bool: ...
    def set_volume(self, volume: float) -> None: ...


class CredentialsPort(Protocol):
    def get(self, owner: str, key: str) -> str | None: ...
    def set(self, owner: str, key: str, value: str) -> None: ...
    def delete(self, owner: str, key: str) -> None: ...


class StoragePort(Protocol):
    def data_dir(self, owner: str) -> Path: ...
    def config_dir(self, owner: str) -> Path: ...
    def data_root(self) -> Path: ...


class ResourcesPort(Protocol):
    """应用只读资源目录（随包分发的模型/素材）；mod 不需要 import ``petcore.paths``。"""

    def bundle_path(self, *parts: str) -> Path: ...


class ConfigPort(Protocol):
    """配置端口：读全局配置；写只留给显式用户动作。

    ``set`` 只用于「菜单勾选/命令切换」这类用户刚按下的一次性动作（Core 负责
    立即落盘）。运行时**不许**把配置当状态存储反复写；设置页仍走 Core 托管的
    ``host.config``。
    """

    def get(self, key: str, default: Any = None) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...


class CommandPort(Protocol):
    def register(
        self, owner: str, command_id: str, label: str, callback: Callable[[], None]
    ) -> Callable[[], None]: ...


class MenuPort(Protocol):
    def register(self, owner: str, contribution: Any) -> Callable[[], None]: ...


class WindowsPort(Protocol):
    def parent_widget(self) -> Any: ...
    def create_overlay(self, owner: str, spec: Any) -> Any: ...


class InputPort(Protocol):
    def on_files_dropped(self, owner: str, callback: Callable[[list[Path]], None]) -> Callable[[], None]: ...


class SystemPort(Protocol):
    def screens(self) -> tuple[Any, ...]: ...
    def platform_name(self) -> str: ...
    def idle_seconds(self) -> float:
        """距离最后一次键鼠输入的秒数；不支持或读取失败返回 0（= 不判定闲置）。"""
        ...


class WorkerPort(Protocol):
    def start(self, owner: str, argv: list[str], env: dict[str, str] | None = None) -> Any: ...


class CapabilityPort(Protocol):
    def require(self, capability: str) -> None: ...


@dataclass
class HostPorts:
    presentation: PresentationPort | None = None
    scheduler: SchedulerPort | None = None
    audio: AudioPort | None = None
    credentials: CredentialsPort | None = None
    storage: StoragePort | None = None
    resources: ResourcesPort | None = None
    config: ConfigPort | None = None
    commands: CommandPort | None = None
    menu: MenuPort | None = None
    windows: WindowsPort | None = None
    input: InputPort | None = None
    system: SystemPort | None = None
    worker: WorkerPort | None = None
    capabilities: CapabilityPort | None = None
    pets: PetRegistryPort | None = None
    interaction: InteractionPort | None = None
    quiet: QuietPort | None = None
    api: ApiPort | None = None
    chat: ChatPort | None = None
    quick_chat: QuickChatPort | None = None
    screen: ScreenPort | None = None
    screen_ask: ScreenAskPort | None = None
    http: HttpPort | None = None
    speech: SpeechPort | None = None
    reminders: ReminderPort | None = None
