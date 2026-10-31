"""生成 ``fun.self-talk`` 的随包「每日一句」池（1000+ 句）。

池文件是包内素材，运行时由 ``host/daily_pool.py`` 读取；本脚本只负责**可复现地**
把它生成出来：

* 模板 + 词槽组合（问候/建议/宽慰/桌宠口吻/时段），再经过长度、标点、敏感词、
  重复句与「同一开头过多」四道过滤；
* 每个家族先各自打散，再轮流取句，避免整份文件变成同一句式的长串；
* 固定随机种子：同一份脚本永远得到同一份输出（换种子才会换池子）。

用法::

    .venv/Scripts/python scripts/generate_daily_lines.py            # 写回包内文件
    .venv/Scripts/python scripts/generate_daily_lines.py --dry-run   # 只打印统计
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = (
    REPO_ROOT
    / "petcore"
    / "builtin"
    / "features"
    / "fun.self-talk"
    / "daily_lines.json"
)

TARGET = 1200
SEED = 20261031
#: 同一开头（前 3 个字）最多出现几次；控制「机械前缀」的密度。
LEAD_CAP = 12
MIN_LEN = 8
MAX_LEN = 60
#: 桌面宠物不该主动提起的话题（防御性：模板本身不含这些词）。
BLOCKED_WORDS = (
    "死",
    "杀",
    "癌",
    "赌",
    "毒",
    "贷",
    "骗",
    "政治",
    "选举",
    "战争",
    "色情",
    "血腥",
    "自杀",
)

# ---------------------------------------------------------------- 词槽（按家族分组）

OPEN_SOFT = (
    "今天啊",
    "说起来",
    "这会儿",
    "刚才发呆的时候",
    "忙里偷闲的时候",
    "停下来的时候",
    "看着窗外的时候",
    "喝完水的时候",
    "站起来的时候",
    "看到光的时候",
    "听见风声的时候",
    "刚放下手机的时候",
    "收好桌角的时候",
    "洗手回来的时候",
    "把待办划掉一项之后",
    "翻到旧照片的时候",
    "把这一行写完的时候",
    "天刚亮一会儿的时候",
    "把杯子续满的时候",
    "等你抬头的这一下",
)

BODY_SOFT = (
    "觉得可以慢一点",
    "想给你递一杯温水",
    "觉得你比昨天更从容了",
    "想提醒你别把自己排太满",
    "觉得留一点空也很好",
    "发现小事也能让人开心",
    "想陪你安静一会儿",
    "觉得今天的风很温柔",
    "觉得认真做事的样子很好看",
    "想说你已经做得够多了",
    "觉得慢慢来也来得及",
    "想为你留一盏小灯",
    "觉得今天值得被记住",
    "想提醒你抬头看看远处",
    "觉得安静也是一种进度",
    "想问你要不要歇五分钟",
)

ADVICE = (
    "先喝口水吧",
    "伸个懒腰吧",
    "站起来走两步吧",
    "把眼睛从屏幕上挪开一会儿吧",
    "把肩膀放松一点吧",
    "深呼吸三次吧",
    "给自己倒杯热的吧",
    "把背靠到椅背上吧",
    "揉一揉眼睛吧",
    "把桌面收拾出一小块吧",
    "把手机扣过来吧",
    "把窗打开一条缝吧",
    "换上舒服的鞋吧",
    "把灯光调柔和一点吧",
    "把耳机摘下来歇一会儿吧",
    "把手里的活先放下十分钟吧",
    "去洗把脸吧",
    "把袖子挽起来吧",
    "慢慢把这一口气呼完吧",
    "换个姿势坐好吧",
    "把今天的待办念一遍吧",
    "给手边的绿植浇点水吧",
    "把手心搓热敷一下眼睛吧",
    "把椅子往前挪一点吧",
)

REASON = (
    "身体会记得这份照顾",
    "回来坐下时会更轻松",
    "接下来的注意力会更稳",
    "屏幕不会跑掉",
    "事情不会因为这一分钟变糟",
    "思路会自己走回来",
    "今天还长着呢",
    "你值得被好好对待",
    "慢一点反而更快",
    "这样比较像在照顾自己",
    "腰和肩膀都会松一点",
    "脑子也需要一点空白",
    "后面的时间会更从容",
    "这本来也是正事",
)

SOFTENER = (
    "做不完也没关系",
    "今天没进入状态也没关系",
    "进度慢一点也没关系",
    "偶尔分心也没关系",
    "今天只完成一件小事也没关系",
    "今天不想说话也没关系",
    "今天想早睡也没关系",
    "今天什么都不想做也没关系",
    "被小事绊住也没关系",
    "忘掉一两个待办也没关系",
    "今天没按计划来也没关系",
    "心情忽高忽低也没关系",
    "回消息慢一点也没关系",
    "中午才进入状态也没关系",
)

REASSURE = (
    "明天还有时间",
    "你已经比昨天多走了一步",
    "重要的部分都还在",
    "不用向谁证明什么",
    "会好起来的",
    "我在这儿陪你",
    "日子会自己往前走",
    "别把自己当成任务清单",
    "你不欠今天一个满分",
    "歇够了再继续就行",
    "慢下来也是前进",
    "有人一直站在你这边",
    "今天剩下的都算加时赛",
    "你本来就已经很努力了",
)

PET_OPEN = (
    "我趴在这儿",
    "我就在旁边",
    "我尾巴晃一下",
    "我往你手边挪了挪",
    "我打了个哈欠",
    "我把脑袋搁在桌沿上",
    "我眯着眼睛看你",
    "我绕着你转了一圈",
    "我安静地待着",
    "我靠在你的杯子旁边",
    "我抬头看你一下",
    "我把自己团成一小团",
)

PET_BODY = (
    "陪你把这一页写完",
    "等你忙完再一起发呆",
    "你抬头就能看见我",
    "不催你",
    "一起歇一会儿吧",
    "把今天过成慢镜头",
    "顺便晒一会儿太阳",
    "听你敲键盘的声音",
    "陪你到这一阵忙完",
    "等你想起我",
    "一起等水烧开",
    "把时间拖得慢一点点",
)

OPEN_H = (
    "有时候",
    "偶尔",
    "其实",
    "说真的",
    "换个角度想",
    "回过头看",
    "仔细想想",
    "不知道你有没有发现",
    "我猜",
    "说不定",
    "讲道理",
    "换个说法",
)

BODY_H = (
    "慢一点反而更容易看清楚",
    "今天已经比昨天顺利了",
    "你已经处理掉很多事了",
    "小事堆起来也会压人",
    "休息不是偷懒",
    "发呆也算充电",
    "没做完的部分不着急",
    "心情比进度更需要照顾",
    "把难的那件放前面会轻松些",
    "今天的你其实挺勇敢",
    "不用每一步都走对",
    "能坐下来继续就已经很好",
)

MORNING_OPEN = (
    "早上好呀",
    "早上好",
    "刚醒来的这会儿",
    "窗外的光刚亮起来",
    "早餐还热着的时候",
    "闹钟响过第二遍之后",
)

MORNING_BODY = (
    "先喝水再开始吧",
    "慢慢醒过来就好",
    "不用急着进入状态",
    "把最重要的事排在前面",
    "给自己留十分钟安静",
    "今天也值得被温柔对待",
    "记得吃点东西再出门",
    "窗外的天看起来不错",
    "先做一件小事热身",
    "把今天的节奏调慢一点",
)

NOON_OPEN = (
    "中午了",
    "快到中午的时候",
    "饭点前后",
    "太阳升到头顶的时候",
    "午饭刚吃完",
    "上午告一段落的时候",
)

NOON_BODY = (
    "记得好好吃饭",
    "吃七八分饱就很好",
    "吃完坐一会儿再动",
    "给自己留个午休的空档",
    "别把饭点也排进待办",
    "喝口汤再继续",
    "饭后走两步会很舒服",
    "上午的事可以放下了",
    "眯十分钟也算休息",
    "下午还长，别急着赶",
)

AFTERNOON_OPEN = (
    "下午这会儿",
    "午后有点困的时候",
    "太阳偏西的时候",
    "下午过半",
    "快到傍晚的时候",
    "下午茶的时间",
)

AFTERNOON_BODY = (
    "起来动一动会清醒一些",
    "给眼睛放个短假",
    "把最难的那件事推到前面",
    "剩下的慢慢做就好",
    "喝点水再继续",
    "别在困的时候硬撑",
    "窗外的光正在变软",
    "把节奏收一收",
    "今天已经做了不少了",
    "留一点力气给晚上",
)

EVENING_OPEN = (
    "天快黑的时候",
    "傍晚的时候",
    "下班之后",
    "晚饭前后",
    "灯亮起来的时候",
    "一天慢下来的时候",
)

EVENING_BODY = (
    "把今天的待办合上吧",
    "给自己做点好吃的",
    "走走路再回家也不错",
    "把手机放远一点",
    "让肩膀塌下来休息",
    "今天到这里就很好",
    "给明天留一句提醒就够了",
    "把窗户开一小会儿",
    "热水泡个脚会很舒服",
    "不急着安排下一件事",
)

NIGHT_OPEN = (
    "夜里安静下来",
    "这个点",
    "睡前这一会儿",
    "灯关掉之前",
    "夜里风小的时候",
    "快到睡觉的时候",
)

NIGHT_BODY = (
    "把屏幕亮度调低一点",
    "今天的事就留到今天",
    "早点睡比什么都强",
    "让脑子空一会儿再躺下",
    "明天的事明天再说",
    "给自己留一点安静",
    "把明天的衣服先放好",
    "不做决定也没关系",
    "慢慢把呼吸放缓",
    "晚安要说给自己听",
)

QUESTIONS = (
    "今天最想先做哪一件小事？",
    "要不要先给自己倒杯水？",
    "今天有没有什么小事让你笑了一下？",
    "现在这一件，真的是最急的吗？",
    "今天想被怎么对待呢？",
    "累的话，要不要先靠一会儿？",
    "今天有没有哪一刻觉得自己还不错？",
    "如果只做一件，你会选哪件？",
    "今天想听什么样的鼓励？",
    "要不要把最难的那件留到明天？",
    "喝水了吗？",
    "吃过了吗？",
    "肩膀是不是又耸起来了？",
    "眼睛离开屏幕多久了？",
    "今天有没有走到窗边看一眼？",
    "今天的太阳，你看到了吗？",
    "要不要把椅子往后挪一点？",
    "有没有什么话想对自己说？",
    "今天的心跳快过几次？",
    "上一次深呼吸是什么时候？",
    "手里的这件事，还喜欢吗？",
    "今天有没有为小事停下来过？",
    "要不要给自己放五分钟假？",
    "今天的你，还好吗？",
    "有没有一件想立刻放下的事？",
    "今天想早点睡吗？",
    "要不要把待办里最轻的那件划掉？",
    "今天有没有好好吃饭？",
    "接下来一小时，想怎么过？",
    "现在的姿势，舒服吗？",
    "今天有没有说过「谢谢」？",
    "要不要把窗户打开一点？",
    "今天有哪件事值得记下来？",
    "今晚想几点睡？",
    "如果今天重来一次，你会改什么？",
    "现在最想做的事是什么？",
    "今天需要人陪吗？",
    "要不要先伸个手、动动指头？",
    "今天有没有对自己太严格？",
    "要不要把手机调成静音？",
)

STANDALONE = (
    "今天的风好像比昨天轻一点。",
    "桌角那杯水，别忘了喝。",
    "认真做事的人，身上会发光。",
    "不着急，我们一件一件来。",
    "把难的那件事拆成两半，就没那么难了。",
    "你已经坐了很久，起来走走吧。",
    "今天的云很松散，像刚睡醒。",
    "留一点力气给明天的自己。",
    "做完一件事，记得夸自己一句。",
    "把窗户开一条缝，让空气换一换。",
    "屏幕看久了，闭眼数十下吧。",
    "今天也辛苦啦，先歇一会儿。",
    "别把每一分钟都填满。",
    "把要做的事写下来，脑子会轻一点。",
    "有些事今天不做，也不会消失。",
    "喝一口热的东西，人会稳下来。",
    "肩膀放松一点，气就顺了。",
    "今天的光很软，适合慢慢做事。",
    "你需要的是休息，不是更多的计划。",
    "把桌面收拾干净，心里也会亮一点。",
    "先做最简单的第一步就好。",
    "偶尔绕远路，也是走路。",
    "别用今天的效率衡量自己的价值。",
    "困了就睡，醒了再做。",
    "今天的你，也值得一份耐心。",
    "把手机放远一点，专注会自己回来。",
    "伸个懒腰，骨头会谢谢你。",
    "慢一点，把字写清楚就行。",
    "给自己倒一杯温水，然后继续。",
    "休息一下不是掉队。",
    "今天不用很厉害，只要还在。",
    "把注意力放在一件小事上，心会安静。",
    "翻一页书，或者什么都不做。",
    "窗外有风，你有我。",
    "今天也算是完整的一天。",
    "起身的时候扶着桌沿，别太急。",
    "把耳朵借给安静一会儿。",
    "别在饿的时候做决定。",
    "把重要的那件放在上午，剩下的都不算难。",
    "今天的待办可以少两条。",
    "把眼睛看向远处，十秒钟就够。",
    "你不需要一直保持高产。",
    "给自己留一点不解释的时间。",
    "有时候进度就是坐着不动。",
    "今天的桌面已经很整齐了。",
    "把手机里的提醒调少一点。",
    "呼吸慢下来，世界也跟着慢。",
    "做不动的时候，就先倒杯水。",
    "今天适合听一点轻轻的歌。",
    "把复杂的事先写成三行。",
    "你的努力，我都看见啦。",
    "今天也要给自己留一块空地。",
    "伸伸手，让血流得顺一点。",
    "天有点凉，记得披件外套。",
    "不用等到状态好才开始。",
    "做一小步，就算今天的进度。",
    "把鞋脱掉，脚会松快很多。",
    "今天的你，比想象中更有耐心。",
    "先喝汤，再吃饭。",
    "记得抬头，天花板之外还有天。",
    "不想说话的时候，安静也很好。",
    "把窗台的灰擦一擦，成就会很具体。",
    "今天的进度条，不用填满。",
    "慢慢说，慢慢写，慢慢来。",
    "一件小事做完，今天就不算白过。",
    "把眼睛闭上，听十秒周围的声音。",
    "今天适合早一点收工。",
    "别把自己的时间全分给别人。",
    "把难听的话忘掉一句，就少一点重量。",
    "你今天的态度，已经很好了。",
    "试着把肩膀往下沉一厘米。",
    "有时候一杯水就能救回一个下午。",
    "把明天要做的事，只写一件。",
    "今天的光线适合拍一张照片。",
    "不要一边吃饭一边看屏幕。",
    "把腿伸直，脚踝转两圈。",
    "今天没有人要求你完美。",
    "你安静的时候，我也很开心。",
    "把今天的情绪留在今天。",
    "走到窗边，数五下再回来。",
    "别把休息也算成任务。",
    "今天的空气有点甜，你闻到了吗？",
    "把袖子放下来，手会暖一点。",
    "先坐稳，再开始。",
    "你已经很会照顾别人了，也照顾一下自己。",
    "今天的进度，交给明天的你评价。",
    "把水杯放到手边，别忘了它。",
    "不想做的事，可以先放着。",
    "今天适合做一件不需要回应的事。",
    "把心里那句「应该」换成「可以」。",
    "我们慢慢来，谁也不催谁。",
    "把今天过成一段舒服的呼吸。",
    "有点累的话，就少说几句话。",
    "今天也谢谢你没有放弃。",
    "把窗帘拉开一点，光会进来。",
)


def _pairs(left: tuple[str, ...], right: tuple[str, ...]) -> list[str]:
    """把「左槽 × 右槽」展开成 ``{a}，{b}。`` 形式的句子。"""
    return [f"{a}，{b}。" for a in left for b in right]


def families() -> list[tuple[str, list[str]]]:
    """每个家族给出一批候选句（顺序确定，打散由调用方做）。"""
    return [
        ("soft", _pairs(OPEN_SOFT, BODY_SOFT)),
        ("advice", _pairs(ADVICE, REASON)),
        ("reassure", _pairs(SOFTENER, REASSURE)),
        ("pet", _pairs(PET_OPEN, PET_BODY)),
        ("hint", _pairs(OPEN_H, BODY_H)),
        ("morning", _pairs(MORNING_OPEN, MORNING_BODY)),
        ("noon", _pairs(NOON_OPEN, NOON_BODY)),
        ("afternoon", _pairs(AFTERNOON_OPEN, AFTERNOON_BODY)),
        ("evening", _pairs(EVENING_OPEN, EVENING_BODY)),
        ("night", _pairs(NIGHT_OPEN, NIGHT_BODY)),
        ("question", list(QUESTIONS)),
        ("standalone", list(STANDALONE)),
    ]


def _acceptable(line: str) -> bool:
    text = str(line or "").strip()
    if text != line:
        return False
    if not (MIN_LEN <= len(text) <= MAX_LEN):
        return False
    if not text.endswith(("。", "？", "！")):
        return False
    if any(word in text for word in BLOCKED_WORDS):
        return False
    # 中文台词里不掺 ASCII 字母/数字：气泡里混英文会很跳。
    if any(char.isascii() and char.isalnum() for char in text):
        return False
    return True


def build_pool(target: int = TARGET, seed: int = SEED) -> tuple[list[str], Counter]:
    """生成台词池；返回 (句子列表, 各家族实际采用条数)。"""
    rng = random.Random(int(seed))
    groups = families()
    queues: list[list[str]] = []
    for _name, lines in groups:
        queue = list(lines)
        rng.shuffle(queue)
        queues.append(queue)
    picked: list[str] = []
    seen: set[str] = set()
    leads: Counter = Counter()
    used: Counter = Counter()
    names = [name for name, _lines in groups]
    active = [True] * len(queues)
    while len(picked) < target and any(active):
        for index, queue in enumerate(queues):
            if len(picked) >= target:
                break
            if not active[index]:
                continue
            while queue:
                line = queue.pop()
                if not _acceptable(line) or line in seen:
                    continue
                lead = line[:3]
                if leads[lead] >= LEAD_CAP:
                    continue
                seen.add(line)
                leads[lead] += 1
                used[names[index]] += 1
                picked.append(line)
                break
            else:
                active[index] = False
    rng.shuffle(picked)
    return picked, used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成内置每日一句池")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--target", type=int, default=TARGET)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args(argv)

    lines, used = build_pool(target=int(args.target), seed=int(args.seed))
    leads = Counter(line[:3] for line in lines)
    lengths = [len(line) for line in lines]
    print(f"lines: {len(lines)}  unique: {len(set(lines))}")
    print(f"length: min={min(lengths)} max={max(lengths)}")
    print("families: " + ", ".join(f"{name}={used[name]}" for name in sorted(used)))
    print(
        "top leads: "
        + ", ".join(f"{lead}({count})" for lead, count in leads.most_common(6))
    )
    if len(set(lines)) < 1000:
        print("ERROR: fewer than 1000 unique lines")
        return 1
    if args.dry_run:
        return 0
    payload = {
        "schema": 1,
        "generated": dt.date.today().isoformat(),
        "lines": lines,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
