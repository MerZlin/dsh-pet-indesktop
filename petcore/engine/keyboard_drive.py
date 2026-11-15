"""键盘控制移动的纯几何：从 move_curves 切出前摇/循环/收尾，生成连续位移计划。"""

from __future__ import annotations

from dataclasses import dataclass

from .motion import move_position_at_frame

_EPS = 1e-6
#: 连续驱动用的大圈数：位置公式在圈数内是线性的，永远走不到计划终点。
DRIVE_LOOPS = 4096


@dataclass(frozen=True)
class MoveSegments:
    """一段移动 clip 的三段边界（帧号闭区间）。"""

    start: int
    loop_start: int
    loop_end: int
    end: int

    @property
    def loop_frames(self) -> int:
        return max(1, self.loop_end - self.loop_start + 1)

    @property
    def has_recovery(self) -> bool:
        return self.loop_end < self.end


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def move_segments(
    curve,
    frame_count: int,
    override: dict | None = None,
) -> MoveSegments:
    """前导 0 = 前摇，尾部 1 = 收尾，中间 = 循环段。

    ``override`` 支持 ``{"start","loop_start","loop_end","end"}``；非法值回落
    自动推导，不抛异常（素材包写错不应让桌宠不能走）。
    """
    total = max(0, _as_int(frame_count, 0))
    if total <= 1:
        return MoveSegments(0, 0, max(0, total - 1), max(0, total - 1))
    if isinstance(override, dict):
        start = max(0, min(total - 1, _as_int(override.get("start"), 0)))
        loop_start = max(start, min(total - 1, _as_int(override.get("loop_start"), start)))
        loop_end = max(loop_start, min(total - 1, _as_int(override.get("loop_end"), total - 1)))
        end = max(loop_end, min(total - 1, _as_int(override.get("end"), total - 1)))
        if loop_end > loop_start:
            return MoveSegments(start, loop_start, loop_end, end)
    values = []
    if isinstance(curve, (list, tuple)) and curve:
        for item in curve[:total]:
            try:
                values.append(max(0.0, min(1.0, float(item))))
            except (TypeError, ValueError):
                return MoveSegments(0, 0, total - 1, total - 1)
    if len(values) < total:
        values.extend([1.0] * (total - len(values)))
    first = next((index for index, value in enumerate(values) if value > _EPS), None)
    last = next(
        (
            index
            for index in range(total - 1, -1, -1)
            if values[index] < 1.0 - _EPS
        ),
        None,
    )
    if first is None or last is None or first >= last:
        return MoveSegments(0, 0, total - 1, total - 1)
    return MoveSegments(0, first, last, total - 1)


def _normalized_loop_curve(curve, segments: MoveSegments):
    if not isinstance(curve, (list, tuple)):
        return None
    loop = list(curve[segments.loop_start : segments.loop_end + 1])
    if len(loop) < 2:
        return None
    try:
        values = [float(item) for item in loop]
    except (TypeError, ValueError):
        return None
    base = values[0]
    span = values[-1] - base
    if span <= _EPS:
        return None
    return tuple(max(0.0, min(1.0, (value - base) / span)) for value in values)


def _loop_curve_span(curve, segments: MoveSegments) -> float:
    """循环段在整条位移曲线上占的比例（``stride`` 是整段 0..1 的位移）。

    把「绊倒/趴地」排除出循环段后，循环段只覆盖曲线的一部分；每圈仍按
    整段 stride 走会让移动速度凭空变快。缺曲线时退回 1.0；循环段是
    平坦曲线（静帧/原地素材）时返回 **0.0**，否则会按整段 stride 滑行。
    """
    if not isinstance(curve, (list, tuple)) or len(curve) < 2:
        return 1.0
    last = len(curve) - 1
    try:
        start_index = max(0, min(last, int(segments.loop_start)))
        end_index = max(0, min(last, int(segments.loop_end)))
        start = float(curve[start_index])
        end = float(curve[end_index])
    except (IndexError, TypeError, ValueError):
        return 1.0
    return abs(end - start)


def build_drive_plan(
    *,
    segments: MoveSegments,
    curve,
    direction: tuple[float, float],
    stride: float,
    start_x: float,
    start_y: float,
    loops: int = DRIVE_LOOPS,
) -> dict:
    """生成连续驱动计划；位置仍由 ``move_position_at_frame`` 统一推进。"""
    dx, dy = float(direction[0]), float(direction[1])
    length = (dx * dx + dy * dy) ** 0.5
    if length <= _EPS:
        return {}
    dx, dy = dx / length, dy / length
    stride_px = max(1.0, float(stride))
    loop_count = max(1, int(loops))
    drive_curve = _normalized_loop_curve(curve, segments)
    per_loop = len(drive_curve) if drive_curve else segments.loop_frames
    distance = stride_px * loop_count * _loop_curve_span(curve, segments)
    return {
        "start_x": float(start_x),
        "start_y": float(start_y),
        "target_x": float(start_x) + dx * distance,
        "target_y": float(start_y) + dy * distance,
        "loops": loop_count,
        "frames_per_loop": max(1, int(per_loop)),
        "total_frames": max(1, int(per_loop)) * loop_count,
        "curve": drive_curve,
        "stride": stride_px,
    }


def drive_position_at_frame(plan: dict, frames_elapsed: float) -> tuple[float, float]:
    """连续驱动的窗口位置；与漫游共用同一套帧号插值。"""
    return move_position_at_frame(plan, frames_elapsed)


__all__ = [
    "DRIVE_LOOPS",
    "MoveSegments",
    "build_drive_plan",
    "drive_position_at_frame",
    "move_segments",
]
