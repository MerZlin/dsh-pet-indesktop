# -*- coding: utf-8 -*-
"""
动画目录（catalog）—— 全部动画名、文件映射、分类与几何常量的"事实来源"。

素材来源：dsh-pet 插件（https://github.com/PC2005-cloud/dsh-pet）的
assets/thumb/*.webm（640×360 透明 webm，VP9 alpha）。

几何常量与原插件 client.js 完全一致：
- 画布 640×360，人物脚底 y=330
- 落地偏移 PAD = 360 - 330 = 30px（绘制时把帧下移 PAD，让脚踩在窗口底线）
"""

from pathlib import Path

# ---------------------------------------------------------------- 画布几何
# webm 尺寸（16:9，640×360 高清素材）
CANVAS_W = 640
CANVAS_H = 360

# 脚底在画布内的 y 坐标（与母版一致：640×360 画布脚底 y=330）
FEET_Y = 330 / 360 * CANVAS_H  # = 330

# 落地偏移：帧下移多少让脚底恰好落在窗口底线
PAD = CANVAS_H - FEET_Y        # = 30

# 视频帧时长（毫秒）—— 24fps → 40ms，用于时长/移动插值换算
FRAME_MS = 40

# 动画链概率（与 client.js 一致）：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动
P_IDLE = 0.30
P_TURN = 0.40  # 累计阈值：<0.3 待机，<0.4 转向
P_ACTS = 0.80  # 累计阈值：<0.8 动作，>=0.8 移动

# 移动参数（与 client.js 一致）
MOVE_MIN_PX = 60
MOVE_MAX_PX = 240
MOVE_MARGIN = 20    # 屏幕边缘安全边距
MOVE_LEAD_SEC = 2   # 动画开头 2s 准备动作，位置不动
MOVE_TAIL_SEC = 2   # 动画结尾 2s 收尾动作，位置不动

# 拖拽判定阈值（像素，缩放前逻辑像素）
DRAG_THRESHOLD = 5

# 默认显示缩放与右下角边距
# 目标显示宽度 ≈ 462px（与 DSH web 端一致）→ 462 / 640 ≈ 0.72
DEFAULT_SCALE = 0.72
CORNER_MARGIN = 24  # 距屏幕右缘的默认间距

# 可选的显示缩放档位（相对 640 宽：320px / 462px / 544px / 640px）
SCALE_STEPS = (0.5, 0.72, 0.85, 1.0)

# ---------------------------------------------------------------- 多形象
# 当前内置形象与未来扩展形象 ID（目录名建议使用稳定 ASCII）
DEFAULT_CHARACTER = 'shenshen'
CHARACTERS = ('shenshen', 'guga', 'dada', 'suansuan', 'dudu', 'mimi')


# ---------------------------------------------------------------- 动画映射
# 中文名 → webm 文件名（主路径，文件名与中文名一致）
ANIM_FILES: dict[str, str] = {
    '待机呼吸休闲': '待机呼吸休闲.webm',
    '东张西望': '东张西望.webm',
    '螃蟹走路': '螃蟹走路.webm',
    '原地漂浮踏步': '原地漂浮踏步.webm',
    '原地左转奔跑': '原地左转奔跑.webm',
    '点击回应 - 开心跃动': '点击回应 - 开心跃动.webm',
    '点击回应 - 害羞惊讶': '点击回应 - 害羞惊讶.webm',
    '点击回应 - 傲娇生气（侧身展示）': '点击回应 - 傲娇生气（侧身展示）.webm',
    '被鼠标拖拽悬空反馈': '被鼠标拖拽悬空反馈.webm',
    '悠闲哼歌': '悠闲哼歌.webm',
    '超大伸懒腰': '超大伸懒腰.webm',
    '原地专心玩魔方': '原地专心玩魔方.webm',
    '原地敲击桌面互动': '原地敲击桌面互动.webm',
    '原地重力下蹲压缩': '原地重力下蹲压缩.webm',
    '哈欠连天': '哈欠连天.webm',
    '原地小憩沉眠': '原地小憩沉眠.webm',
    '原地蹲下玩玩具汽车': '原地蹲下玩玩具汽车.webm',
    '鲸鱼吐泡泡特效': '鲸鱼吐泡泡特效.webm',
    '女仆屈膝礼仪': '女仆屈膝礼仪.webm',
    '被吓一跳（炸毛）': '被吓一跳（炸毛）.webm',
    '原地跳跃抓碎头顶物品': '原地跳跃抓碎头顶物品.webm',
    '小幅度原地 360 度旋转展示': '小幅度原地 360 度旋转展示.webm',
    '偷吃零食被抓住': '偷吃零食被抓住.webm',
    '玩游戏气急败坏': '玩游戏气急败坏.webm',
    '用鲸鱼尾巴拍打地面': '用鲸鱼尾巴拍打地面.webm',
    '打瞌睡被惊醒': '打瞌睡被惊醒.webm',
    '玩水枪': '玩水枪.webm',
    '小提琴演奏': '小提琴演奏.webm',
    '蓝鲸现世': '蓝鲸现世.webm',
    '吃白饭': '吃白饭.webm',
    '照镜子': '照镜子.webm',
    '优雅女仆舞': '优雅女仆舞.webm',
    '轻快摇摆舞': '轻快摇摆舞.webm',
    '可爱宅舞': '可爱宅舞.webm',
    '整体换装试色': '整体换装试色.webm',
    '大口吃零食': '大口吃零食.webm',
    '吹气球': '吹气球.webm',
    '动物环绕': '动物环绕.webm',
    '深度思考碎碎念': '深度思考碎碎念.webm',
    '轻快记录': '轻快记录.webm',
    '写代码': '写代码.webm',
    '吃Token': '吃Token.webm',
    '吃早餐': '吃早餐.webm',
    '吃午餐': '吃午餐.webm',
    '吃晚餐': '吃晚餐.webm',
    '放风筝': '放风筝.webm',
    '摇扇纳凉': '摇扇纳凉.webm',
    '吃冰淇淋融化': '吃冰淇淋融化.webm',
    '被落叶淹没': '被落叶淹没.webm',
    '中秋赏月吃月饼': '中秋赏月吃月饼.webm',
    '堆雪人': '堆雪人.webm',
}

# 兼容旧字段名：webm 文件名映射
WEBM_FILES: dict[str, str] = ANIM_FILES

# 动画分组（语义与 client.js 一致）
IDLE = '待机呼吸休闲'
TURN = '东张西望'
MOVES = ['螃蟹走路', '原地漂浮踏步', '原地左转奔跑']
CLICKS = ['点击回应 - 开心跃动', '点击回应 - 害羞惊讶', '点击回应 - 傲娇生气（侧身展示）']
DRAG = '被鼠标拖拽悬空反馈'
ACTS = [n for n in ANIM_FILES if n not in (IDLE, TURN, DRAG, *MOVES, *CLICKS)]

assert len(ANIM_FILES) == 51, f"动画总数应为 51，实际 {len(ANIM_FILES)}"
assert len(ACTS) == 42, f"动作池应为 42，实际 {len(ACTS)}"


def assets_dir() -> Path:
    """兼容旧调用：默认形象 shenshen 的 webm 素材目录。"""
    return webm_dir()


def characters_dir() -> Path:
    """内置多形象根目录（项目根/assets/characters）。"""
    return Path(__file__).resolve().parent.parent / 'assets' / 'characters'


def character_video_dir(character_id: str) -> Path:
    """内置某个形象的 webm 目录：assets/characters/<id>/videos。"""
    return characters_dir() / character_id / 'videos'


def resolve_character_video_dir(character_id: str) -> Path:
    """返回形象视频目录（全部随 exe 打包，内置 assets/characters）。"""
    return character_video_dir(character_id)


def webm_dir() -> Path:
    """默认形象 shenshen 的 webm 素材目录（兼容旧调用）。"""
    return character_video_dir(DEFAULT_CHARACTER)


def legacy_assets_dir() -> Path:
    """兼容旧名称：默认形象 webm 素材目录。"""
    return webm_dir()


def build_categories(names) -> dict:
    """根据某个形象实际拥有的动画名，动态计算分类。

    这样不同形象可以有不同动作集：
    - 核心动画按已知名称优先识别；
    - 不在核心分类里的动画自动归入“随机动作池”。
    """
    names = set(names)
    idle = IDLE if IDLE in names else (next(iter(names), None) if names else None)
    turn = TURN if TURN in names else None
    moves = [n for n in MOVES if n in names]
    clicks = [n for n in CLICKS if n in names]
    drag = DRAG if DRAG in names else None
    core = {idle, turn, drag, *moves, *clicks}
    core.discard(None)
    acts = [n for n in names if n not in core]
    return {
        'idle': idle,
        'turn': turn,
        'moves': moves,
        'clicks': clicks,
        'drag': drag,
        'acts': acts,
    }


def resolve_asset_path(name: str, filename: str, base_dir: Path | None = None) -> Path:
    """解析 webm 素材路径；不存在时返回预期路径以便上层报错。"""
    base_dir = Path(base_dir) if base_dir is not None else webm_dir()
    path = base_dir / WEBM_FILES.get(name, filename)
    return path
