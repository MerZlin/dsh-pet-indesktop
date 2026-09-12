# PR 合并三则教训（2026-09-12 实测记录）

> 来源：2026-09-11 ~ 09-12 连续合并 PR #97–#109 的实机过程（本地验证 + 三平台 CI 实测）。
> 每条都给出**现象 / 根因 / 处置 / 预防 / 证据**，证据一律是可复现的命令或 CI run。
> 目的：把这三处"看起来像运气、其实是规律"的坑固化成合并前的检查动作。

---

## 教训 1：叠放 PR —— squash 掉父 PR 后，子 PR 一定在冲突边缘

**现象**
#104（父，桥接插件归零外部依赖）以 squash 合并为 `7f3b896` 后，#105（正文写明"基于 #104 叠放"）从 `clean` 直接变成 **`dirty` 冲突**。冲突文件恰好是 #105 的后续提交又改过的那 3 个：`.github/workflows/pr-test.yml`、`docs/ACCEPTANCE_TESTS.md`、`integrations/dsh-pet-bridge/verify_import.mjs`。

**根因**
squash 合并会在 main 上生成**内容相同但提交不同**的新提交 `A'`。子分支里是「原提交 `A` + 后续微调」。三方合并时 base→ours 与 base→theirs **都修改了同一批行**，git 无法判断 `ours ⊆ theirs`，于是报冲突。

**处置（本次做法）**
1. 本地 `git merge origin/pr/105`（不 commit），列出冲突面；
2. 逐个冲突文件先验证「子 PR 版本是父 PR 版本的**加强**而非回退」——本次三处分别是：bridge 契约测试 5→7 个、动态 import 从"仅禁外部"升级为"一律禁止"、验收命令同步；
3. 确认后**取子 PR 版本**（`git checkout --theirs`），因为子分支构造上包含父 PR 全部改动；
4. **树等价校验**：`git diff origin/pr/105` 为空 ⇒ 合并结果与作者分支逐字节一致、父 PR 内容未丢；
5. push 合并提交：GitHub 仍会把 PR 记为 **Merged**（实测 #105 `merged_at=2026-09-12T06:42:05Z`，`merge_commit_sha=3f96c1a`）。

**预防**
- 看到 PR 正文出现「基于 #X 叠放 / 本 PR 合并后 rebase 即只剩自身 diff」就**预期冲突**，不要相信合并前那一刻的 `mergeable: clean`；
- 优先请作者 rebase；如需维护者落地，按上面 5 步走；
- **冲突时绝不机械取 ours**——若两侧互不相干（例如 #108 与 #109 同改 `window.py` 但不同代码块），取 ours/theirs 都会丢改动，必须真合并后核对行数与功能测试。

**证据**
`#104 → 7f3b896`（squash）→ `#105 → 3f96c1a`（本地合并提交，PR 状态 merged）；对照案例：`#108 → e5cd186`、`#109 → 7770740` 两者同改 `window.py` 但 git 自动合并成功（未冲突）。

---

## 教训 2：红线/预算类改动 —— 多个 PR 同期加行时，先预校准再合最后一个

**现象**
#108 给 `pet/window.py` 加 118 行，并在自己的 PR 里把行数预算校准到 4420；#109 又加 13 行、没抬预算。两者**各自合并时 CI 都绿**，但合到一起后 main 的 `window.py = 4425 > 4420` → 中间态提交 `7770740` 三平台全红：

```
AssertionError: window.py 涨到 4425 行（预算 4420）
FAILED tests/test_architecture.py::test_window_py_line_budget
```

**根因**
行数红线校验的是**合并后的树**，而每个 PR 的 CI 只对自己分支的状态负责。两个各自合规的 PR 叠加即可越线——**红线是"组合性质"，不是"单元性质"**。

**处置**
按仓库既有惯例把预算校准到实测值（带日期注释、写明增量来源）：

```python
# 2026-09-12 再上调到 4425：#109 探头/头槌体验三连修 + 气泡分页避头尾在同一批落地
# （window.py +13，实测 4425）…按维护者约定 klxxya 的修复可越过本红线，预算仍只随实测校准。
WINDOW_PY_LINE_BUDGET = 4425
```

**预防**
- 合并前先算 `window.py(main) + Σ(各 PR 的文件增量)`（`git show <ref>:pet/window.py | 行数`，注意别用会丢行的粗糙计数）；
- 若会越线：**先把校准提交推上去（预校准），再合"会越线"的那个 PR**——这样 main 全程无红。本仓库 #101 那次就是这么做的，对比鲜明；
- 同样适用于 `modern_settings_dialog.py` 行数预算，以及任何"只许降不许涨"的门禁；
- 反例警告：**不要**为了过关去压缩行宽/合并语句/删注释（测试失败信息里也明写了这是"反向优化"）。

**证据**
CI run `34694291483`（`7770740` 三平台红于 `test_window_py_line_budget`）→ 校准提交 `4b921dd` → run `34694487069` 架构转绿。

---

## 教训 3：新测试的时序写法 —— 固定 sleep 猜时序会在 macOS CI 确定性红

**现象**
#109 新增的 `tests/test_speech_bubble.py::test_paged_bubble_flip_fades_and_updates_dots` 在 macOS CI **连续两轮**失败：

```
FAILED … - assert <PySide6.QtCore.QPropertyAnimation(0x…) at 0x…> is None
1 failed, 1853 passed, 12 skipped
```

而 windows/ubuntu 绿、**本机 5/5 绿**、作者 PR 自己的 macOS CI 当时也绿。

**根因**
测试用固定 `QTest.qWait(150)` 猜「淡出 → 换字 → 淡入」的完成时刻，并断言内部句柄 `bubble._page_fade is None`；macOS 上 1ms 动画的 `finished` 派发会晚于该窗口。产品实现其实是防御性的（`fade_in.finished → _fade_in_done` 带 `is fade_in` 守卫 + `DeleteWhenStopped`），失败信息里文本与圆点页码**已经更新**，证明翻页链路本身没坏——坏的只是"测试的计时方式"。

**处置**
改为**轮询目标状态**（`_wait_until`：5s 预算 / 10ms 步进），**四条断言全部保留**（强度不变），失败时额外打印 `_page_fade` / `opacity` 便于日后定位。提交 `f6db3cd`，macOS CI 复验转绿（run `34694930365` 三平台成功）。

**预防（与 AGENTS「CI 优先纪律」同源，本次再次验证）**
1. 涉及真实动画 / Qt 事件循环 / 线程的断言：一律"**轮询状态变化 + 宽预算**"，禁止固定 sleep 猜时序；
2. **不要把内部句柄的生命周期当作主断言**：先断言用户可见结果（文本、页码、透明度），句柄收尾只作补充并给足预算；
3. 新测试写完**先在最慢平台验证**（本项目 = macOS CI），别"本地绿就合"；
4. **区分"环境性假红"与"本次引入的红"**：本次 `tests/test_proactive.py::…test_foreground_window_info_real_call_no_shadow_bug` 在本机失败（真实前台窗口调用，函数开头 `ctypes.windll` 抛 `UnboundLocalError` 被 except 吞掉 → `info is None`）。判定手法：`git worktree add <tmp> <合并前 sha>` 在**同一台机器**上跑同一用例——它在合并前也失败 ⇒ 与本 PR 无关（该用例在后续全量运行中自行转绿，进一步印证随桌面状态漂移）。

**证据**
CI run `34694487069`（macOS 两轮均红于同一用例）→ `f6db3cd` → run `34694930365` 三平台绿。

---

## 附 A：合并前/中/后的可复用检查动作

**合并前（预测）**
1. `gh api repos/<owner>/<repo>/pulls/<n> --jq '{mergeable,mergeable_state,files,commits}'`
2. 判断叠放：`git rev-list --count origin/pr/<child>..origin/pr/<parent>` 与反向计数（0 表示包含关系）
3. 逐 PR 看文件增量，**合计受预算文件的行数**（教训 2）
4. 看新增测试里有没有固定 sleep / 断言内部句柄（教训 3）

**合并中（处置）**
5. 冲突时先判定关系：`ours ⊆ theirs`（可取 theirs）／互不相干（必须真合并）／部分重叠（逐块核对）
6. 直接落地（无法走合并按钮）时，保留 `Co-authored-by` 署名

**合并后（对账）**
7. **树等价校验**：本地按顺序合并各 PR head，与 main `git diff` 应为空
8. 本地三道门：`ruff` → 全量 `pytest` → 受影响聚焦测试
9. CI 三平台；若出现中转红，确认是否是"预测到的红灯"（如红线校验）并及时补校准提交

## 附 B：本轮实证到的有效防线（值得保留）

- **构建期红线真的能拦事故**：`scripts/fix_bridge_bundle.py` 的"dist 清单零依赖"校验，正确地**拒绝**了本机一个仍带 9 个依赖的旧 bundle（退出码 1）——这说明"在构建期把事故型回归判红"是有效手段，而不是形式主义。
- **PR 门禁新增桥接零依赖双层校验**（`node --test` 7 个契约测试 + `verify_import.mjs` hermetic 冒烟）在引入后首次 CI 即覆盖到 3 平台。
- **事件同步的用例在引入后未再复现平台差异**：改为轮询的 `test_paged_bubble_flip_fades_and_updates_dots` 在 macOS 上连续 1 次 CI 通过（如需进一步加固，可考虑再跑 3 轮 CI 观察）。
