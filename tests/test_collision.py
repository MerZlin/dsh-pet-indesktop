# -*- coding: utf-8 -*-
"""针对 pet/collision.py (Phase 1) 物理模型与协议编解码的完整单元测试。

覆盖 plan4 §0-§4 及 §8 Phase 1 验收标准：
1. 3 种半轴（圆、扁宽椭圆、高瘦椭圆）
2. 3 种速度方向（正碰、斜碰擦边、相背分离）
3. 重合中心（两 ID 稳定哈希方向，绝不随机）
4. 零质量与无限质量（拖拽/锁定/普通）
5. 两两碰撞与三体及以上多体碰撞（最深重叠优先、迭代分离）
6. 动量守恒（误差 < 1e-6）
7. 分离后不重叠或残余重叠量 < 0.5px
8. 协议帧流式解析（4 字节大端长度前缀、粘包/半包、超长帧丢弃、空帧、坏 JSON、不抛未捕获异常）
9. 水位去重（重复 tick 拒绝、低于水位拒绝、epoch 切换整体重置）
10. 合速度 9000+9000 施加冲量后，经 physics.soft_clamp_speed 钳制后严格不超过 cap
"""

from __future__ import annotations

import math
import pytest

from pet import collision
from pet import physics


class TestCollisionEllipseDetection:
    """椭圆检测 (Broad-phase + Narrow-phase) 与半轴几何特性测试。"""

    def test_three_different_semi_axes_configurations(self):
        """测试 3 种半轴配置：圆形、扁宽椭圆、高瘦椭圆。"""
        # 1. 圆形 (rx=ry=50) 对撞
        c1, nx1, ny1, ov1, cx1, cy1 = collision.check_collision_ellipse(
            x1=0.0, y1=0.0, rx1=50.0, ry1=50.0,
            x2=80.0, y2=0.0, rx2=50.0, ry2=50.0,
            id1="pet-1", id2="pet-2",
        )
        assert c1 is True
        assert nx1 == pytest.approx(1.0, abs=1e-5)
        assert ny1 == pytest.approx(0.0, abs=1e-5)
        assert ov1 == pytest.approx(20.0, abs=1e-3)
        assert cx1 == pytest.approx(50.0, abs=1e-3)
        assert cy1 == pytest.approx(0.0, abs=1e-3)

        # 2. 扁宽椭圆 (rx=100, ry=40)
        c2, nx2, ny2, ov2, _, _ = collision.check_collision_ellipse(
            x1=0.0, y1=0.0, rx1=100.0, ry1=40.0,
            x2=150.0, y2=0.0, rx2=100.0, ry2=40.0,
            id1="pet-1", id2="pet-2",
        )
        assert c2 is True
        assert ov2 == pytest.approx(50.0, abs=1e-3)

        # 扁宽椭圆在 y 轴方向距离 70px (ry1+ry2=80, 应该碰撞)
        c2_y, _, _, ov2_y, _, _ = collision.check_collision_ellipse(
            x1=0.0, y1=0.0, rx1=100.0, ry1=40.0,
            x2=0.0, y2=70.0, rx2=100.0, ry2=40.0,
            id1="pet-1", id2="pet-2",
        )
        assert c2_y is True
        assert ov2_y == pytest.approx(10.0, abs=1e-3)

        # 3. 高瘦椭圆 (rx=30, ry=120)
        c3, _, _, ov3, _, _ = collision.check_collision_ellipse(
            x1=0.0, y1=0.0, rx1=30.0, ry1=120.0,
            x2=0.0, y2=200.0, rx2=30.0, ry2=120.0,
            id1="pet-1", id2="pet-2",
        )
        assert c3 is True
        assert ov3 == pytest.approx(40.0, abs=1e-3)

        # 高瘦椭圆在 x 轴方向距离 70px (rx1+rx2=60, 应该不碰撞，Broad-phase 排除)
        c3_x, _, _, _, _, _ = collision.check_collision_ellipse(
            x1=0.0, y1=0.0, rx1=30.0, ry1=120.0,
            x2=70.0, y2=0.0, rx2=30.0, ry2=120.0,
            id1="pet-1", id2="pet-2",
        )
        assert c3_x is False

    def test_concentric_centers_uses_stable_hash_direction(self):
        """测试重合中心：完全重合时使用稳定哈希方向，绝不随机且对称。"""
        id_a = "slot-0-pid100-alpha"
        id_b = "slot-1-pid200-beta"

        # 顺向检测
        c1, nx1, ny1, ov1, _, _ = collision.check_collision_ellipse(
            x1=100.0, y1=100.0, rx1=50.0, ry1=50.0,
            x2=100.0, y2=100.0, rx2=50.0, ry2=50.0,
            id1=id_a, id2=id_b,
        )
        # 反向检测
        c2, nx2, ny2, ov2, _, _ = collision.check_collision_ellipse(
            x1=100.0, y1=100.0, rx1=50.0, ry1=50.0,
            x2=100.0, y2=100.0, rx2=50.0, ry2=50.0,
            id1=id_b, id2=id_a,
        )

        assert c1 is True and c2 is True
        assert ov1 > 0 and ov2 > 0
        # 向量长度为 1
        assert math.hypot(nx1, ny1) == pytest.approx(1.0, abs=1e-5)
        assert math.hypot(nx2, ny2) == pytest.approx(1.0, abs=1e-5)
        # 严格互为反向
        assert nx1 == pytest.approx(-nx2, abs=1e-5)
        assert ny1 == pytest.approx(-ny2, abs=1e-5)

        # 多次调用结果完全一致（确定性，非随机）
        dir_a1, dir_a2 = collision.stable_hash_direction(id_a, id_b)
        dir_b1, dir_b2 = collision.stable_hash_direction(id_a, id_b)
        assert dir_a1 == dir_b1 and dir_a2 == dir_b2


class TestCollisionCircleChain:
    def test_circle_chain_horizontal_vertical_and_square(self):
        assert collision.circles_from_rect(10, 20, 100, 40) == [
            [30.0, 40.0, 20.0], [60.0, 40.0, 20.0], [90.0, 40.0, 20.0]]
        assert collision.circles_from_rect(10, 20, 40, 100) == [
            [30.0, 40.0, 20.0], [30.0, 70.0, 20.0], [30.0, 100.0, 20.0]]
        assert collision.circles_from_rect(10, 20, 50, 50) == [
            [35.0, 45.0, 25.0], [35.0, 45.0, 25.0], [35.0, 45.0, 25.0]]

    def test_circle_pair_tangent_one_pixel_overlap_and_separation(self):
        a = [[0.0, 0.0, 10.0]]
        b = [[20.0, 0.0, 10.0]]
        assert collision.check_collision_circles(a, b)[0] is False
        b[0][0] = 19.0
        result = collision.check_collision_circles(a, b)
        assert result[0] is True and result[3] == pytest.approx(1.0)
        b[0][0] = 21.0
        assert collision.check_collision_circles(a, b)[0] is False

    def test_diagonal_chain_collision_can_trigger_when_ellipse_does_not(self):
        circles_a = collision.circles_from_rect(-50, -20, 100, 40)
        circles_b = collision.circles_from_rect(35, 5, 40, 100)
        assert collision.check_collision_ellipse(0, 0, 50, 20, 55, 55, 20, 50)[0] is False
        assert collision.check_collision_circles(circles_a, circles_b)[0] is True

    def test_member_without_circles_uses_ellipse_fallback(self):
        a = collision.MemberState("a", 0, 0, 50, 20)
        b = collision.MemberState("b", 55, 55, 20, 50)
        assert collision.check_collision_members(a, b)[0] is False

    def test_swept_circle_chain_detects_fast_crossing(self):
        result = collision.swept_circle_chain_collision(
            [[0.0, 0.0, 30.0]], [[450.0, 0.0, 30.0]],
            [[225.0, 0.0, 30.0]], [[225.0, 0.0, 30.0]],
        )
        assert result[0] is True
        # TOI 语义：首次接触时 A 圆心在 225 − (30+30) = 165
        assert result[4] == pytest.approx(165.0)
        assert result[5] == pytest.approx(0.0)

    def test_swept_circle_chain_low_speed_non_crossing_does_not_trigger(self):
        result = collision.swept_circle_chain_collision(
            [[0.0, 0.0, 10.0]], [[5.0, 0.0, 10.0]],
            [[100.0, 0.0, 10.0]], [[100.0, 0.0, 10.0]],
        )
        assert result[0] is False

    def test_swept_circle_chain_tangent_path_triggers(self):
        result = collision.swept_circle_chain_collision(
            [[0.0, 0.0, 10.0]], [[50.0, 0.0, 10.0]],
            [[30.0, 20.0, 10.0]], [[30.0, 20.0, 10.0]],
        )
        assert result[0] is True
        # 相切也判定接触；扫掠接触的 overlap 固定为小正值 1.0
        assert result[3] == pytest.approx(1.0)
        assert result[4] == pytest.approx(30.0)

    def test_swept_circle_chain_receding_path_does_not_trigger(self):
        result = collision.swept_circle_chain_collision(
            [[0.0, 0.0, 10.0]], [[-100.0, 0.0, 10.0]],
            [[30.0, 0.0, 10.0]], [[30.0, 0.0, 10.0]],
        )
        assert result[0] is False


class TestCollisionMassCalculation:
    """质量计算规则与 fallback 测试。"""

    def test_calculate_mass_with_base_and_clamping(self):
        """有基准半轴时的面积比加权与 clamp 0.7~1.6（实机手感收窄）。"""
        # 基准: 100 * 80 = 8000
        # 当前: 100 * 80 = 8000 -> mass = 1.0
        m1 = collision.calculate_mass(100.0, 80.0, base_radius_x=100.0, base_radius_y=80.0)
        assert m1 == pytest.approx(1.0, abs=1e-5)

        # 极小面积：20 * 20 / 8000 = 0.05 -> clamp 0.7
        m_small = collision.calculate_mass(20.0, 20.0, base_radius_x=100.0, base_radius_y=80.0)
        assert m_small == 0.7

        # 极大面积：400 * 300 / 8000 = 15.0 -> clamp 1.6
        m_large = collision.calculate_mass(400.0, 300.0, base_radius_x=100.0, base_radius_y=80.0)
        assert m_large == 1.6

    def test_calculate_mass_scale_squared_fallback(self):
        """无基准半轴时按 scale^2 估算（clamp 0.7~1.6）。"""
        # scale = 0.72 (基准) -> 1.0
        m_base = collision.calculate_mass(50.0, 50.0, scale=0.72)
        assert m_base == pytest.approx(1.0, abs=1e-5)

        # scale = 1.44 (2倍) -> 2^2 = 4.0 -> clamp 1.6
        m_big = collision.calculate_mass(50.0, 50.0, scale=1.44)
        assert m_big == 1.6

        # scale = 0.36 (0.5倍) -> 0.5^2 = 0.25 -> clamp 0.7
        m_tiny = collision.calculate_mass(50.0, 50.0, scale=0.36)
        assert m_tiny == 0.7


class TestCollisionImpulseAndMomentum:
    """冲量求解、3 种相对速度方向、动量守恒及无限质量测试。"""

    def test_three_velocity_directions(self):
        """测试 3 种速度方向：正碰、斜碰擦边、相背分离。"""
        # 1. 正碰 (Head-on)
        m1 = collision.MemberState(runtime_id="p1", x=0.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=200.0, vy=0.0, mass=1.0)
        m2 = collision.MemberState(runtime_id="p2", x=80.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=-200.0, vy=0.0, mass=1.0)
        nx, ny = 1.0, 0.0  # p1 指向 p2
        j1, dvx_a1, dvy_a1, dvx_b1, dvy_b1 = collision.solve_collision_impulse(m1, m2, nx, ny, restitution=0.82)
        assert j1 > 0.0
        assert dvx_a1 < 0.0  # p1 向左反弹
        assert dvx_b1 > 0.0  # p2 向右反弹

        # 2. 斜碰擦边 (Glancing with friction)
        m3 = collision.MemberState(runtime_id="p3", x=0.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=200.0, vy=150.0, mass=1.0)
        m4 = collision.MemberState(runtime_id="p4", x=80.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=-200.0, vy=-50.0, mass=1.0)
        j2, dvx_a2, dvy_a2, dvx_b2, dvy_b2 = collision.solve_collision_impulse(m3, m4, nx, ny, restitution=0.82, friction=0.08)
        assert j2 > 0.0
        assert dvy_a2 != 0.0  # 摩擦导致 y 轴速度产生反向调整
        assert dvy_b2 != 0.0

        # 3. 相背分离 (Moving apart) -> 不施加冲量
        m5 = collision.MemberState(runtime_id="p5", x=0.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=-200.0, vy=0.0, mass=1.0)
        m6 = collision.MemberState(runtime_id="p6", x=80.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=200.0, vy=0.0, mass=1.0)
        j3, dvx_a3, dvy_a3, dvx_b3, dvy_b3 = collision.solve_collision_impulse(m5, m6, nx, ny, restitution=0.82)
        assert j3 == 0.0
        assert dvx_a3 == 0.0 and dvy_a3 == 0.0
        assert dvx_b3 == 0.0 and dvy_b3 == 0.0

    def test_momentum_conservation_error_less_than_1e_6(self):
        """验证弹性+摩擦碰撞下系统的总动量严格守恒（误差 < 1e-6）。"""
        m_a_val = 1.25
        m_b_val = 2.40
        m1 = collision.MemberState(runtime_id="p1", x=0.0, y=0.0, radius_x=60.0, radius_y=40.0, vx=350.0, vy=-120.0, mass=m_a_val)
        m2 = collision.MemberState(runtime_id="p2", x=70.0, y=30.0, radius_x=50.0, radius_y=50.0, vx=-180.0, vy=260.0, mass=m_b_val)

        # 计算法线
        dist = math.hypot(70.0, 30.0)
        nx = 70.0 / dist
        ny = 30.0 / dist

        # 碰撞前动量
        p_x_before = m1.mass * m1.vx + m2.mass * m2.vx
        p_y_before = m1.mass * m1.vy + m2.mass * m2.vy

        j, dvx_a, dvy_a, dvx_b, dvy_b = collision.solve_collision_impulse(
            m1, m2, nx, ny, restitution=0.82, friction=0.08,
        )
        assert j > 0

        # 碰撞后速度
        v1_x_after = m1.vx + dvx_a
        v1_y_after = m1.vy + dvy_a
        v2_x_after = m2.vx + dvx_b
        v2_y_after = m2.vy + dvy_b

        # 碰撞后动量
        p_x_after = m1.mass * v1_x_after + m2.mass * v2_x_after
        p_y_after = m1.mass * v1_y_after + m2.mass * v2_y_after

        assert abs(p_x_after - p_x_before) < 1e-6
        assert abs(p_y_after - p_y_before) < 1e-6

    def test_zero_and_infinite_mass_interactions(self):
        """测试零质量与无限质量（如拖拽、锁定状态）。"""
        # Case A: 一个无限质量 (如被锁定的桌宠)，一个普通质量
        m_fixed = collision.MemberState(
            runtime_id="fixed", x=100.0, y=0.0, radius_x=50.0, radius_y=50.0,
            vx=0.0, vy=0.0, mass=1.0, is_infinite_mass=True, flags=collision.FLAG_LOCK_POSITION,
        )
        m_dynamic = collision.MemberState(
            runtime_id="dyn", x=20.0, y=0.0, radius_x=50.0, radius_y=50.0,
            vx=400.0, vy=0.0, mass=1.5,
        )

        _, dvx_dyn, dvy_dyn, dvx_fix, dvy_fix = collision.solve_collision_impulse(
            m_dynamic, m_fixed, 1.0, 0.0, restitution=0.82,
        )
        # 固定体速度不受任何影响
        assert dvx_fix == 0.0 and dvy_fix == 0.0
        # 动态体完全吸收反弹
        assert dvx_dyn == pytest.approx(-400.0 * (1.0 + 0.82), abs=1e-3)

        # Case B: 两个都无限质量 -> 无速度冲量
        m_fixed2 = collision.MemberState(
            runtime_id="fixed2", x=60.0, y=0.0, radius_x=50.0, radius_y=50.0,
            vx=100.0, vy=0.0, is_infinite_mass=True,
        )
        j, dvx_a, dvy_a, dvx_b, dvy_b = collision.solve_collision_impulse(
            m_fixed, m_fixed2, 1.0, 0.0,
        )
        assert j == 0.0
        assert dvx_a == 0.0 and dvx_b == 0.0

    def test_minimum_approach_speed_suppresses_low_speed_impulse(self):
        """低于 80px/s 的接近速度不产生速度冲量。"""
        slow_a = collision.MemberState(
            runtime_id="slow-a", x=0.0, y=0.0, radius_x=50.0, radius_y=50.0,
            vx=0.0, vy=0.0,
        )
        slow_b = collision.MemberState(
            runtime_id="slow-b", x=80.0, y=0.0, radius_x=50.0, radius_y=50.0,
            vx=-50.0, vy=0.0,
        )
        result = collision.solve_collision_impulse(slow_a, slow_b, 1.0, 0.0)
        assert result == (0.0, 0.0, 0.0, 0.0, 0.0)

        fast_b = collision.MemberState(
            runtime_id="fast-b", x=80.0, y=0.0, radius_x=50.0, radius_y=50.0,
            vx=-500.0, vy=0.0,
        )
        j, dvx_a, dvy_a, dvx_b, dvy_b = collision.solve_collision_impulse(
            slow_a, fast_b, 1.0, 0.0,
        )
        assert j > 0.0
        assert dvx_a < 0.0 and dvx_b > 0.0
        assert dvy_a == 0.0 and dvy_b == 0.0


class TestPositionSeparationAndMultiBody:
    """位置分离算法、多体/三体碰撞与迭代分离测试。"""

    def test_position_separation_clamping_and_slop(self):
        """测试 60% 分离、min 1px / max 12px、0.5px slop 容差。"""
        inv_ma = 1.0
        inv_mb = 1.0

        # 重叠小于等于 slop (0.5px) -> 不分离
        sep0, dxa0, _, _, _ = collision.calculate_position_separation(0.4, 1.0, 0.0, inv_ma, inv_mb)
        assert sep0 == 0.0 and dxa0 == 0.0

        # 重叠 1.5px: eff_overlap = 1.0, target = 0.6 -> 下限 1.0px
        sep1, dxa1, _, dxb1, _ = collision.calculate_position_separation(1.5, 1.0, 0.0, inv_ma, inv_mb)
        assert sep1 == 1.0
        assert dxa1 == -0.5 and dxb1 == 0.5

        # 重叠 10.5px: eff_overlap = 10.0, target = 6.0 -> 6.0px
        sep2, dxa2, _, dxb2, _ = collision.calculate_position_separation(10.5, 1.0, 0.0, inv_ma, inv_mb)
        assert sep2 == 6.0
        assert dxa2 == -3.0 and dxb2 == 3.0

        # 重叠 40.5px: eff_overlap = 40.0, target = 24.0 -> 上限 12.0px
        sep3, dxa3, _, dxb3, _ = collision.calculate_position_separation(40.5, 1.0, 0.0, inv_ma, inv_mb)
        assert sep3 == 12.0
        assert dxa3 == -6.0 and dxb3 == 6.0

        # 连续 3 tick 强制完整分离 (force_full=True)
        sep_full, dxa_f, _, dxb_f, _ = collision.calculate_position_separation(
            40.0, 1.0, 0.0, inv_ma, inv_mb, force_full=True,
        )
        assert sep_full == 40.0
        assert dxa_f == -20.0 and dxb_f == 20.0

    def test_multi_body_three_bodies_collision(self):
        """测试三体同时重叠：向量合并与迭代分离后跨 tick 收敛（plan4 §6.4：
        单 tick 最多 4 轮迭代，残余重叠由后续 tick 继续修正，绝不死循环）。"""
        # 构造三只桌宠紧密排列在一条线上 (A, B, C)
        # 半径均为 50px，A 在 x=0, B 在 x=70 (与 A 重叠 30), C 在 x=140 (与 B 重叠 30)
        mA = collision.MemberState(runtime_id="A", x=0.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=100.0, vy=0.0)
        mB = collision.MemberState(runtime_id="B", x=70.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=0.0, vy=0.0)
        mC = collision.MemberState(runtime_id="C", x=140.0, y=0.0, radius_x=50.0, radius_y=50.0, vx=-100.0, vy=0.0)

        # 连续重叠历史，让其触发 force_full 以彻底推开
        history = {"A|B": 3, "B|C": 3}

        impulses, combined, new_history = collision.solve_multi_body_collision(
            [mA, mB, mC], tick=1, overlap_history=history, max_separation_iterations=4,
        )

        assert len(impulses) == 2  # A|B 和 B|C 两个 pair
        assert "A" in combined and "B" in combined and "C" in combined

        # 中间体 B 被两侧对称拉扯时单 tick 4 轮迭代只能几何级收敛（残余可非零），
        # 按 plan4 约定跨 tick 继续修正：模拟后续 tick 直至收敛，验证不死循环且最终 < 0.5px
        members = {"A": mA, "B": mB, "C": mC}
        pos = {k: [m.x + combined[k][2], m.y + combined[k][3]] for k, m in members.items()}

        def overlap_of(id1, id2):
            _, _, _, ov, _, _ = collision.check_collision_ellipse(
                pos[id1][0], pos[id1][1], members[id1].radius_x, members[id1].radius_y,
                pos[id2][0], pos[id2][1], members[id2].radius_x, members[id2].radius_y,
                id1=id1, id2=id2,
            )
            return ov

        # 首 tick 后必须有实质进展（重叠显著下降）
        assert overlap_of("A", "B") < 30.0
        assert overlap_of("B", "C") < 30.0

        # 模拟后续 tick：速度清零只测分离收敛（静止堆叠场景）
        for tick in range(2, 9):
            cur = [
                collision.MemberState(runtime_id=k, x=pos[k][0], y=pos[k][1],
                                      radius_x=members[k].radius_x, radius_y=members[k].radius_y,
                                      vx=0.0, vy=0.0)
                for k in ("A", "B", "C")
            ]
            _, combined, history = collision.solve_multi_body_collision(
                cur, tick=tick, overlap_history=history, max_separation_iterations=4,
            )
            for k in ("A", "B", "C"):
                pos[k][0] += combined[k][2]
                pos[k][1] += combined[k][3]
            if overlap_of("A", "B") < 0.5 and overlap_of("B", "C") < 0.5:
                break

        assert overlap_of("A", "B") < 0.5
        assert overlap_of("B", "C") < 0.5

    def test_force_full_clears_normal_approach_velocity(self):
        """连续重叠第 3 tick（force_full）：完整分离并把法向接近速度置零，不再反弹抖动。

        plan4 §4.2：连续 3 个 tick 仍重叠时强制完整分离一次并把法向接近速度置零。
        只清相向分量、不动切向：分离后下一 tick 不再产生反弹冲量。
        """
        m1 = collision.MemberState(runtime_id="A", x=0.0, y=0.0, radius_x=50.0, radius_y=50.0,
                                   vx=80.0, vy=30.0, mass=1.0)
        m2 = collision.MemberState(runtime_id="B", x=60.0, y=0.0, radius_x=50.0, radius_y=50.0,
                                   vx=-80.0, vy=-10.0, mass=1.0)
        history = {"A|B": 2}  # 前两 tick 已连续重叠，本次为第 3 tick
        impulses, combined, new_history = collision.solve_multi_body_collision(
            [m1, m2], tick=3, overlap_history=history, restitution=0.82,
        )

        assert new_history["A|B"] == 3
        assert len(impulses) == 1
        res = impulses[0]
        # force_full：40px 重叠一次完整修正（无 min/max 截断）
        assert res.sep == pytest.approx(40.0, abs=1e-6)
        # 法向接近速度置零：施加冲量后相对法向速度 == 0
        # （若走正常恢复系数 0.82 会反弹为 vn_after ≈ +131.2）
        vn_after = ((m2.vx + res.dvx_b) - (m1.vx + res.dvx_a)) * res.nx + \
                   ((m2.vy + res.dvy_b) - (m1.vy + res.dvy_a)) * res.ny
        assert vn_after == pytest.approx(0.0, abs=1e-6)
        # 只清相向分量：切向速度分量不被改动（dvy 增量为 0）
        assert res.dvy_a == pytest.approx(0.0, abs=1e-9)
        assert res.dvy_b == pytest.approx(0.0, abs=1e-9)
        # 分离后中心距 == 半径和，下一 tick 不再碰撞/产生冲量（不反弹抖动）
        pos_a = m1.x + combined["A"][2]
        pos_b = m2.x + combined["B"][2]
        assert pos_b - pos_a == pytest.approx(100.0, abs=1e-6)
        settled_a = collision.MemberState(runtime_id="A", x=pos_a, y=0.0,
                                          radius_x=50.0, radius_y=50.0,
                                          vx=0.0, vy=30.0, mass=1.0)
        settled_b = collision.MemberState(runtime_id="B", x=pos_b, y=0.0,
                                          radius_x=50.0, radius_y=50.0,
                                          vx=0.0, vy=-10.0, mass=1.0)
        impulses4, _, _ = collision.solve_multi_body_collision(
            [settled_a, settled_b], tick=4, overlap_history=new_history,
        )
        assert impulses4 == []

    def test_persistent_low_speed_contact_only_separates(self):
        """低速重叠连续 10 tick 只分离，不产生反弹速度冲量。"""
        a_x, b_x = 0.0, 80.0
        history = {}
        impulse_count = 0
        for tick in range(10):
            a = collision.MemberState(
                runtime_id="A", x=a_x, y=0.0, radius_x=50.0, radius_y=50.0,
                vx=0.0, vy=0.0,
            )
            b = collision.MemberState(
                runtime_id="B", x=b_x, y=0.0, radius_x=50.0, radius_y=50.0,
                vx=-1.0, vy=0.0,
            )
            impulses, combined, history = collision.solve_multi_body_collision(
                [a, b], tick=tick, overlap_history=history,
            )
            impulse_count += sum(1 for item in impulses if item.j > 0.0)
            a_x += combined["A"][2]
            b_x += combined["B"][2]

        assert impulse_count == 0
        assert collision.check_collision_ellipse(
            a_x, 0.0, 50.0, 50.0, b_x, 0.0, 50.0, 50.0, "A", "B",
        )[3] < 0.5


class TestPhysicsSoftClampSpeedIntegration:
    """验证冲量加到速度后与 physics.soft_clamp_speed 的总速度钳制集成。"""

    def test_combined_speed_9000_plus_9000_does_not_exceed_cap(self):
        """输入极限分量 9000 + 9000，经 soft_clamp_speed 钳制后严格不超过 cap。"""
        # 测试所有力度档位
        for strength, cap in physics.THROW_STRENGTH_CAPS.items():
            # 客户端初始速度 vx=9000, vy=9000，又收到 impulse 增量 dvx=9000, dvy=9000
            init_vx, init_vy = 9000.0, 9000.0
            dvx, dvy = 9000.0, 9000.0

            raw_vx = init_vx + dvx
            raw_vy = init_vy + dvy
            raw_speed = math.hypot(raw_vx, raw_vy)
            assert raw_speed > 25000.0  # 极大速度

            # 客户端应用 soft_clamp_speed
            clamped_speed = physics.soft_clamp_speed(raw_speed, cap=cap)
            assert clamped_speed <= cap
            # 软膝曲线渐近保证严格小于 cap
            assert clamped_speed < cap
            # 输入约 2.8~7 倍 cap（随档位不同）时软膝输出应已吃满 90% 以上
            assert clamped_speed > cap * 0.9

            # 还原分量
            ratio = clamped_speed / raw_speed
            final_vx = raw_vx * ratio
            final_vy = raw_vy * ratio
            final_speed = math.hypot(final_vx, final_vy)

            assert final_speed == pytest.approx(clamped_speed, abs=1e-5)
            assert final_speed < cap


class TestProtocolFrameEncodingAndDecoding:
    """协议帧（4 字节大端长度前缀、4096 上限、粘包/半包、坏 JSON/空帧）测试。"""

    def test_encode_and_decode_normal_frame(self):
        """正常帧编解码 round-trip。"""
        msg = {
            "type": "state",
            "seq": 42,
            "runtime_id": "slot-0-pid123-abc",
            "x": 120.5,
            "y": 300.0,
            "w": 461,
            "h": 281,
            "radius_x": 115.0,
            "radius_y": 82.0,
            "vx": 10.0,
            "vy": -5.0,
            "character": "shenshen",
            "scale": 0.72,
            "flags": 1,
        }
        frame_bytes = collision.encode_frame(msg)
        assert len(frame_bytes) > 4
        # 头部 4 字节为载荷长度
        payload_len = int.from_bytes(frame_bytes[:4], byteorder="big")
        assert payload_len == len(frame_bytes) - 4

        decoder = collision.FrameStreamDecoder()
        results = decoder.feed(frame_bytes)
        assert len(results) == 1
        assert results[0] == msg

    def test_sticky_and_partial_frames(self):
        """测试粘包（多帧合并）与半包（分片到达）。"""
        msg1 = {"type": "hello", "runtime_id": "p1"}
        msg2 = {"type": "leave", "runtime_id": "p1"}

        b1 = collision.encode_frame(msg1)
        b2 = collision.encode_frame(msg2)
        combined = b1 + b2

        decoder = collision.FrameStreamDecoder()

        # 分 3 个任意切片喂入
        chunk1 = combined[:10]
        chunk2 = combined[10:35]
        chunk3 = combined[35:]

        res1 = decoder.feed(chunk1)
        assert len(res1) == 0  # 尚未形成完整帧

        res2 = decoder.feed(chunk2)  # 可能解析出第 1 帧
        res3 = decoder.feed(chunk3)  # 解析出剩余帧

        all_res = res1 + res2 + res3
        assert len(all_res) == 2
        assert all_res[0] == msg1
        assert all_res[1] == msg2

    def test_empty_frame_corrupted_json_and_oversized_frame_no_exception(self):
        """坏 JSON、空帧、超长帧返回 DecodeError 且不抛未捕获异常。"""
        decoder = collision.FrameStreamDecoder(max_frame_len=4096)

        # 1. 空帧 (长度 0)
        empty_header = (0).to_bytes(4, byteorder="big")
        res_empty = decoder.feed(empty_header)
        assert len(res_empty) == 1
        assert isinstance(res_empty[0], collision.DecodeError)
        assert "Empty frame" in res_empty[0].reason

        # 2. 坏 JSON 载荷
        bad_json_payload = b"this is not a valid json {"
        bad_frame = len(bad_json_payload).to_bytes(4, byteorder="big") + bad_json_payload
        res_bad = decoder.feed(bad_frame)
        assert len(res_bad) == 1
        assert isinstance(res_bad[0], collision.DecodeError)
        assert "JSON decode error" in res_bad[0].reason

        # 3. 超长帧 (> 4096 字节)
        oversized_len = 5000
        oversized_header = oversized_len.to_bytes(4, byteorder="big") + b"x" * 20
        res_over = decoder.feed(oversized_header)
        assert len(res_over) == 1
        assert isinstance(res_over[0], collision.DecodeError)
        assert "exceeds limit" in res_over[0].reason


class TestWatermarkDeduplication:
    """水位去重器（重复 tick 拒绝、低于水位拒绝、epoch 切换整体替换）测试。"""

    def test_watermark_deduplication_lifecycle(self):
        """测试同一个 epoch 下单调递增，以及新 epoch 的重置。"""
        dedup = collision.WatermarkDeduplicator()

        epoch1 = "epoch-20260830-001"
        pair_ab = "petA|petB"
        pair_bc = "petB|petC"

        # 首次接收 tick 10 -> 允许
        assert dedup.should_apply(epoch1, pair_ab, 10) is True
        # 重复 tick 10 -> 拒绝
        assert dedup.should_apply(epoch1, pair_ab, 10) is False
        # 滞后 tick 8 -> 拒绝
        assert dedup.should_apply(epoch1, pair_ab, 8) is False
        # 新 tick 12 -> 允许
        assert dedup.should_apply(epoch1, pair_ab, 12) is True

        # 另一个 pair 独立计数
        assert dedup.should_apply(epoch1, pair_bc, 5) is True
        assert dedup.should_apply(epoch1, pair_bc, 5) is False

        # Epoch 切换：换为 epoch2，即使 tick 变小（如协调者重选 tick 从 1 开始），也必须被接受并重置水位
        epoch2 = "epoch-20260830-002"
        assert dedup.should_apply(epoch2, pair_ab, 1) is True
        # 旧 epoch 的残余事件到达 -> 切换并记录新 epoch
        assert dedup.current_epoch == epoch2
        assert dedup.should_apply(epoch2, pair_ab, 1) is False
        assert dedup.should_apply(epoch2, pair_ab, 2) is True
