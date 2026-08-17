# -*- coding: utf-8 -*-
"""
动画目录（catalog）—— 全部动画名、文件映射、分类与几何常量的"事实来源"。

素材来源：dsh-pet 插件（https://github.com/PC2005-cloud/dsh-pet）的
assets/thumb/*.webm（640×360 透明 webm，VP9 alpha）经 scripts/convert.py
转码为 640×360 透明 GIF，QMovie 原生播放（零额外依赖）。

几何常量与原插件 client.js 完全一致：
- 画布 640×360，人物脚底 y=330
- 落地偏移 PAD = 360 - 330 = 30px（绘制时把帧下移 PAD，让脚踩在窗口底线）
"""

from pathlib import Path

# ---------------------------------------------------------------- 画布几何
# GIF 尺寸（16:9，640×360 高清素材）
CANVAS_W = 640
CANVAS_H = 360

# 脚底在画布内的 y 坐标（与母版一致：640×360 画布脚底 y=330）
FEET_Y = 330 / 360 * CANVAS_H  # = 330

# 落地偏移：帧下移多少让脚底恰好落在窗口底线
PAD = CANVAS_H - FEET_Y        # = 30

# GIF 的帧时长（毫秒）—— 24fps → 40ms，用于时长/移动插值换算
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

# ---------------------------------------------------------------- 动画映射
# 中文名 → GIF 文件名（主路径）
ANIM_FILES: dict[str, str] = {
    # 待机
    '待机呼吸休闲': 'daiji-huxi-xiuxian.gif',
    # 转向
    '东张西望': 'dongzhangxiwang.gif',
    # 移动姿态（位置由代码驱动）
    '螃蟹走路': 'pangxie-zoulu.gif',
    '原地漂浮踏步': 'yuandi-piaofu-tabu.gif',
    '原地左转奔跑': 'yuandi-zuozhuan-benpao.gif',
    # 点击回应 ×3
    '点击回应 - 开心跃动': 'dianji-huiying-kaixin-yuedong.gif',
    '点击回应 - 害羞惊讶': 'dianji-huiying-haixiu-jingya.gif',
    '点击回应 - 傲娇生气（侧身展示）': 'dianji-huiying-aojiao-shengqi-ceshen-zhanshi.gif',
    # 拖拽
    '被鼠标拖拽悬空反馈': 'beishubiao-tuozhuai-xuankong-fankui.gif',
    # 随机动作池 ×42（与 client.js ACTS 一致）
    '悠闲哼歌': 'youxian-hengga.gif',
    '超大伸懒腰': 'chaoda-shenlanyao.gif',
    '原地专心玩魔方': 'yuandi-zhuanxin-wan-mofang.gif',
    '原地敲击桌面互动': 'yuandi-qiaoji-zhuomian-hudong.gif',
    '原地重力下蹲压缩': 'yuandi-zhongli-xiadun-yasuo.gif',
    '哈欠连天': 'haqian-liantian.gif',
    '原地小憩沉眠': 'yuandi-xiaoqi-chenmian.gif',
    '原地蹲下玩玩具汽车': 'yuandi-dunxia-wan-wanju-qiche.gif',
    '鲸鱼吐泡泡特效': 'jingyu-tu-paopao-texiao.gif',
    '女仆屈膝礼仪': 'nvpu-quxi-liyi.gif',
    '被吓一跳（炸毛）': 'beixiayitiao-zhamao.gif',
    '原地跳跃抓碎头顶物品': 'yuandi-tiaoyue-zhuasui-touding-wupin.gif',
    '小幅度原地 360 度旋转展示': 'xiaofudu-yuandi-360du-xuanzhuan-zhanshi.gif',
    '偷吃零食被抓住': 'touchi-lingshi-bei-zhuazhu.gif',
    '玩游戏气急败坏': 'wan-youxi-qijibaituai.gif',
    '用鲸鱼尾巴拍打地面': 'yong-jingyu-weiba-paidadi.gif',
    '打瞌睡被惊醒': 'da-keshui-bei-jingxing.gif',
    '玩水枪': 'wan-shuiqiang.gif',
    '小提琴演奏': 'xiaotiqin-yanzou.gif',
    '蓝鲸现世': 'lanjing-xianshi.gif',
    '吃白饭': 'chi-baifan.gif',
    '照镜子': 'zhao-jingzi.gif',
    '优雅女仆舞': 'youya-nvpuwu.gif',
    '轻快摇摆舞': 'qingkuai-yaobaiwu.gif',
    '可爱宅舞': 'keai-zhaiwu.gif',
    '整体换装试色': 'zhengti-huanzhuang-shise.gif',
    '大口吃零食': 'dakou-chi-lingshi.gif',
    '吹气球': 'chui-qiqiu.gif',
    '动物环绕': 'dongwu-huanrao.gif',
    '深度思考碎碎念': 'shendu-sikao-suisuinian.gif',
    '轻快记录': 'qingkuai-jilu.gif',
    '写代码': 'xie-daima.gif',
    '吃Token': 'chi-token.gif',
    '吃早餐': 'chi-zaocan.gif',
    '吃午餐': 'chi-wucan.gif',
    '吃晚餐': 'chi-wancan.gif',
    '放风筝': 'fang-fengzheng.gif',
    '摇扇纳凉': 'yaoshan-naliang.gif',
    '吃冰淇淋融化': 'chi-bingqilin-ronghua.gif',
    '被落叶淹没': 'beiluoye-yanmo.gif',
    '中秋赏月吃月饼': 'zhongqiu-shangyue-chi-yuebing.gif',
    '堆雪人': 'duixueren.gif',
}

# 中文名 → 上游 webm 文件名（兼容回退，仅当 GIF 缺失时使用）
WEBM_FILES: dict[str, str] = {name: f'{name}.webm' for name in ANIM_FILES}

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
    """主素材目录（项目根/assets/animations，透明 GIF）。"""
    return Path(__file__).resolve().parent.parent / 'assets' / 'animations'


def legacy_assets_dir() -> Path:
    """兼容回退目录（项目根/assets/videos，透明 webm）。"""
    return Path(__file__).resolve().parent.parent / 'assets' / 'videos'


def resolve_asset_path(name: str, filename: str, base_dir: Path | None = None) -> Path:
    """解析素材路径；GIF 缺失时回退到同名 webm。"""
    base_dir = assets_dir() if base_dir is None else Path(base_dir)
    path = base_dir / filename
    if path.exists():
        return path
    if base_dir == assets_dir() and path.suffix.lower() == '.gif':
        webm = legacy_assets_dir() / WEBM_FILES.get(name, path.with_suffix('.webm').name)
        if webm.exists():
            return webm
    return path