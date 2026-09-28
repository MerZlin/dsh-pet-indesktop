# AI 协作复盘（2026-09-29）：一次完整改造里踩过的坑

> **背景**：一次通宵改造——免打扰 + 今日汇总（`c7c9bac`）、天气（`7b62e05`）、
> 完整检出后的对照补记（`3611672`）、修掉真实桌面上的 9 个测试失败（`5c31a48`）。
> 过程中踩了 20 个坑，本文按类整理，每条给「现象 / 根因 / 现在的做法」。
> **给后来的 agent（含未来的我）看的**——尤其第 1、2、3 条，是花了用户时间的那种。
>
> 最终状态：用户本机全量 `3027 passed, 9 skipped, 0 failed`。

## 一、最该改的五条

1. **别把「环境不全」当背景噪音**：稀疏检出没补 `assets/` 与 `docs/` 就交付"可跑的测试"，
   用户连跑三次全是 `FileNotFoundError`。**交付前自己先跑通一次**，跑不通就先说差什么。
2. **动有契约测试的框架，先照抄现成同类实现**：菜单里「免打扰」第一版写成**空 submenu**，
   被 `resolve_menu_layout()` 过滤 → 菜单里根本不会出现。同类实现是「播放动画」
   （模板放 action 节点、子菜单由注册表动态生成）。
3. **改一处、扫一片**：动菜单模板 / 配置键 / 设置行 / 公开文案，必须同批更新对应断言
   （根节点 id 序列、`registered` 集合、配置快照、互动域标签表、文案红线）。
4. **归因逐条看 traceback**：曾把图片抽屉用例的 `FileNotFoundError`（Windows `MAX_PATH`）
   误报成"桌面几何问题"；报告里每个根因都要有原始输出支撑。
5. **长测试先问再跑**：全量一次约 4 分钟，反复跑是烧用户时间。默认只跑聚焦集 + 相邻文件。

## 二、网络与 TLS

1. **schannel 别硬试**：`Invoke-WebRequest` / `curl.exe` 在本机报
   `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`，
   而 node 的 OpenSSL 一次就通。→ HTTPS 优先用 node / Python 的 TLS 栈，报这个错立刻换路。
2. **先试镜像**：`registry.npmjs.org` 反复 `ECONNRESET/ETIMEDOUT`，换 `registry.npmmirror.com`
   十秒拉完；PyPI 同理（阿里云镜像可用，清华镜像当时 403）。→ 第一步并行探 2~3 个源的延迟。
3. **别把长任务输出憋住**：`... | Select-Object -Last 8` 会缓冲整条流，几十分钟看不到进度。
   → 写日志再 `Get-Content -Tail`，或让输出流式返回。

## 三、检出与环境

1. **别一上来整仓克隆**：仓库大头是 `assets/`（约 70 MB 素材），整拉易被中断
   （`RPC failed` / `early EOF`）。→ `git clone --depth 1 --filter=blob:none --sparse`，
   再按需 add `pet tests scripts packaging integrations docs`。
2. **稀疏检出必须补齐素材与文档**：测试要建 `MovieLibrary`，缺
   `assets/characters/<角色>/videos/` 会成片 `FileNotFoundError`。
   线上素材还可以从已安装产物 `_internal/assets/` 直接拷（本机实测 148/149 个文件一致）。
3. **sparse 重放会清掉手工放进来的文件**：`git sparse-checkout add/set` 之后，不在定义里的
   路径会被删（本次丢了 `docs/` 下两份报告）。→ 手工补的文件要么写进 sparse 定义，
   要么放检出范围外。
4. **临时目录命名要收敛**：为了绕开 `pytest-of-<user>` 的 ACL 残留，每次换新名字，
   结果仓库根目录堆了一串 `.tmp-*`。→ 固定一个名字（如 `.tmp`），收尾明确告知可删。
5. **批量失败先跑单文件**：`.pytest-tmp` 里连建子目录都被拒，一次性 1494 个 error，
   看着像"产品大面积坏了"。→ 先最小复现，再谈产品。

## 四、功能实现与契约测试

1. **空 submenu 会被过滤**（见「最该改的五条」第 2 条）：`resolve_menu_layout()` 会丢弃
   没有可见子项的子菜单；模板层放 **action**，子菜单交给注册表的 builder 动态生成。
2. **契约断言同批更新**：`tests/test_menu_layout.py`（根节点 id、`registered` 集合、解析结果、
   QMenu 渲染序列）与 `tests/test_desktop_pet_features.py`（根菜单文本序列）都是外部契约；
   `tests/test_settings_interaction_tabs.py`（互动域标签表）同理。
3. **配置键四件套**：新增键要同步 `_default_base()`、reload 白名单、
   `DEFAULTS_SNAPSHOT` / `RELOAD_WHITELIST_SNAPSHOT` 与文件头统计数字。
4. **行数预算先量再动**：`window.py` 与 `modern_settings_dialog.py` 卡在预算上限
   （4671 / 2392）。新增设置一律「控件与行放新模块（`settings_*`）+ 对话框只留接线」，
   预算按实测校准并在常量上方写理由。

## 五、报告与交付纪律

1. **仓库内文本不许出现外部品牌词**：`test_product_copy_has_no_external_brand_reference`
   会扫 `pet/ tests/ docs/ README.md`；本次是报告里抄了上游分支名而变红。
   绝对路径同理（示例里用 `<仓库路径>`）。
2. **只引用仓库内文件**：报告里指向会话目录的相对链接是悬空的。
3. **三份证据的数字要刷新**：同一份报告里的失败计数随每轮变化（67 → 14 → 9 → 0），
   收尾必须回头统一；并在 `docs/INDEX.md` 登记（新文档入场规则第 1 条）。

## 六、测试归因与修复

1. **异步断言要有界等待**：reader 由 `clip.start()` 之后异步拉起，紧接着断言会抢跑。
   → 用 `_wait_until(pred, timeout)`（同 `tests/test_agent_link_threads.py` 惯例）。
2. **"同类"不等于同一根因**：9 个真实桌面失败里，6+1 是**测试假屏太小**
   （`1920×1200` 装不下 309×259 的身体框 → 目标 (1874,1088) 被贴边钳到 (1611,941)），
   1 是 **Windows MAX_PATH**（`"very-long-image-name-" * 8`），
   1 是**线程抢跑**。→ 逐条看 traceback 再写报告。
3. **假屏尺寸是隐式契约**：放大到 `4096×3072` 后所有被测目标都落在边界内；
   另需在测试里关掉 `window_placement.desktop_area()`（#186 的多屏快照会绕过假屏，
   多显示器/远程桌面下必红）。多屏钳位语义由 `tests/test_edge_reachability.py` 负责。

## 七、协作与沟通

1. **留在用户机器上的东西要主动列清单**：临时目录、基线工作树、venv、补进来的素材，
   都要说清"能不能删、怎么删"。
2. **会被用户接手的细节提前说**：提交作者（本次落成工具默认身份，用户需自行 amend）、分支与是否推送、
   `git` 的 `dubious ownership`（`git config --global --add safe.directory <repo>`）。
3. **权限/网络"以为开了"时，先给探针证据再下结论**，别猜原因。
4. **测试执行权归用户（本次会话末期定的硬约定）**：agent 不自己跑 pytest（含聚焦集），
   只写代码/用例 + 跑 `ruff`/`py_compile`，把"建议验证命令"写进交接；由用户在终端跑并回报告。
   原因：① 全量一次约 4 分钟，反复跑是烧用户时间；② 用户机器才是真实环境，
   沙箱里"禁网 + 禁写 `%APPDATA%`"跑不出真结论（本次的 click_sound 4 条就是典型）。
5. **生成的文件只许放一个地方（用户 2026-09-29 明确提出的规则）**：agent 的非交付文件
   一律放会话 `work/`，不许散落到仓库根目录。本次踩的坑很典型——仓库根目录堆了
   **24 个 `.tmp*` / `.pytest*` 目录**（每轮换一个临时目录名留下的），用户一眼就看到了。
   → 临时目录固定一个名字；测试产物（缓存、basetemp）也指到那一个目录里
   （本仓库已在 `pytest.ini` 设 `cache_dir = .tmp/.pytest_cache`，`.gitignore` 忽略 `.tmp/`）；
   收尾时清干净并列出清单。

## 八、可执行自检清单

**开工前**

- [ ] 交付物用户会怎么用？如果是"让用户去跑"，我先自己跑通一次最小验证。
- [ ] 仓库里有没有同类实现？有就先读、先照抄形状。
- [ ] 这次会碰哪些契约（菜单/配置/设置/文案）？先 `rg` 出对应断言与红线。

**写代码时**

- [ ] 新配置键：默认 / 白名单 / 快照 / 数字 四件套齐了吗？
- [ ] 新设置行：控件与行在 `settings_*.py`，对话框只留接线，预算按实测校准并写理由。
- [ ] 新公开文案：无外部品牌词、无绝对路径、不引用仓库外文件。
- [ ] 联网/异步：HTTP 出口可注入、断言有界等待。

**收尾前**

- [ ] 聚焦用例 + 1~2 个相邻文件跑绿；全量交给用户或先问。
- [ ] **不自己跑 pytest**：只跑 `ruff` 与 `py_compile`，把建议验证命令写给用户。
- [ ] **自己生成的临时文件都在会话 `work/` 与那一个 `.tmp/` 里**；仓库根目录不留 `.tmp*`、`.pytest_cache`。
- [ ] 三份证据报告（修改文件说明 / 性能分析 / 实机运行记录）+ `docs/INDEX.md` 登记。
- [ ] 报告里的每个根因都有原始输出；失败分「产品缺陷 / 测试缺陷 / 环境限制」。
- [ ] 列一份"在用户机器上留下了什么、怎么清理"。
