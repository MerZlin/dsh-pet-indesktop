# 菜单悬浮响应修复报告

基线：2026-10-22 当前工作区，macOS 26.6.2 / arm64 / Python 3.12.10 /
PySide6 6.12.0。范围是菜单交互策略安装时机；未新增持久配置、依赖或菜单项。
本文记录本次实测快照，不代表 Windows/Linux 或已安装桌宠构建的验收结果。

## 一、问题与目标

用户录屏中，鼠标已经移到其他父菜单选项，高亮仍停留在 Agent 联动，右侧
子菜单也暂留；Agent 子菜单内的普通选项则立即跟随。两者均为 QMenu/QAction。
父菜单在子菜单展开后进入 Qt 的路径保护，而叶子选项不建立自己的子菜单保护。

Qt 6.12 的 QMenuPrivate::init 在构造时缓存子菜单展开等待和 sloppy 状态；
StyleChange 不重新初始化该状态。原实现创建整棵菜单后才递归 setStyle，
styleHint 查询能得到 60/120ms，但不能证明缓存已生效。

目标：高亮在鼠标移动事件内更新；保留 60ms 子菜单展开等待和 120ms 路径保护；
保持非菜单控件的样式查询语义及现有菜单外观。

相关历史：[菜单重构记录](CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md)。
源码依据：[Qt 6.12 qmenu.cpp](https://github.com/qt/qtbase/blob/v6.12.0/src/widgets/widgets/qmenu.cpp)
与 [qmenu_p.h](https://github.com/qt/qtbase/blob/v6.12.0/src/widgets/widgets/qmenu_p.h)，
本次定位已读取；不要将创建后的 styleHint 值当作实际时序验证。

## 二、修改文件说明

增删行数按本次工作区差异统计；新增文件单列，报告自身不参与业务代码统计。

| 文件 | 增/删 | 改了什么与原因 |
|---|---:|---|
| `pet/context_menus/menu_styles/common.py` | +17/-20 | 应用启动时安装一个共享代理，只覆盖 QMenu 参数，避免构造后修改无法刷新缓存；重复安装不叠加代理。 |
| `pet/app.py` | +3/-0 | 主进程创建 QApplication 后立即安装，先于任何菜单。 |
| `pet/__main__.py` | +3/-0 | 独立设置进程执行相同初始化，不导入 pet.app。 |
| `pet/context_menu.py` | +2/-3 | 移除建树后的安装调用，更新流水线说明。 |
| `pet/sprite_menu_facade.py` | +0/-2 | overlay 菜单删除同一过期安装调用与导入。 |
| `tests/test_desktop_pet_features.py` | +3/-1 | 原样式、滚动测试改为创建菜单前初始化。 |
| `tests/test_overlay_instance_gate.py` | +4/-0 | 假 QApplication 启动门测试隔离真实 Qt 样式操作；真实初始化由独立进程回归覆盖。 |
| `tests/test_menu_response.py` | 新增 142 行 | 两个实际入口、真实 Qt 事件循环与鼠标事件；断言同级/叶子高亮、展开等待、键盘导航、非菜单委托及重复安装。 |
| `docs/CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md` | +8/-0 | 文首链接当前交互策略，明确递归安装为历史实现。 |
| `docs/INDEX.md` | +3/-1 | 更新菜单阅读条件并登记本报告。 |
| 本报告 | 新增 367 行 | 记录根因、实测、验证范围与回滚。 |

没有删除文件。QSS、菜单注册表、配置格式及角色资源未修改。

## 三、实现要点

- 在 QApplication 创建后安装 ResponsiveMenuStyle，基类使用当前 app.style()，
  由 Qt 接管生命周期；不改变当前平台的基础样式。
- 仅当 widget 是 QMenu 且请求命中六个菜单参数时返回覆盖值；其他请求委托基类。
- 参数表为类常量，避免每次查询重复创建字典；进程只保留一个代理，菜单不再各自创建。
- 行为回归每例独立子进程，避免 QApplication 全局样式污染其他测试；只有平台样式
  输入和启动退出边界使用替身，QMenu、事件循环与命中判断均使用真实 Qt。

## 四、性能分析

环境见文首。计时为本机 wall clock，包含 Qt/Python 桥接与调度，不能解读为屏幕
像素显示延迟。原生探针启用 tracemalloc，微基准表则在样式查询计时前关闭它。

### 用户路径

命令（先将附录 A 保存为 `/tmp/pet-menu-native-probe.py`）：

```sh
env QT_QPA_PLATFORM=cocoa PYTHONPATH=. .venv/bin/python /tmp/pet-menu-native-probe.py before
env QT_QPA_PLATFORM=cocoa PYTHONPATH=. .venv/bin/python /tmp/pet-menu-native-probe.py after
```

脚本每组 3 次：旧顺序在菜单创建后递归安装代理，新顺序在 QApplication 创建后
安装；分别展开工具菜单，再发送指向 Agent 联动的鼠标移动，读取 activeAction。
两个模式使用同一套当前样式值与 QSS，唯一变量为安装时机。

| 指标 | 旧顺序，3 次 | 新顺序，3 次 |
|---|---|---|
| 高亮是否在鼠标事件内更新 | 全部否 | 全部是 |
| 事件后等待高亮 ms | 1072.885 / 954.305 / 952.022 | 0.003 / 0.003 / 0.003 |
| 首个子菜单展开 ms | 104.393 / 105.486 / 97.593 | 65.907 / 58.474 / 67.122 |
| 同级切换至新子菜单可见 ms | 1072.989 / 954.407 / 952.123 | 80.367 / 102.108 / 66.645 |
| 叶子高亮即时 / 禁用项未触发 | 3/3，3/3 | 3/3，3/3 |

可长期复跑的入口回归及离屏计时：

```sh
env QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -s tests/test_menu_response.py
```

首次绿色输出：main 的事件处理耗时为 0.039/0.037/0.038ms，settings 为
0.039/0.033/0.035ms。测试判断同步 activeAction，不以很窄的毫秒阈值判断通过。

### 稳态、启动与内存

```sh
env QT_QPA_PLATFORM=offscreen PYTHONPATH=. .venv/bin/python /tmp/pet-menu-cost-probe.py before
env QT_QPA_PLATFORM=offscreen PYTHONPATH=. .venv/bin/python /tmp/pet-menu-cost-probe.py after
```

附录 B 的脚本用 QStyleFactory 的 macOS 样式，创建销毁菜单每批 200 个、5 批取中位；
非菜单查询为 QPushButton 的 SH_Widget_Animate，每批 10000 次、5 批取中位。
内存预热后再创建销毁 1000 个菜单，gc.collect 后读取 tracemalloc 与有效 Qt 代理数。

| 指标 | 旧顺序 | 新顺序 |
|---|---:|---:|
| 进程级安装 ms，1 次 | 无 | 0.0373 |
| 菜单创建销毁 µs/个，5×200 | 38.125 | 33.961 |
| 非菜单样式查询 µs/次，5×10000 | 0.7664 | 1.0116 |
| 1000 菜单后的 Python 保留字节差 | +2876 | +2812 |
| 批次前后存活代理数 | 0→0 | 1→1 |

1. 稳态新增成本是全局样式请求经 Python 代理转发；本样本非菜单查询增加约
   0.2452µs/次。没有新增轮询或周期定时器，不能据此推断完整应用 CPU 百分比。
2. 新增安装路径每个 GUI 进程一次，实测约 0.0373ms；菜单创建销毁本样本减少
   约 4.164µs/个，主要来自取消逐菜单代理分配。
3. 产品路径没有新增网络、磁盘访问、系统调用或线程；使用原有 Qt 事件与计时器。
4. 进程常驻一个代理对象及一个固定参数字典，取消每个菜单的代理；样本没有代理数
   随菜单创建增长。tracemalloc 仅覆盖 Python 分配，没有测量完整 RSS 或 Qt 原生堆。

附录提供本轮探针源码，后续跨机复测应重新采样；自动回归以仓库里的入口测试为准。

## 五、实机运行记录

本机 Cocoa 原生菜单已运行，使用一个独立 QWidget 宿主、实际 QMenu 弹窗与合成鼠标
事件，不启动桌宠、不读写真实用户配置。QPA 输出为 cocoa，基础样式为 QMacStyle。
根因和用户行为结果见上节 3 组原始数字；边界是 disabled QAction 收到释放事件后
3/3 未触发。原生探针读取的是 Qt 活跃项与子菜单可见性，不等同于物理鼠标端到端延迟。

首次无宿主探针失败：`AssertionError: menu timeout`。进一步输出显示
`qpa cocoa root False screen (0, 0, 2560, 1440)`；菜单在未激活应用中已经隐藏。
补齐独立宿主窗口并等待 ApplicationActive 后，原生菜单保持可见，两组均完成测量。

已检查 Cocoa grab 截图：根菜单 122×62，Agent 子菜单 135×86；旧/新两组尺寸相同，
RGB 像素差的包围盒均为 None。确认高亮、箭头与禁用项表现；截图只覆盖该最小菜单。
本机证据文件：`/tmp/pet-menu-native-{before,after}-{root,agents}.png`。

未进行完整桌宠应用菜单路径、物理鼠标操作或 Windows/Linux 原生验证；未重启用户
现有桌宠进程。以上原生探针通过不替代这些尚未完成的验收项。

## 六、测试与验证

| 检查 | 命令/方法 | 结果 |
|---|---|---|
| 红灯 | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_menu_response.py` | 修改前 2 failed，均失败于同级高亮未立即更新。 |
| 入口行为 | 同上，修改后加 `-s` | 2 passed，每入口 3 次；叶子、键盘、展开等待同时通过。 |
| 相关回归 | menu_response、context_menu_modern_style、desktop_pet_features、overlay_instance_gate、settings_process_isolation、animation_menu_close_and_group、sprite_menu_parity、context_menu_lifecycle | 191 passed，25.80s。 |
| 满载时序 | `test_menu_response.py` 与 `test_animation_menu_close_and_group.py`；10 个 `while True: pass` 子进程满载，finally 终止 | 连续 3 遍：14 passed / 6.41s；14 passed / 5.82s；14 passed / 5.62s。 |
| Ruff | `.venv/bin/python -m ruff check pet tests scripts` | All checks passed。 |
| 全量 | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` | 8835 passed, 82 skipped, 20 warnings，1095.19s；无失败。 |
| 文档与样式 | `tests/test_pr_report_discipline.py tests/test_context_menu_modern_style.py` | 230 passed，0.79s。 |
| 差异 | `git diff --check` | 通过。 |

全量因涉及应用启动、全局样式与多测试域而运行。
20 条警告来自未修改的路径：18 条 Qt API 弃用警告（mirrored、QHoverEvent、
QDragEnterEvent），2 条 HDR float16 转换 RuntimeWarning；跳过项按套件原有条件保留。

## 七、风险与回滚

应用级代理会经过全部样式查询，但只覆盖 QMenu 的指定参数；范围包括本进程以后
创建的原生 Qt 菜单。非菜单委托已断言，完整界面的平台兼容性仍以对应平台运行结果
为准。当前没有运行期替换 QApplication 样式的其他路径；未来增加主题引擎时需保留
这层菜单策略。

没有配置迁移或文件落盘；回滚本次代码并重启 GUI 进程即可。修改只有在重启后才能
覆盖已经创建并缓存旧参数的菜单。

## 附录 A：原生响应探针

仅用于独立菜单诊断；需要可用的 macOS 图形会话。`before` 复现旧安装顺序，
`after` 使用产品初始化。保存后按第四节命令运行，窗口在测量后随进程退出。

```python
import json
import platform
import statistics
import sys
import time
import tracemalloc

import PySide6
import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QPushButton, QWidget
from pet.context_menus.menu_styles.common import ResponsiveMenuStyle, install_responsive_menu_style, install_stay_open_interaction
from pet.context_menus.menu_styles.modern import apply_modern_menu_style

mode = sys.argv[1]
app = QApplication([])
base_name = app.style().metaObject().className()
owner = None
if app.platformName() == 'cocoa':
    owner = QWidget()
    owner.setWindowTitle('菜单交互验证')
    owner.resize(200, 100)
    owner.show()
    owner.raise_()
    owner.activateWindow()
    deadline = time.perf_counter() + 3
    while app.applicationState() != Qt.ApplicationState.ApplicationActive and time.perf_counter() < deadline:
        app.processEvents()
tracemalloc.start()
start = time.perf_counter()
if mode == 'after':
    install_responsive_menu_style(app)
install_ms = (time.perf_counter()-start)*1000

class HoldPointer(QObject):
    def eventFilter(self, watched, event):
        return event.type() in (QEvent.Type.Enter, QEvent.Type.Leave)

def pump_until(predicate):
    start = time.perf_counter()
    while not predicate() and time.perf_counter()-start < 5:
        app.processEvents()
    assert predicate(), 'menu timeout'
    return round((time.perf_counter()-start)*1000, 3)

def move(menu, action):
    p = menu.actionGeometry(action).center()
    for _ in range(3):
        QApplication.sendEvent(menu, QMouseEvent(QEvent.Type.MouseMove, p, menu.mapToGlobal(p), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

def late_style(root):
    for action in root.actions():
        if action.menu():
            late_style(action.menu())
    proxy = ResponsiveMenuStyle()
    proxy.setParent(root)
    root.setStyle(proxy)
    root._responsive_menu_style = proxy

results = []
for sample in range(3):
    root = QMenu(owner)
    tools = root.addMenu('工具与帮助')
    tools.addAction('检查更新')
    agents = root.addMenu('Agent 联动')
    claude = agents.addAction('Claude Code')
    cursor = agents.addAction('Cursor')
    disabled = agents.addAction('不可用选项')
    disabled.setEnabled(False)
    triggered = []
    disabled.triggered.connect(lambda: triggered.append(True))
    for m in (root, tools, agents):
        apply_modern_menu_style(m, {'theme': 'dark'})
    if mode == 'before':
        late_style(root)
    install_stay_open_interaction(root)
    root.popup(QPoint(100, 100))
    app.processEvents()
    p = root.rect().center()
    QApplication.sendEvent(root,QEnterEvent(QPointF(p),QPointF(p),QPointF(root.mapToGlobal(p))))
    guard=HoldPointer(root)
    root.installEventFilter(guard)
    move(root, tools.menuAction())
    opening_ms=pump_until(tools.isVisible)
    move(root, tools.menuAction())
    start=time.perf_counter()
    move(root, agents.menuAction())
    immediate=root.activeAction()==agents.menuAction()
    highlight_ms=pump_until(lambda:root.activeAction()==agents.menuAction())
    shown_ms=pump_until(agents.isVisible)
    total_ms=(time.perf_counter()-start)*1000
    move(agents, claude)
    move(agents, cursor)
    leaf_immediate=agents.activeAction()==cursor
    app.processEvents()
    if sample==0:
        root.grab().save('/tmp/pet-menu-native-'+mode+'-root.png')
        agents.grab().save('/tmp/pet-menu-native-'+mode+'-agents.png')
    p=agents.actionGeometry(disabled).center()
    QApplication.sendEvent(agents,QMouseEvent(QEvent.Type.MouseButtonRelease,p,agents.mapToGlobal(p),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
    assert not triggered, 'disabled action must not trigger'
    results.append({'opening_ms':opening_ms,'highlight_immediate':immediate,'highlight_wait_ms':highlight_ms,'total_switch_ms':round(total_ms,3),'leaf_immediate':leaf_immediate,'disabled_triggered':bool(triggered)})
    root.close()
    shiboken6.delete(root)
    app.processEvents()

button=QPushButton()
hint=QStyle.StyleHint.SH_Widget_Animate
cost=[]
for _ in range(5):
    start=time.perf_counter()
    for _ in range(10000):
        app.style().styleHint(hint,None,button)
    cost.append((time.perf_counter()-start)*1e6/10000)

def batch():
    start=time.perf_counter()
    for _ in range(200):
        menu=QMenu()
        if mode=='before':
            late_style(menu)
        shiboken6.delete(menu)
    return (time.perf_counter()-start)*1e6/200

creation=[batch() for _ in range(5)]
current,peak=tracemalloc.get_traced_memory()
print(json.dumps({'mode':mode,'platform':platform.platform(),'python':platform.python_version(),'qt':PySide6.__version__,'qpa':app.platformName(),'base_style':base_name,'install_ms':install_ms,'samples':results,'nonmenu_stylehint_us_median':statistics.median(cost),'menu_create_destroy_us_median':statistics.median(creation),'python_current_bytes':current,'python_peak_bytes':peak},ensure_ascii=False))
```

## 附录 B：创建、样式转发与 Python 分配探针

保存为 `/tmp/pet-menu-cost-probe.py`，按第四节命令运行。

```python
import gc
import json
import platform
import statistics
import sys
import time
import tracemalloc

import shiboken6
from PySide6 import __version__
from PySide6.QtWidgets import QApplication,QMenu,QStyle,QStyleFactory,QPushButton,QProxyStyle
from pet.context_menus.menu_styles.common import ResponsiveMenuStyle,install_responsive_menu_style

mode=sys.argv[1]
app=QApplication([])
app.setStyle(QStyleFactory.create('macOS'))
tracemalloc.start()
start=time.perf_counter()
if mode=='after':install_responsive_menu_style(app)
install_ms=(time.perf_counter()-start)*1000
button=QPushButton()
hint=QStyle.StyleHint.SH_Widget_Animate

def batch():
 start=time.perf_counter()
 for _ in range(200):
  menu=QMenu()
  if mode=='before':
   proxy=ResponsiveMenuStyle();proxy.setParent(menu);menu.setStyle(proxy)
  shiboken6.delete(menu)
 return (time.perf_counter()-start)*1e6/200

batch()
creation=[batch() for _ in range(5)]
gc.collect()
mem_start=tracemalloc.get_traced_memory()[0]
proxy_start=sum(isinstance(obj,QProxyStyle) for obj in shiboken6.getAllValidWrappers())
for _ in range(5):batch()
gc.collect()
mem_end=tracemalloc.get_traced_memory()[0]
proxy_end=sum(isinstance(obj,QProxyStyle) for obj in shiboken6.getAllValidWrappers())
tracemalloc.stop()
queries=[]
for _ in range(5):
 start=time.perf_counter()
 for _ in range(10000):app.style().styleHint(hint,None,button)
 queries.append((time.perf_counter()-start)*1e6/10000)
print(json.dumps({'mode':mode,'platform':platform.platform(),'python':platform.python_version(),'qt':__version__,'install_ms':install_ms,'menu_us_median':statistics.median(creation),'nonmenu_hint_us_median':statistics.median(queries),'retained_python_bytes_after_1000_menus':mem_end-mem_start,'live_proxy_before_after':[proxy_start,proxy_end]}))
```

## 合并基线验证

在原项目与 fork 的 `main` 提交 `4079116` 基础上应用本次菜单修复后验证；环境同文首。
本轮采用 `QT_QPA_PLATFORM=offscreen`、`PYTHONDONTWRITEBYTECODE=1`，pytest 加
`-p no:cacheprovider`。

| 检查 | 命令/方法 | 结果 |
|---|---|---|
| Ruff | `.venv/bin/python -m ruff check pet tests scripts` | All checks passed。 |
| 相关回归 | 第六节相关八个测试文件与 `test_pr_report_discipline.py` | 420 passed，30.51s。 |
| 全量 | `.venv/bin/python -m pytest -p no:cacheprovider -q` | 8839 passed, 82 skipped, 21 warnings，1145.06s。 |
| 满载时序 | 10 个 CPU 负载进程下连续运行 `test_menu_response.py` 与 `test_animation_menu_close_and_group.py`，每轮使用上述 pytest 参数 | 三轮均 14 passed；7.50s / 7.56s / 14.56s；负载进程已回收。 |

本轮验证覆盖合并后的本地测试门禁；原生平台运行与视觉证据范围仍见第五节。
