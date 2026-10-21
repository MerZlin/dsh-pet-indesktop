# 灵动岛第三方段扩展契约（P3 Slice F1 契约 + F2 宿主链）

> 基线：分支 `feature/island-segment-redesign`，2026-10-07（P3 Slice F1）。契约的权威实现是
> `pet/island/segments.py`（零 Qt）；**组件安装面与采样宿主**的权威实现是
> `pet/island/extension_host.py`。本文是**对第三方的公开说明**，字段与规则变了要同步改这里。
> 相关：P1 段化重设计（`PR-REPORT-ISLAND-P1-SEGMENT-REDESIGN-2026-10-07.md`，段契约与宽度白名单先例）、
> 卡片四分区与「不支持段不占位」机制（`PR-REPORT-ISLAND-*`，见 `docs/INDEX.md`「PR 报告存档」）。

## 一句话

第三方只交**数据与声明**，岛上怎么画、画多宽、折谁、卡片怎么写，全部是宿主的事——
**你拿不到 QPainter**。契约层 `pet/island/segments.py` 不 import 任何 Qt。

安装路线（F2 起，推荐）：把组件目录放进**可信本地目录**，声明写在清单 JSON 里。
组件代码只在**运行期**被导入与采样（见 §八）。

```text
<可信目录>/<你的组件>/
    island-component.json      # 静态声明（id / label / max_text / unit / 阈值 / provider 文件名）
    provider.py                # 运行期才导入：read() 取值、format_value() 格式化
```

进程内路线（仍在，适合集成在宿主进程里的第三方库）：直接调

```text
from pet.island import segments
segments.register_segment(id=..., label=..., format_value=..., max_text=..., ...)
```

两条路最终走**同一个**声明与同一套呈现/折叠/告警链路；区别只有一处：**能不能写进配置**
由清单决定（`island_metrics` 的准入看清单，不看"谁在运行期注册过"，见 §八）。

## 一、声明字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 稳定标识。非空、单行、无空白、≤ 32 字符，且不得是内置保留字（`network` / `cpu` / `mem` / `gpu`）。折叠、`+N` chip、卡片行都按它索引 |
| `format_value` | ✅ | `裸值 -> 文案` 的可调用对象。**异常与巨型串在这里被兜住**（见 §五） |
| `max_text` | ✅ | 最坏读数样本（如 `"888"`）。声明盒宽 = 实测它。**缺它直接拒注**：没有盒宽契约，岛宽会跟着读数跳，那是本项目的红线 |
| `label` | — | ≤ 6 字符，可空（纯 glyph 段）。超长自动截断并告警 |
| `unit` | — | 单位盒的串（`%` / `°C`）。非 `ok` 态不画单位但**盒宽仍占**，状态切换不跳宽 |
| `unit_full` | — | 单位全称（卡片详情列用） |
| `source` | — | 来源口径（卡片详情列用，如「本机无线网卡」） |
| `detail` | — | 一句话补充（卡片详情列用） |
| `thresholds` | — | `(warn, crit)` 两个**有限数**且 `warn < crit`。声明后并进告警阈值表：你的段与 CPU/内存走**同一条**判定链（两级 + 3pt 滞回，见 §六） |
| `read` | — | 无参取值函数。声明了就能用 `declaration.sample()` 一步拿到快照；没声明也能注册，只是 `sample()` 如实报 error |

`register_segment()` 也接受一个现成的 `SegmentDeclaration` 实例（两种调用形式等价）。

## 二、四态

段快照的 `state` 决定呈现（`render_compact`）：

| state | 胶囊文案 | 谁用 |
|---|---|---|
| `ok` | `label + 读数` | 有真读数 |
| `sampling` | `label + --` | 还没采到第一拍（**不是** 0） |
| `unknown` | `label + --` | 不认识这个 id / 读数不可解释 |
| `error` | `label + --` | 取值或格式化失败；原因进卡片（`detail["error_reason"]`） |
| `unsupported` | **不占位**（返回 `None`） | 已知 id 但**本机当前没有这项能力**（例：没有可用的 GPU 读数器）。"没能力"与"读不到数"是两回事——后者是 `sampling`/`error`，占位并在卡片写原因 |

`--` 与声明样本同盒宽语义，所以状态切换不改岛宽。**不许伪造读数**：拿不到就说拿不到，
`sampling` 与 `--` 是两种不同的"没有"。

## 三、宽度纪律

- 盒宽 = `horizontalAdvance(label) + (4px) + horizontalAdvance(max_text)`，**实测**，
  按 `(id, 字体指纹)` 缓存；字体/DPI 一变自动重测。声明了 `unit` 的段再
  `+ 1px + horizontalAdvance(unit)`（E4 双盒：值盒与单位盒之间 1px 间隙）——
  120px 超宽判决按这个**完整口径**算，长单位（如 `Mbps`）绕不过去。
- **采样值不进任何宽度键**：读数只在盒内右对齐伸缩（`9% → 100%` 个位不动）。
  你的格式化函数无论返回什么，都不可能改岛宽——这是结构保证，不是纪律。
- 运行期读数超过盒宽 → 右省略到盒宽 + warning（见 §五第二道护栏）。

## 四、120px 降级规则

声明盒宽 > `MAX_SEGMENT_WIDTH_PX`（120px）**不拒绝注册**：

```text
segments.oversize_segment_ids(font_metrics)   # -> 首批折叠候选（注册序）
```

- 超宽段照常注册、照常在注册表里（**不禁止表达**）；
- 它进「`+N` 首批折叠候选」名单，并在**首次**测到时记一条 warning（按
  `(id, 字体指纹)` 去重，不逐帧刷屏）；
- 判决在 `oversize_segment_ids()` 处做而不是在注册处，因为 px 必须实测——
  第三方注册时进程里通常还没有字体。**长段自付被先折的代价**；
- **宿主已消费这张名单**（F2 起）：超宽段在收缩流水线里**先于**尾部段被折，
  哪怕它落在列表中间——折叠计数、`+N` chip 与卡片标注都按"实际折掉的那几段"算。

## 五、恶意/失控段护栏（四处）+ 注册期拒注面

1. **格式化函数抛异常** → 段降级成 `error` 态（`--` + 卡片原因），warning 留痕。
   一次脏输出不会让整条岛停止绘制；`read` 抛异常同样走这条；
2. **返回巨型串** → 在**度量之前**按 `MAX_VALUE_CHARS`（32 字符）截断 + warning。
   理由：几万字符进一次 `QFontMetrics.horizontalAdvance` 就是自己给自己排一个 O(n) 长任务；
3. **返回多行串** → 压平成单行（段契约禁换行，否则撑破行高）；返回 `None` →
   `error` 态（漏写 return 不是"读数就是 None"）；
4. **异常文案同样限长压平**（`MAX_DETAIL_CHARS` = 120）：原因文案也是你的输入；
5. 以上任意一条触发后，**整岛宽度、收缩级、其他段的位置一个像素都不变**——
   失控段推不歪整岛。

注册期拒注面（明确报错，不静默吞）：重复 id → `ValueError`；非法 id（空/含空白/超长/
保留字）→ `ValueError`；字段缺失或非法 → `ValueError`；类型不对（id 非 str、
`format_value` 不可调用、多给字段关键字）→ `TypeError`。

组件清单那条路还有两个**安装面**的状态（与上面的 `error` 不同，都不是"读数坏了"）：

- **组件暂缺**（清单在、`provider` 文件不在）→ 这一段**不占位**（胶囊上连 `--` 都没有），
  卡片写清哪一份文件不在；用户的偏好留着，文件回来即恢复；
- **清单本身不合法**（坏 JSON / 未知字段 / 保留 id / provider 路径爬出目录 / 缺
  `max_text`）→ **整条组件拒收**并留一条 warning，不影响其他组件与内置段。

## 六、告警阈值

```text
thresholds=(70.0, 85.0)   # 升到 70 上 warn、85 上 crit；降过"线 − 3"才褪色
```

判定是纯函数（`alert_severity(id, value, previous)`），滞回要的"上一拍"由宿主持有——
你只要声明阈值，不需要维护状态。没有读数的拍不会褪色（"还没读数"≠"读数降到安全值"），
所以被折叠停采样时告警不会凭空消失。

**阈值进的是同一张表**：清单里声明的阈值在你这一段**装上**时并入（卸载时撤回，不留第二
真值），所以你的段与 CPU/内存走的是**同一条**判定链（两级 + 3pt 滞回），不需要自己实现
告警色。判定吃的是**原始值**（快照里的 `raw_value`），不是格式化之后的文案——"74" 与
"74.4" 对阈值是同一个数，但 `--` 不是 0。

## 七、完整示例（可跑）

下面这段原样可跑（`tests/test_island_segment_extension.py` 会把它抽出来 exec 一遍，
示例腐烂测试就红）：

```python
"""第三方段最小完整示例：一个 CPU 温度段。"""
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from pet.island import segments

app = QApplication.instance() or QApplication([])

# 1) 数据源：真实实现里在这里读传感器（示例给固定值，避免依赖本机硬件）
_reading = {"celsius": None}


def read_celsius():
    return _reading["celsius"]


def format_celsius(value):
    """值 → 文案。异常与巨型串都由契约层兜住，这里可以放心写。"""
    if value is None:
        return "--"
    return f"{round(float(value))}"


declaration = segments.register_segment(
    id="cpu_temp",
    label="温度",                  # ≤ 6 字符（超了自动截断 + 告警）
    format_value=format_celsius,
    max_text="88",                 # 必填：最坏读数样本，声明盒宽 = 实测它
    unit="°C",                     # 单位盒（10px 降色）
    unit_full="摄氏度",             # 以下四项只在展开卡片的详情列出现
    source="本机传感器",
    detail="CPU 封装温度",
    thresholds=(70.0, 85.0),       # warn < crit，进既有的告警判定链
    read=read_celsius,             # 可选的数据源；不声明也能用 snapshot(value)
)

# 2) 取一拍 → 段快照（护栏已在契约层生效）
_reading["celsius"] = 74.0
snapshot = declaration.sample()
assert snapshot.state == segments.STATE_OK
assert segments.render_compact(snapshot) == "温度74"        # 胶囊文案
assert segments.alert_severity(declaration.id, 74.0) == segments.ALERT_WARN

# 3) 宽度与降级判决（宿主排版时用同一份实测缓存）
#    注意：声明了 unit 的段，盒宽 = label + 间隔 + max_text + 1px + 单位宽（双盒）
metrics = QFontMetrics(QFont("", 13))
assert declaration.width_px(metrics) == (
    metrics.horizontalAdvance("温度") + segments.LABEL_GAP_PX
    + metrics.horizontalAdvance("88")
    + 1 + metrics.horizontalAdvance("°C"))
assert declaration.is_oversize(metrics) is False
assert segments.oversize_segment_ids(metrics) == ()

# 4) 卸载时注销：声明、并进阈值表的阈值一起撤（不留第二真值）
assert segments.unregister_segment("cpu_temp") is True
assert segments.alert_thresholds("cpu_temp") is None
```

## 八、宿主接线现状（如实，R1 评审补全版 + F2 落地）

> **2026-10-10 更新**：`gpu` 段已从"占位"改成**真实读数**；第三方段的整条链
> （组件清单 → 配置准入 → 后台采样 → 胶囊/卡片 → 超宽折叠 → 卸载）**已接通**。
> 同日**生命周期失效**补齐：清单不变时 provider 的增删改会被看见（声明扫描 + 每拍来源
> 核对）、退订→重订的旧在飞读数按每段 token 丢弃、`stop()` 后卡住的 worker 被保留并接续
> （不开第二条线程）。上表每一项都有聚焦用例，**这一节只承诺聚焦用例能证明的东西**。
> 本节的证据口径：聚焦用例 + offscreen 几何/像素断言 + 独立解释器里的设置进程路径；
> **没有**真机 GUI 目视、**没有**全量套件、**没有**构建部署。

| 能力 | 状态 |
|---|---|
| 声明/校验/注销、四态、宽度自测量（含单位盒）、120px 判决、恶意段护栏 | ✅ 已实现（`pet/island/segments.py`） |
| `gpu` 段**真实读数**（`pet/gpu_monitor.py` 的 PDH 读数器 → `sys_monitor.subscribe_gpu` → 胶囊值/卡片行/告警同一条路） | ✅ 已接线（2026-10-10）：`ok` 用真读数、其余态同宽 `--`；**不可用是非 0 语义**；**无订阅即停采集**（CPU-only 路径一次 GPU 调用都不发生） |
| 第三方段进**胶囊**的渲染接线（`_compose_info_segments` 收第三方段） | ✅ 已接线：第三方段是普通 `KIND_METRIC` 段，走与 CPU/内存同一条排版、字段盒与单位盒路径 |
| 组件**安装面**（可信本地目录 + 标准库 JSON 清单 + 仅运行期导入 provider） | ✅ 已实现（`pet/island/extension_host.py`，见本节末） |
| `oversize_segment_ids()` 在**收缩流水线**里的消费（先折候选） | ✅ 已接线：宿主按同一判决（`island_segments.judge_oversize`）把超宽段喂给 `plan_info_row(fold_first_ids=...)`；**中间段被折也按 id 生效**，`+N` 计数与 chip 告警按实际折掉的段算（`apply_shrink`） |
| **配置准入**：`island_metrics` 的合法成员 | ✅ 已接线：内置段（`island_segments.BUILTIN_SEGMENT_IDS`）∪ **声明清单里的组件**（主进程与设置进程读同一份静态 metadata，见本节末） |
| **采样生命周期**：谁调 `read`、在哪个线程、如何按折叠/隐藏/停靠退订、来源变了怎么失效 | ✅ 已实现：**一条**有界节拍的后台线程（不是每组件一条），宿主只读快照；折叠/停靠细条/隐藏/关闭/停用都退订；清单不变时 provider 的增删改在**下一次采样**即生效（见下"采样与生命周期纪律"） |
| **告警与更新时间的原始值** | ✅ 已接线：快照带 `raw_value`（阈值判定吃它）+ `monotonic` 的 `sampled_at`（卡片"几秒前"）；声明阈值并进与 CPU/内存同一张表 |

也就是说：**"注册即可上岛"现在成立**——但成立的条件是"组件**已声明**且这一段在配置里"。
上表每一项都有聚焦用例（`tests/test_island_extension_host.py`、
`tests/test_island_extension_chain.py`、`tests/test_island_extension_recovery.py`、
`tests/test_settings_network.py`），细节与边界见下。

### 组件清单（安装面，F2 起）

可信目录（**只有这两个默认位置**）：

| 根 | 路径 | 说明 |
|---|---|---|
| 用户 | 既有配置目录下的 `island-extensions`（`pet/config.py` 的 `APP_DIR_NAME`） | Windows 上即 `%APPDATA%/dsh-pet-standalone/island-extensions` |
| 项目 | 仓库 / 程序目录下的 `island-extensions` | 随产品分发的组件；不存在就等于"没有" |

环境变量 `DSH_PET_ISLAND_EXTENSION_DIRS`（`os.pathsep` 分隔）**一旦设置就只扫它**：
显式指定的可信目录，供开发与测试用（每个用例一份临时目录）。

清单是一个 JSON 对象，字段与 §一 的声明面一一对应，多一个 `provider`：

```json
{
  "id": "cpu_temp",
  "label": "温度",
  "max_text": "88",
  "unit": "°C",
  "unit_full": "摄氏度",
  "source": "本机传感器",
  "detail": "CPU 封装温度",
  "thresholds": [70, 85],
  "provider": "provider.py",
  "read": "read_celsius",
  "format": "format_celsius"
}
```

- `id` / `label` / `max_text` / `unit` / `thresholds` 的规则与 §一 完全相同（**同一份实现**：
  清单就是那个声明去掉两个可调用对象）；
- `provider` 必须是组件目录内的**相对路径**（绝对路径与 `..` 一律拒收）；
- `read` / `format` 是 provider 模块里的**属性名**，缺省 `read` / `format_value`；
- 清单里出现**未知字段**即整条拒收（拼错字段名不许静默丢弃）；被拒收的组件会记一条
  warning，并提供查询面（`island_extension_host.rejected()`），不是静默跳过。

### 采样与生命周期纪律

- **谁调 `read` / `format_value`**：只有那条后台采样线程。宿主（GUI 线程）**既不取值也不
  格式化**，只读一份快照字典——你的取值慢不会卡住绘制。宿主读快照的节拍由它自己的定时器
  驱动（默认 1 秒，等于那段声明的采样节拍、下限 200ms），**看不到就不读**：岛隐藏、信息槽
  关掉、整段收口时表也停（不会有空转的读）。
- **一条线程管全部组件**：装 1 个还是 20 个组件，都只有一条采样线程（不是每组件一条）。
  这条线程被你的 `read` 卡住时，`stop()` 之后 `start()` **不会**再开第二条——它接续同一条
  线程（清停止位、换代、继续采当刻订阅），所以"同一个 provider 两个 worker 同时在跑"
  不会发生，也不会"重开这一段之后永远没有读数"。收口（关掉信息槽 / 折叠到看不见 / 细条态）
  也**保留采样器本体**：重新启用时复用的还是同一个实例、同一条线程。
- **慢读怎么处理（诚实版）**：单次 `read` 超过 2 秒记一次慢读；连续 2 次即**熔断**这一段
  （此后不再调它的 `read`），快照如实报"已停止采样这一段"，胶囊回到 `--`、卡片写原因。
  **不声称能杀掉它**——Python 杀不掉阻塞中的线程；那条线程被卡住期间其余段会一起延后，
  但界面不受影响（它从不取值）。重新订阅（关掉再打开这一段）视为新序列，熔断记录清除。
- **0 订阅 = 零开销**：没人看的段不采样，一次 provider 调用都不发生。
- **看不见就不采**：折叠进 `+N`、停靠细条、岛隐藏、关闭窗口、在这一段被停用时都退订。
- **迟到结果不复活（两道代次）**：快照带 `epoch`（换线程 / `stop()` 换代）与**每段
  `token`**（退订 → 重订自增）。退订前那次在飞的读数即使带着当刻 `epoch`、状态还是
  `ok`，也会被 `token` 丢弃——否则它会被宿主当成"新序列的第一拍"收下并推进告警。
  重复订阅（宿主每轮刷新都会重放）**不**自增 token，那是常态而不是新序列。
- **来源变了就换代码**：解析结果与 `provider` 文件的 `(路径, mtime_ns, size)` 绑定。
  清单一字不改、只把 provider **删掉 / 装回来 / 整体改写**，下一次采样就会丢掉旧解析
  重新导入（删了 → 那一段如实报"组件暂缺"；换了内容 → 跑新代码）。声明扫描本身也在
  缓存命中时核对同一版本，所以"装回来了、岛却一直不订阅"不会发生。边界（不假装能看
  见）：改写后 `mtime_ns` 与 `size` **完全相同**时看不出来——组件请按"写临时文件 +
  原子替换"发布。宿主内部的 provider 模块缓存按路径只留最新版本、总量有上限
  （不随作者的编辑次数增长）。
- **四态与"不伪造读数"照旧**：还没采到第一拍 → `sampling`（`--`）；取值/格式化失败 →
  `error`（`--` + 卡片原因）；真读数 `0` 就是 `0`（**不是** `--`）。

### 准入口径（哪些 id 能写进配置）

`island_metrics` 的合法成员 = **内置段 ∪ 声明清单里的组件**（`pet/config.py::_clean_island_metrics`；
内置段全集见 `island_segments.BUILTIN_SEGMENT_IDS`，本文不写死个数——每加一个内置段就该
改一遍的计数迟早变假）。
它**刻意不看运行期注册表**：否则"这条配置合不合法"就取决于进程内谁先注册，主进程与独立设置
进程可能各存一份结果（设置进程没有那些注册，会在保存时把用户的偏好清掉）。于是：

- **能显示**与**能写进配置**是两件事：`register_segment` 让段能呈现、能采样；能不能写进
  配置由清单决定。两者分离，配置保存因此不依赖进程内的注册顺序。
- **组件暂缺**（清单在、`provider` 文件不在）：id 仍然合法，**用户的偏好留着**，但既不
  采样也不在胶囊上占位——卡片写清"哪一份文件不在"。文件装回来后**下一次扫描**即恢复
  （配置刷新 / 岛显示 / `refresh_segments()` 都会触发重扫）；产品**没有目录监视器**，
  所以"文件落下去的那一刻"到"下一次扫描"之间不会自己发现，这是设计边界不是缺陷。
- **停用 ≠ 卸载**：停用 = 从 `island_metrics` 里去掉（保留声明与偏好、不采样、不撤阈值）；
  卸载 = 删掉组件目录（撤声明、撤阈值、清订阅，不留第二真值）。
- 设置页的组件行与内置指标行**同层级、同风格**（同一个 `SettingRow`、同一个可见性依赖），
  顺序来自清单扫描序，不来自进程内的注册顺序。

## 九、红线

1. **不碰 QPainter**：段契约层零 Qt；第三方拿到的只有数据（`format_value` 收裸值、
   返回串），没有画笔、没有窗口、没有字体对象。
2. **采样值永不进宽度键**：盒宽只认 `max_text` 与 label。你的格式化函数返回什么
   都不改几何。
3. **不伪造读数**：没数据源/取值失败 → `error`；还没采到 → `sampling`。两者都不是 0。
4. **`max_text` 必填**：宁可注册失败，也不许让岛宽跟着读数跳。
5. **单行**：label / 单位 / 读数 / detail 都禁换行（换行会撑破 44px 的行高预算）。
6. **不是沙箱**（对第三方与安装者都重要）：组件是本机**可信代码**，`provider` 在本应用
   进程里执行，权限与本应用完全相同。宿主保证的是"一个组件的坏读数 / 慢读 / 死循环
   不会拖垮界面"，**不是**"隔离恶意组件"；组件只从 §八 的那两个可信目录来，
   宿主**不下载、不远程执行、不扫其他目录**。
