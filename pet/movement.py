# -*- coding: utf-8 -*-
"""移动驱动的纯逻辑（无 Qt）：漫游目标、朝向判定、步幅量化与帧驱动位置。

规则单一事实来源：朝向由「移动目标 + 屏幕位置」决定，与随机数无关——
随机只用来挑方向（两侧都够得着时）和步长。RNG 一律可注入，便于钉死分支。
位移按步态整圈量化（quantize_move），窗口位置由解码帧号推进
（move_position_at_frame）——移动速度恒等于动画步态速度，不打滑。
"""

from __future__ import annotations

import random

__all__ = ["body_reach", "choose_move_direction", "inward_facing",
           "move_position_at_frame", "quantize_move", "wander_target_y"]


def wander_target_y(
    start_y: float,
    top: float,
    bottom: float,
    height: float,
    margin: float,
    rnd=random,
) -> int:
    """Pick a bounded vertical wander target; injectable RNG keeps it testable."""
    y_lo = top + margin
    y_hi = bottom - height - margin
    if y_hi <= y_lo:
        return int(start_y)
    max_dy = max(40, int((y_hi - y_lo) * 0.25))
    return int(max(y_lo, min(y_hi, start_y + rnd.randint(-max_dy, max_dy))))


def body_reach(
    avail_left: float,
    avail_right: float,
    body_left: float,
    body_width: float,
    margin: float,
) -> tuple[float, float, float]:
    """身体框中心的可达界：返回 (cx, left_bound, right_bound)。

    漫游空间按角色身体框算（虚拟窗口坐标）——身体不越出工作区，不用
    "窗口中心 + _w/2"这类画布经验值。可达界含 margin 安全边距与身体半宽，
    即身体框中心允许落到的端点；两侧剩余空间 = |cx - 对应 bound|。
    """
    half_w = body_width / 2
    return (body_left + half_w,
            avail_left + margin + half_w,
            avail_right - margin - half_w)


def choose_move_direction(
    cx: float,
    left_bound: float,
    right_bound: float,
    min_distance: float,
    rnd=random,
) -> int | None:
    """按边缘可达性挑移动方向：-1 左 / +1 右 / None 两侧都走不了。

    先算两侧剩余空间，够 min_distance 才算「可达」（含等号）。单侧可达 →
    该侧（不掷骰）；两侧可达 → rnd.choice 二选一；都不可达 → None，调用方
    据此拒绝建立移动计划（边缘可达性闸门，替代此前的「先取朝向再算出界」）。
    """
    if cx - left_bound >= min_distance and right_bound - cx >= min_distance:
        return rnd.choice([-1, 1])
    if cx - left_bound >= min_distance:
        return -1
    if right_bound - cx >= min_distance:
        return 1
    return None


def inward_facing(
    cx: float,
    left_bound: float,
    right_bound: float,
    ratio: float = 0.07,
) -> str | None:
    """偏离中线超过滞回带时返回应朝的内侧方向，带内返回 None。

    left_bound/right_bound 是 body_reach 算出的身体可达界（含 margin 与
    身体半宽），不是屏幕可用区边缘。滞回带 = 可达界宽 × ratio，用于避免
    角色在中线附近反复翻转朝向。
    """
    center = (left_bound + right_bound) / 2
    band = (right_bound - left_bound) * ratio
    if cx < center - band:
        return "right"
    if cx > center + band:
        return "left"
    return None


def quantize_move(
    distance_px: float,
    stride_px: float,
    room_px: float,
    loop_duration: float,
) -> tuple[int, float, float]:
    """把目标位移量化为整圈步态：返回 (loops, distance, duration)。

    位置帧驱动后窗口速度 = stride/loop_duration 恒等于动画步态速度（不打滑），
    位移因此必须是步幅整倍数：n = max(1, round(distance/stride))。量化结果
    越出可达空间（room）时递减圈数（下限 1 圈）；单圈仍越界（room < stride，
    贴边场景）时位移夹到 room——此时速度略慢于步态，但身体绝不越界。
    duration = n × loop_duration（loop_duration 已含 playback_speed 换算）。
    """
    stride = max(1.0, float(stride_px))
    loops = max(1, round(distance_px / stride))
    while loops > 1 and loops * stride > room_px:
        loops -= 1
    return loops, min(loops * stride, float(room_px)), loops * float(loop_duration)


def move_position_at_frame(plan: dict, frames_elapsed: float) -> tuple[float, float]:
    """按帧进度插值窗口位置，与墙钟/播放速度解耦。

    无 curve：progress = frames_elapsed/total_frames 线性插值，夹到 [0,1]；
    末拍（frames_elapsed ≥ total-1，帧号 0-based）强制 progress=1 提交终点。
    有 curve（圈内逐帧位移曲线，curve[i] = 源帧 i 的圈内累计进度 0..1）：
    progress = (已完成圈数 + curve[当前帧]) / 总圈数——动画静帧段曲线走平，
    窗口原地停住；动帧段匀速推进。动帧才动、静帧不动，且位置只跟解码帧号
    走（playback_speed 变化不失步）。
    """
    curve = plan.get('curve')
    if curve:
        per_loop = max(1, int(plan['frames_per_loop']))
        loops = max(1, int(plan['loops']))
        total = max(1, int(plan['total_frames']))
        f = min(float(total), max(0.0, frames_elapsed))
        loop_idx = min(int(f // per_loop), loops - 1)
        intra = f - loop_idx * per_loop
        # 曲线按下标对齐源帧号；长度不符时按比例折算（防御性，正常逐帧等长）
        last = len(curve) - 1
        pos = min(float(last), intra * last / max(1, per_loop - 1))
        lo = int(pos)
        frac = pos - lo
        intra_progress = curve[lo] + (curve[min(lo + 1, last)] - curve[lo]) * frac
        progress = (loop_idx + intra_progress) / loops
    else:
        total = max(1, int(plan['total_frames']))
        # 帧号是 0-based：末拍 frames_elapsed == total-1，到位必须提交终点，
        # 否则窗口停在离目标 ~stride/frames 处（无 curve 角色）。
        if frames_elapsed >= total - 1:
            progress = 1.0
        else:
            progress = min(1.0, max(0.0, frames_elapsed / total))
    x = plan['start_x'] + (plan['target_x'] - plan['start_x']) * progress
    y = plan['start_y'] + (plan['target_y'] - plan['start_y']) * progress
    return x, y
