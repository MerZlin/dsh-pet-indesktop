# -*- coding: utf-8 -*-
"""
动画目录（catalog）—— 全部动画名、文件映射、分类与几何常量的"事实来源"。

素材来源：dsh-pet 插件（https://github.com/PC2005-cloud/dsh-pet）的
assets/thumb/*.webm（640×360 透明 webm，VP9 alpha）。

几何常量与原插件 client.js 完全一致：
- 画布 640×360，人物脚底 y=330
- 落地偏移 PAD = 360 - 330 = 30px（绘制时把帧下移 PAD，让脚踩在窗口底线）
"""

import os
import sys

import json

from pathlib import Path

# ---------------------------------------------------------------- 画布几何
# webm 尺寸（16:9，640×360 高清素材）
CANVAS_W = 640
CANVAS_H = 360

# 脚底在画布内的 y 坐标（与母版一致：640×360 画布脚底 y=330）
FEET_Y = 330 / 360 * CANVAS_H  # = 330

# 落地偏移：帧下移多少让脚底恰好落在窗口底线
PAD = CANVAS_H - FEET_Y        # = 30

# 视频帧时长（毫秒）—— 24fps → 1000/24 ≈ 42ms，用于时长/移动插值换算
FRAME_MS = 42

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
CHARACTERS = ('shenshen',)
MANIFEST_FILENAME = 'manifest.json'
# videos 下的分类子目录
DIR_IDLE = 'idle'
DIR_TURN = 'turn'
DIR_IDLE_TURN = 'idle_turn'  # 兼容旧结构：待机+转向合并目录
DIR_MOVE = 'move'
DIR_CLICK = 'click'
DIR_DRAG = 'drag'
DIR_RANDOM = 'random'

# ---------------------------------------------------------------- 动画映射
# 中文名 → webm 文件名（主路径，文件名与中文名一致）
ANIM_FILES: dict[str, str] = {
    '待机呼吸休闲': '待机呼吸休闲.webm',
    '东张西望': '东张西望.webm',
    '螃蟹走路': '螃蟹走路.webm',
    '漂浮踏步': '漂浮踏步.webm',
    '左转奔跑': '左转奔跑.webm',
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
MOVES = ['螃蟹走路', '漂浮踏步', '左转奔跑']
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


def characters_gif_dir() -> Path:
    """内置 GIF 多形象根目录（项目根/assets/characters_gif）。"""
    return Path(__file__).resolve().parent.parent / 'assets' / 'characters_gif'


def character_gif_video_dir(character_id: str) -> Path:
    """内置某个形象的 GIF 目录：assets/characters_gif/<id>/videos。"""
    return characters_gif_dir() / character_id / 'videos'


def _data_character_dirs(app_dir_name: str) -> list[Path]:
    """按平台返回某数据目录下 characters/ 的外部形象根目录。"""
    if sys.platform == 'win32':
        data_root = Path(os.environ.get('APPDATA', Path.home())) / app_dir_name
    elif sys.platform == 'darwin':
        data_root = Path.home() / 'Library' / 'Application Support' / app_dir_name
    else:
        data_root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / app_dir_name
    return [data_root / 'characters']


def external_character_dirs() -> list[Path]:
    """外部可扩展形象根目录（不存在时返回空列表，不报错）。

    顺序：
    1. exe 同目录 / 当前工作目录下的 characters/
    2. 打包变体数据目录下的 characters/（变体感知，如 dsh-pet-standalone-webm-chat）
    3. 旧目录 dsh-pet-standalone/characters/（兼容老用户已在旧目录放置的角色）

    数据目录名复用 config.APP_DIR_NAME（延迟导入，避免与 config 顶层
    ``from . import catalog`` 构成循环依赖）。
    """
    dirs: list[Path] = []
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path.cwd()
    dirs.append(base / 'characters')

    from . import config as _config  # 延迟导入防环：config 顶层 import catalog
    app_dir_name = getattr(_config, 'APP_DIR_NAME', 'dsh-pet-standalone')
    dirs.extend(_data_character_dirs(app_dir_name))
    # 非变体旧目录兜底：老用户已放入的角色不能因为变体拆分而丢
    if app_dir_name != 'dsh-pet-standalone':
        dirs.extend(_data_character_dirs('dsh-pet-standalone'))
    return dirs


def resolve_character_video_dir(character_id: str) -> Path:
    """按 外部 > 内置(webm) > 内置(gif) 返回形象视频目录；都不存在时回退 webm 路径，不报错。"""
    for root in external_character_dirs():
        candidate = root / character_id / 'videos'
        if candidate.is_dir():
            return candidate
    webm_dir_path = character_video_dir(character_id)
    if webm_dir_path.is_dir() and any(webm_dir_path.rglob('*.webm')):
        return webm_dir_path
    gif_dir_path = character_gif_video_dir(character_id)
    if gif_dir_path.is_dir() and any(gif_dir_path.rglob('*.gif')):
        return gif_dir_path
    return webm_dir_path


def list_available_characters() -> list[str]:
    """返回可切换角色列表：内置角色 + 外部目录中额外检测到的角色。

    外部目录不存在时静默跳过，不会报错。
    """
    ids: list[str] = list(CHARACTERS)
    seen = set(ids)
    for root in external_character_dirs():
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for child in entries:
            video_dir = child / 'videos'
            if child.is_dir() and video_dir.is_dir() and (
                any(video_dir.rglob('*.webm')) or any(video_dir.rglob('*.gif'))
            ):
                cid = child.name
                if cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
    return ids


def webm_dir() -> Path:
    """默认形象 shenshen 的 webm 素材目录（兼容旧调用）。"""
    return character_video_dir(DEFAULT_CHARACTER)


def legacy_assets_dir() -> Path:
    """兼容旧名称：默认形象 webm 素材目录。"""
    return webm_dir()


def load_character_manifest(character_id: str, asset_dir: Path | str | None = None) -> dict | None:
    """读取角色目录下的 manifest.json（可选）。

    查找位置（按优先级）：
    1. <角色目录>/videos/manifest.json
    2. <角色目录>/manifest.json

    不存在或解析失败时返回 None，不影响运行。
    """
    if asset_dir is not None:
        video_dir = Path(asset_dir)
    else:
        video_dir = resolve_character_video_dir(character_id)
    candidates = [
        video_dir / MANIFEST_FILENAME,
        video_dir.parent / MANIFEST_FILENAME,
    ]
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
            except Exception:
                return None
    return None


def character_display_name(character_id: str) -> str:
    """角色显示名：manifest.json 的 name 字段优先，缺省回退目录 id。"""
    manifest = load_character_manifest(character_id)
    if isinstance(manifest, dict):
        name = str(manifest.get('name', '') or '').strip()
        if name:
            return name
    return character_id


def _manifest_name(value, names: set[str]) -> str | None:
    """把 manifest 中的文件名/动画名解析为实际存在的动画名。"""
    if not isinstance(value, str):
        return None
    stem = Path(value).stem
    if stem in names:
        return stem
    if value in names:
        return value
    return None


def _manifest_names(values, names: set[str]) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    for v in values:
        name = _manifest_name(v, names)
        if name is not None and name not in result:
            result.append(name)
    return result


def _keyword_match(name: str, keywords) -> bool:
    low = name.lower()
    return any(k.lower() in low for k in keywords)


def build_categories(names, manifest: dict | None = None, folder_map: dict | None = None, folder_files: dict | None = None) -> dict:
    """根据某个形象实际拥有的动画名，动态计算分类。

    分类优先级：
    1. 如果提供 folder_map（来自 videos/ 下的子目录），优先按子目录分类；
    2. 如果角色目录存在 manifest.json，则按 manifest 指定分类（可补充/覆盖）；
    3. 否则按内置已知名称 + 文件名关键词自动识别；
    4. 未进入核心分类的动画自动归入“随机动作池”。

    推荐的 videos 目录结构：
        videos/
        ├── idle/     # 待机（可多个）
        ├── turn/     # 转向（可多个）
        ├── move/     # 移动
        ├── click/    # 点击回应
        ├── drag/     # 拖拽（可选）
        └── random/   # 随机动作
    """
    names = set(names)
    if not names:
        return {
            'idle': None, 'turn': None,
            'idles': [], 'turns': [],
            'moves': [], 'clicks': [], 'drag': None, 'acts': [],
        }

    idles: list[str] = []
    turns: list[str] = []
    moves: list[str] = []
    clicks: list[str] = []
    drag = None

    if folder_files is not None:
        by_folder: dict[str, list[str]] = {k: list(v) for k, v in folder_files.items()}
    elif folder_map:
        by_folder: dict[str, list[str]] = {}
        for name in names:
            by_folder.setdefault(folder_map.get(name, ''), []).append(name)
    else:
        by_folder = {}

    if folder_files is not None or folder_map:
        idles = list(by_folder.get(DIR_IDLE, []))
        turns = list(by_folder.get(DIR_TURN, []))
        legacy_idle_turn = by_folder.get(DIR_IDLE_TURN, [])

        if not idles and legacy_idle_turn:
            idle_candidates = [
                n for n in legacy_idle_turn
                if n == IDLE or _keyword_match(n, ['待机', 'idle', '呼吸'])
            ]
            idles = idle_candidates or (legacy_idle_turn[:1] if legacy_idle_turn else [])

        if not turns and legacy_idle_turn:
            turn_candidates = [
                n for n in legacy_idle_turn
                if n == TURN or _keyword_match(n, ['转向', '转身', '东张西望', 'turn', '回头', '转'])
            ]
            turns = turn_candidates
            if not turns and idles and len(legacy_idle_turn) > 1:
                turns = [n for n in legacy_idle_turn if n != idles[0]][:1]

        moves = list(by_folder.get(DIR_MOVE, []))
        clicks = list(by_folder.get(DIR_CLICK, []))
        drag_names = by_folder.get(DIR_DRAG, [])
        if drag_names:
            drag = drag_names[0]

    # manifest 补充/覆盖
    if manifest:
        if not idles:
            m = _manifest_name(manifest.get('idle'), names)
            if m:
                idles = [m]
        if not turns:
            m = _manifest_name(manifest.get('turn'), names)
            if m:
                turns = [m]
        if not moves:
            moves = _manifest_names(manifest.get('moves', []), names)
        if not clicks:
            clicks = _manifest_names(manifest.get('clicks', []), names)
        if drag is None:
            drag = _manifest_name(manifest.get('drag'), names)

    # 关键词兜底
    if not idles:
        m = IDLE if IDLE in names else next(
            (n for n in names if _keyword_match(n, ['待机', 'idle', '呼吸'])), None
        )
        if m:
            idles = [m]
    if not turns:
        m = TURN if TURN in names else next(
            (n for n in names if _keyword_match(n, ['转向', '转身', '东张西望', 'turn', '回头', '转'])), None
        )
        if m:
            turns = [m]
    if drag is None:
        drag = DRAG if DRAG in names else next(
            (n for n in names if _keyword_match(n, ['拖拽', '拖', '悬空', 'drag', '抓'])), None
        )
    if not moves:
        moves = [n for n in MOVES if n in names]
        if not moves:
            moves = [n for n in names if _keyword_match(n, ['走', '跑', '移动', 'move', 'walk', 'run', '踏步', '奔跑'])]
    if not clicks:
        clicks = [n for n in CLICKS if n in names]
        if not clicks:
            clicks = [n for n in names if _keyword_match(n, ['点击', '回应', 'click', 'response'])]

    # 如果没有明确 idle，安全回退到第一个动画，避免启动崩溃
    if not idles:
        first = next(iter(names), None)
        if first:
            idles = [first]

    # 位移只给真正的走路素材：文件名含「原地」的移动素材只播姿态、不位移
    # （用户反馈：原地动画带着窗口跑是 bug 观感）。原地素材降级进动作池。
    inplace_moves = [m for m in moves if '原地' in m]
    moves = [m for m in moves if '原地' not in m]

    core = set(idles) | set(turns) | set(moves) | set(clicks)
    if drag:
        core.add(drag)

    if folder_files is not None:
        # 子目录模式下，random/ 和未知目录的内容都进入随机动作池；
        # 允许同一文件同时出现在多个分类中（例如测试时复制同一视频到多个文件夹）
        acts = []
        known = {DIR_IDLE, DIR_TURN, DIR_MOVE, DIR_CLICK, DIR_DRAG}
        for folder, ns in by_folder.items():
            # random/ 和未知目录（含 events/balance）都进入随机动作池：
            # 余额动画既要能按档位触发，也要允许在随机动画/手动菜单里播放。
            if folder == DIR_RANDOM or folder not in known:
                acts.extend(ns)
        seen_acts = set()
        unique_acts = []
        for n in acts:
            if n not in seen_acts:
                seen_acts.add(n)
                unique_acts.append(n)
        acts = unique_acts
    else:
        acts = [n for n in names if n not in core]
    acts.extend(n for n in inplace_moves if n not in acts)  # 原地素材降级为随机动作
    return {
        'idle': idles[0] if idles else None,
        'turn': turns[0] if turns else None,
        'idles': idles,
        'turns': turns,
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
