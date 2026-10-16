# -*- coding: utf-8 -*-
"""避字移动纯逻辑测试：坐标换算、遮字评分、落点选择。

不碰屏幕、不碰 OCR：全部用合成词框，跑在任意平台/无显示环境。
"""
from __future__ import annotations

import pytest

from pet.text_avoid import (
    CoordMap,
    TextBox,
    is_cjk,
    plan_avoid,
    text_cost,
)


def box(text: str, left: float, top: float, width: float = 20.0,
        height: float = 20.0) -> TextBox:
    return TextBox(text=text, left=left, top=top,
                   right=left + width, bottom=top + height)


# ------------------------------------------------------------------ 基础判定

def test_is_cjk_covers_common_and_extension_ranges():
    assert is_cjk('鱼')
    assert is_cjk('汉')
    assert not is_cjk('a')
    assert not is_cjk('。')
    assert not is_cjk('5')


def test_text_cost_counts_touched_and_area():
    boxes = [box('鱼', 100, 100)]
    touched, area = text_cost((90, 90, 130, 130), boxes)
    assert touched == 1
    assert area == pytest.approx(400.0)


def test_text_cost_ignores_sliver_overlap():
    """只擦到一两个像素不算压住（否则描边会让桌宠整屏乱窜）。"""
    boxes = [box('鱼', 100, 100)]
    touched, area = text_cost((98, 98, 100.2, 130), boxes)
    assert touched == 0
    assert area == pytest.approx(0.2 * 20.0)


def test_text_cost_cjk_only_filters_latin():
    boxes = [box('hello', 100, 100, width=60)]
    assert text_cost((90, 90, 200, 200), boxes, cjk_only=True)[0] == 0
    assert text_cost((90, 90, 200, 200), boxes, cjk_only=False)[0] == 1


def test_text_cost_empty_rect_is_free():
    assert text_cost((10, 10, 10, 10), [box('鱼', 0, 0)]) == (0, 0.0)


def test_mask_boxes_drops_only_intersecting():
    """抓屏里桌宠自己那块是噪声，必须剔掉；紧贴边缘但不相交的必须留下。"""
    from pet.text_avoid import mask_boxes

    foot = (100.0, 100.0, 300.0, 300.0)
    overlapping = box('盖住', 250, 200, width=100, height=40)   # 相交
    left_edge = box('紧贴左', 60, 200, width=40, height=40)     # right=100 正好贴边
    above = box('在上方', 150, 40, width=100, height=60)        # bottom=100 正好贴边
    far = box('离得远', 500, 500)
    kept = mask_boxes((overlapping, left_edge, above, far), foot)
    assert set(kept) == {left_edge, above, far}


def test_mask_boxes_with_degenerate_rect_keeps_everything():
    from pet.text_avoid import mask_boxes

    boxes = (box('甲', 0, 0), box('乙', 10, 10))
    assert mask_boxes(boxes, (5, 5, 5, 5)) == boxes


def test_fingerprint_ignores_the_given_rect():
    """桌宠自己那块不参与指纹，否则它一动指纹就变、"屏幕没变就别 OCR"永远失效。"""
    from PIL import Image

    from pet import text_avoid

    base = Image.new('RGB', (640, 480), (10, 20, 30))
    inside = base.copy()
    inside.paste((255, 0, 0), (100, 100, 200, 200))
    outside = base.copy()
    outside.paste((0, 255, 0), (400, 300, 500, 400))
    foot = (100.0, 100.0, 200.0, 200.0)

    assert (text_avoid.fingerprint(inside, ignore_rect=foot)
            == text_avoid.fingerprint(base, ignore_rect=foot))
    assert (text_avoid.fingerprint(outside, ignore_rect=foot)
            != text_avoid.fingerprint(base, ignore_rect=foot))
    # 不给 ignore_rect 时，桌宠那块的变化照样会被算进去
    assert text_avoid.fingerprint(inside) != text_avoid.fingerprint(base)


# ------------------------------------------------------------------ 坐标换算

def test_coord_map_round_trip_with_fractional_scale():
    """本机 1.5 倍缩放：逻辑 <-> 物理必须严格互逆。"""
    cmap = CoordMap.from_window(logical_x=1246, logical_y=45,
                                physical_rect=(1869, 67, 692, 422), scale=1.5)
    phys = cmap.to_physical(1246, 45)
    assert phys == pytest.approx((1869.0, 67.5), abs=0.51)
    back = cmap.to_logical(*phys)
    assert back == pytest.approx((1246.0, 45.0))
    rect = cmap.rect_to_physical((1246, 45, 1315, 91))
    assert rect[0] == pytest.approx(1869.0, abs=0.51)
    assert rect[2] - rect[0] == pytest.approx(103.5, abs=0.51)


def test_coord_map_scale_zero_falls_back_to_one():
    cmap = CoordMap.from_window(0, 0, (0, 0, 10, 10), 0)
    assert cmap.scale == 1.0


# ------------------------------------------------------------------ 落点选择

BASE = dict(
    body_w=200.0,
    body_h=240.0,
    cur_cx=500.0,
    cur_top=700.0,
    cx_lo=100.0,
    cx_hi=1200.0,
    top_lo=650.0,
    top_hi=900.0,
    step=5.0,
)


def test_no_boxes_keeps_target():
    plan = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=(), **BASE)
    assert plan.changed is False
    assert (plan.cx, plan.body_top) == (520.0, 700.0)
    assert plan.clear


def test_clean_proposed_target_is_left_alone():
    """原落点本来就干净 → 一次都不许挪（保持随机漫游的自然感）。"""
    boxes = [box('鱼', 20, 20)]
    plan = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=boxes, **BASE)
    assert plan.changed is False
    assert (plan.cx, plan.body_top) == (520.0, 700.0)


def _footprint(plan, body_w: float, body_h: float) -> tuple[float, float, float, float]:
    half = body_w / 2.0
    return (plan.cx - half, plan.body_top, plan.cx + half, plan.body_top + body_h)


def test_moves_to_nearest_clear_spot_when_proposed_covers_text():
    # 身体框 (420..620, 700..940)：这行字正落在中间
    boxes = [box('这是一个句子', 480, 780, width=80, height=24)]
    plan = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=boxes, **BASE)
    assert plan.changed is True
    assert plan.clear
    # 必须真的让开：新落点的身体框与词框不再相交
    assert text_cost(_footprint(plan, 200.0, 240.0), boxes)[0] == 0
    # 且不能跑到天边：就近解决
    assert abs(plan.cx - 520.0) <= 300.0


def test_picks_fewest_touched_when_nowhere_is_clean():
    """全屏都是字时不许比原落点更差：先比字数、再比面积。"""
    boxes = []
    for i in range(60):                      # 一整行小字块，铺满可搜索宽度
        boxes.append(box('甲', 100.0 + i * 24.0, 780.0))
    for i in range(20):                      # 第二行只在左侧，右侧本就干净
        boxes.append(box('乙', 100.0 + i * 24.0, 850.0))
    proposed_cx = 300.0
    proposed = text_cost((proposed_cx - 100, 700, proposed_cx + 100, 940),
                         boxes)
    plan = plan_avoid(proposed_cx=proposed_cx, proposed_top=700.0, boxes=boxes,
                      **BASE)
    assert plan.changed is True
    assert plan.touched <= proposed[0]
    if plan.touched == proposed[0]:
        assert plan.area <= proposed[1]
    # 第二行只在左侧：往右挪能少压一条
    assert plan.cx > proposed_cx


def test_prefers_nearest_clear_spot_over_far_one():
    """两侧都有干净落点时，挑离原落点更近的那个（不许舍近求远）。"""
    boxes = [box('中', 600, 780, width=100, height=30)]
    plan = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=boxes, **BASE)
    # 词框 600..700：向左让到 500 附近即可，向右要绕到 800 附近
    assert plan.changed is True
    assert plan.clear
    assert plan.cx < 520.0, '应往更近的左侧让'
    assert abs(plan.cx - 520.0) < abs(800.0 - 520.0), '没理由绕远路'
    assert abs(plan.cx - 520.0) < 25.0, '就近解决，只该让开一点点'


def test_travel_radius_is_a_preference_not_a_cap():
    """半径内没有干净落点时，允许走到半径外更干净的位置（半径不是硬上限）。"""
    boxes = [box('字', 400, 600, width=400, height=400)]   # 纵向躲不掉，只能横向让
    near = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=boxes,
                      max_travel=40.0, **BASE)
    assert near.clear, '半径内虽脏，半径外有干净落点就该走过去'
    assert abs(near.cx - 520.0) > 40.0


def test_no_pointless_trek_when_nothing_improves():
    """全屏都是字、任何位置都一样脏时，维持原落点，不许满屏乱走。"""
    boxes = [box('一整屏的字', -1000.0, 600.0, width=3000.0, height=600.0)]
    near = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=boxes,
                      max_travel=40.0, **BASE)
    far = plan_avoid(proposed_cx=520.0, proposed_top=700.0, boxes=boxes, **BASE)
    assert not near.clear
    assert near.changed is False, '哪里都一样脏时不该乱走'
    assert (near.cx, near.body_top) == (520.0, 700.0)
    assert (far.cx, far.body_top) == (520.0, 700.0)


def test_reachable_bounds_are_respected():
    boxes = [box('字', 500, 700, width=200, height=60)]
    plan = plan_avoid(proposed_cx=300.0, proposed_top=700.0, boxes=boxes, **BASE)
    assert BASE['cx_lo'] <= plan.cx <= BASE['cx_hi']
    assert BASE['top_lo'] <= plan.body_top <= BASE['top_hi']


def test_clamps_when_no_room_to_move():
    """可达界塌成一个点（贴边）时退回原落点，不许越界。"""
    boxes = [box('字', 500, 700, width=200, height=60)]
    plan = plan_avoid(proposed_cx=300.0, proposed_top=700.0, boxes=boxes,
                      body_w=200.0, body_h=240.0, cur_cx=300.0, cur_top=700.0,
                      cx_lo=300.0, cx_hi=300.0, top_lo=700.0, top_hi=700.0,
                      step=10.0, max_travel=700.0)
    assert plan.cx == 300.0
    assert plan.body_top == 700.0


# ------------------------------------------------- 落点吸附到步态整圈（准度）

def test_snap_to_gait_lands_on_an_exact_whole_stride():
    """没有字时，吸附到离"想要的落点"最近的整圈位置（而不是原样返回浮点）。"""
    from pet.text_avoid import snap_to_gait

    cx, touched, area = snap_to_gait(
        boxes=(), want_cx=1234.0, cur_cx=1000.0, stride=100.0, room=900.0,
        cx_lo=0.0, cx_hi=2000.0, body_w=200.0, body_h=100.0, body_top=0.0)
    assert cx == 1200.0          # 234/100 → 2 圈（200）比 3 圈（300）更近
    assert (cx - 1000.0) % 100.0 == 0.0
    assert (touched, area) == (0, 0.0)


def test_snap_to_gait_prefers_a_clean_whole_stride_over_a_dirty_closer_one():
    """整圈落点里有一个会压字时，宁可少走一圈也不能落在字上。

    这是"不够准"的根因：窗口会把位移量化到 loops×stride，所以直接返回精确干净点，
    真正落地的位置可能偏出半个步幅、把身体压回字上。
    """
    from pet.text_avoid import snap_to_gait

    # 身体框宽 200：cx=1200 → 1100..1300；cx=1300 → 1200..1400
    boxes = [box('挡路', 1310, 0, width=80, height=100)]   # 只压得到 cx=1300 那格
    cx, touched, area = snap_to_gait(
        boxes=boxes, want_cx=1290.0, cur_cx=1000.0, stride=100.0, room=900.0,
        cx_lo=0.0, cx_hi=2000.0, body_w=200.0, body_h=100.0, body_top=0.0)
    assert cx == 1200.0, '3 圈（1300）会压字，必须退到 2 圈（1200）'
    assert touched == 0
    assert (cx - 1000.0) % 100.0 == 0.0


def test_snap_to_gait_respects_room_and_step_zero():
    from pet.text_avoid import snap_to_gait

    # room 只够一圈 → 不许越界
    cx, _t, _a = snap_to_gait(
        boxes=(), want_cx=1900.0, cur_cx=1000.0, stride=100.0, room=100.0,
        cx_lo=0.0, cx_hi=2000.0, body_w=200.0, body_h=100.0, body_top=0.0)
    assert cx == 1100.0
    # 没有步幅数据（stride<=0）→ 原样返回，不做吸附
    cx, _t, _a = snap_to_gait(
        boxes=(), want_cx=1234.5, cur_cx=1000.0, stride=0.0, room=900.0,
        cx_lo=0.0, cx_hi=2000.0, body_w=200.0, body_h=100.0, body_top=0.0)
    assert cx == 1234.5


# ------------------------------------------------- 区间并集快路 vs 参考实现对拍

def _rect(plan, body_w: float, body_h: float) -> tuple[float, float, float, float]:
    half = body_w / 2.0
    return (plan.cx - half, plan.body_top, plan.cx + half, plan.body_top + body_h)


def test_fast_path_never_misses_a_clear_spot_randomised():
    """随机对拍：产品搜索空间里有干净落点，区间快路就必须给出干净落点。

    区间解里全是"闭区间 / 空隙取点 / 阈值换算成横向重叠"这类容易写错一格的地方，
    靠手写用例很容易两边一起错。这里用**参考实现 `text_cost` + 细网格**当 oracle：
    x 方向按 5px（比产品默认 12px 更细）扫，纵向**只用产品那 5 个档位**
    （``_y_candidates``）——纵向档位数是刻意的产品约束（避免桌宠上下乱跳），
    拿连续的 y 当 oracle 会得到"产品漏解"的假故障。快路漏解、端点取错、阈值
    换算错都会当场红。
    """
    import random as _random

    from pet.text_avoid import _y_candidates

    rng = _random.Random(20261015)
    checked = 0
    for _ in range(120):
        body_w = rng.choice([120.0, 200.0, 300.0])
        body_h = rng.choice([100.0, 200.0])
        cx_lo, cx_hi = 0.0, 600.0
        top_lo, top_hi = 0.0, 120.0
        boxes = []
        for _b in range(rng.randint(1, 7)):
            left = rng.uniform(-50.0, 650.0)
            top = rng.uniform(-40.0, 200.0)
            boxes.append(box('字', left, top,
                             width=rng.uniform(10.0, 120.0),
                             height=rng.uniform(8.0, 60.0)))
        proposed_cx = rng.uniform(cx_lo, cx_hi)
        proposed_top = rng.uniform(top_lo, top_hi)

        # oracle：产品的纵向档位 × 细横向网格 + 参考实现
        half = body_w / 2.0
        oracle_clear = False
        for top in _y_candidates(proposed_top, top_lo, top_hi, 60.0):
            cx = cx_lo
            while cx <= cx_hi:
                if text_cost((cx - half, top, cx + half, top + body_h), boxes)[0] == 0:
                    oracle_clear = True
                    break
                cx += 5.0
            if oracle_clear:
                break
        if not oracle_clear:
            continue
        checked += 1

        plan = plan_avoid(
            body_w=body_w, body_h=body_h,
            proposed_cx=proposed_cx, proposed_top=proposed_top,
            cur_cx=proposed_cx, cur_top=proposed_top,
            cx_lo=cx_lo, cx_hi=cx_hi, top_lo=top_lo, top_hi=top_hi,
            boxes=boxes, step=12.0, max_travel=1200.0, y_margin=60.0)
        assert plan.clear, (
            f'oracle 找到干净落点，产品却给出压字落点：plan=({plan.cx},{plan.body_top}) '
            f'touched={plan.touched} area={plan.area}')
        assert text_cost(_rect(plan, body_w, body_h), boxes)[0] == 0

    assert checked >= 20, f'样本里只有 {checked} 例存在干净落点，对拍覆盖不足'


def test_never_worse_than_the_proposed_target_randomised():
    """随机对拍不变量：避字结果不许比原来的随机落点更差（先比字数、再比面积）。"""
    import random as _random

    rng = _random.Random(20261016)
    for _ in range(200):
        body_w = rng.choice([120.0, 200.0, 400.0])
        body_h = rng.choice([80.0, 200.0])
        boxes = [box('字', rng.uniform(-100.0, 700.0), rng.uniform(-50.0, 200.0),
                     width=rng.uniform(10.0, 150.0), height=rng.uniform(8.0, 90.0))
                 for _b in range(rng.randint(1, 10))]
        proposed_cx = rng.uniform(0.0, 600.0)
        proposed_top = rng.uniform(0.0, 120.0)
        half = body_w / 2.0
        before = text_cost((proposed_cx - half, proposed_top,
                            proposed_cx + half, proposed_top + body_h), boxes)
        plan = plan_avoid(
            body_w=body_w, body_h=body_h,
            proposed_cx=proposed_cx, proposed_top=proposed_top,
            cur_cx=proposed_cx, cur_top=proposed_top,
            cx_lo=0.0, cx_hi=600.0, top_lo=0.0, top_hi=120.0,
            boxes=boxes, step=12.0, max_travel=1200.0, y_margin=60.0)
        assert (plan.touched, plan.area) <= before, (
            f'结果比原落点更差：{plan.touched}/{plan.area:.0f} vs {before[0]}/{before[1]:.0f}')
        assert text_cost(_rect(plan, body_w, body_h), boxes)[0] == plan.touched
