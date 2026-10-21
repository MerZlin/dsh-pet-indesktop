# Firefox 媒体标识识别修复（2026-10-20）

基线：上游 `main` / `147170f7a89e9cfa71e4e97966e34183087a3fc2`。
分支：`fix/firefox-media-session`。本报告为 Windows 本机验证快照，不代表三端发布验收。
前序设计：[SMTC 媒体类型过滤](PR-REPORT-SMTC-MEDIA-TYPE-2026-10-06.md)。

## 一、根因与范围

Windows SMTC 的 `source_app_user_model_id` 不保证等于进程名。本机正常安装的
Firefox 实际返回 `308046B0AF4A39CB`；HKCU/HKLM 的
`Software\Mozilla\Firefox\TaskBarIDs` 将安装路径映射到相同标识。
旧实现只认 `firefox` / `firefox.exe`，因此把 Firefox 当成表外播放器，即使
`music_browser_media_enabled=False` 仍放行。安装包内原始模块的真实执行也确认了此误判。

新实现按 Firefox 的注册记录补全身份集合，而不是硬编码一台机器的哈希，也不是
把任意十六进制字符串都当成浏览器。覆盖 HKCU/HKLM、32/64 位视图，注册键不存在或
无法访问时保留已有行为；空值、非字符串类型忽略。只读注册表，不创建/修改任何键。
进程内缓存一次，避免媒体轮询重复访问注册表；运行期间新装/移动 Firefox 后需要重启桌宠。

不新增设置，不修改配置格式、媒体传输控制、线程生命周期或其他播放器策略。
关闭现有开关时，已识别的 Firefox 不参与歌词/唱歌；开启则恢复原先允许行为。
保持原有局限：没有 SMTC 会话或只有暂停会话时，唱歌门仍按音频峰值回退；
不宣称此补丁能区分所有网页视频与音乐，也不扩展到所有 Firefox 衍生浏览器。

## 二、修改文件说明

| 文件 | 增删 | 内容与原因 |
|---|---|---|
| `pet/now_playing.py` | +44 / -1 | 缓存读取 Firefox 注册身份，接入公共浏览器判断，使歌词选择和唱歌事实探针共用正确识别 |
| `tests/test_firefox_media_session.py` | 新增 142 行 | 18 个确定性用例：两种安装 ID、两根键四视图、大小写/空白、缺失/拒绝访问、坏值、缓存、非 Windows、缺少 winreg、歌词与音频门开关、多播放器同播 |
| 本报告 | 新增 | 根因、测试、性能、冻结验证与验收边界 |
| `docs/INDEX.md` | 新增条目 | 报告入口与前序设计互链 |

## 三、测试与验证

依赖来自 `uv run --no-project --python 3.11 --with ...` 的隔离环境，未装入桌宠运行目录。
测试环境 `QT_QPA_PLATFORM=offscreen`。OS 边界使用假注册表/假 SMTC；产品选择器与唱歌门走真实代码。

1. 先加回归，旧实现 **13 failed, 4 passed**；失败包括关闭开关仍返回 Firefox 曲目、同播 Firefox 抢占未知类型播放器。
2. 修正后再补缺少 winreg 用例，相关族 **128 passed in 4.09s**：

   ```text
   python -m pytest tests/test_firefox_media_session.py tests/test_now_playing_media_type.py tests/test_now_playing_session.py tests/test_now_playing_session_probe.py tests/test_music_detect.py tests/test_lyric_sources.py tests/test_music_sing_grace.py tests/test_music_sing_timer.py tests/test_overlay_music_sing.py -q
   ```

3. `ruff check pet/ tests/` 与 `git diff --check` 已通过。
4. 整仓全量完成：**8409 passed, 12 skipped, 1 failed**，耗时 1371.36 秒。该次收集早于最后补入的缺少 winreg 用例和本报告；最终相关族与文档门禁另跑验证，不把不同批次数字相加宣称全量绿色。
5. 唯一失败为 `tests/test_overlay_instance_gate.py::test_crash_residue_lock_reclaimed_by_pid_staleness`：持锁子进程未在 60 秒内写就绪标记。修复分支独立复跑同样失败；在**未修改的上游基线 `147170f` 独立 worktree**、同解释器和依赖下复跑也以同一断言失败（61.51 秒）。这证明当前环境/上游基线同样存在此失败，不是只在 Firefox 修改后出现。未扩大范围修单实例门，也未放宽或跳过该测试；不能据此宣称上游没有问题或全量通过。

## 四、性能分析

Windows x64 / CPython 3.11.15，`timeit.repeat`，5 组重复：

| 路径 | 每组样本量 | 5 组平均耗时 |
|---|---:|---:|
| 清缓存后读取四处注册表视图 | 100 | 134.4114 µs/次 |
| 缓存后识别 Firefox 安装 ID | 100000 | 0.260813 µs/次 |
| 缓存后识别非浏览器 cloudmusic.exe | 100000 | 0.315329 µs/次 |

冷路径正常仅在进程第一次遇到非静态浏览器名称时执行一次；静态名称仍短路返回。
常态只增加一次缓存读取和集合查询，无每拍注册表 I/O，无新增线程、网络、定时器或文件写入。
缓存上限 1 项，内容为已注册 Firefox 身份的不可变集合，大小随安装记录数而不是轮询次数增长。
这是修复后实测，不声称相较旧版更快；未做进程总 RSS 对照。

## 五、实机运行记录

- 直接读取真实 SMTC 与注册表：Firefox 会话 ID 为 `308046B0AF4A39CB`；网易云为 `cloudmusic.exe`。采样时两者状态均为暂停，不声称观察到了播放中的桌面动画。
- 旧安装 EXE 的归档模块实际执行：Firefox 安装 ID 被判断为非浏览器，关闭开关仍放行；`firefox.exe` 则能被认出。
- 修复后的冻结探针使用原安装版全部依赖与修复后的同一份 PYZ，在 Windows 真机执行 **exit 0 / passed=true**。真实注册表识别成功，真实 SMTC 中 Firefox 变为 `browser=true`、网易云仍为 `false`。
- 冻结探针对在播 Firefox 构造 OS 会话边界，执行真实快照/选择器/音频门：关闭拒绝、开启接受均通过；它不是用户桌面上播放视频的肉眼验收。

## 六、本机热修复构建与部署边界

这是本机增量 EXE 热修复，不冒称跑过官方完整发行流水线。
先将上游基线 `pet.now_playing` 编译结果与安装归档的 code object 对比，相等才允许替换。
只重编译 `pet.now_playing`；其余 **1874 个 PYZ 压缩 payload 逐字节相同**，其他归档入口
与 bootloader 前缀保持相同，现有歌词等修复不会被旧分支覆盖。没有替换 `_internal`、素材、Qt/Python DLL。

- 原 EXE SHA-256：`8ef4bb561373ba6049b47a79b1e8725d0afcb2fce26cc3111afa97ef5a37213d`
- 候选 EXE SHA-256：`9b70eb6b8cc424274036d70b7f58cd530ebb855d53e7058de62bc34e7c21e709`
- 候选大小：16162577 字节。
- 用户确认退出后，进程查询为 0；已校验备份、旧文件与候选 hash，经同目录临时文件 fsync 后原子替换。独立读回安装 EXE hash 与候选一致，再提取安装归档模块执行：Firefox 安装 ID 的浏览器判断为 True，关闭开关的音乐门判断为 False。配置、素材未修改，未代启动程序。
- GUI 实际反馈（部署后）：用户确认 Firefox 视频不再产生歌词，但仍触发唱歌动作，并表示已无伤大雅。因此本次只可确认 Firefox 来源识别/歌词过滤在用户现场生效，**不能声称浏览器视频触发唱歌动作已修复**。冻结边界用例通过不等于现场完整链路通过；残余动画触发原因尚未查证，不归因于宽限期或缓存。用户接受当前效果，本轮不继续扩大动画改动范围。原生播放器现场表现与开启开关后的现场行为尚未由用户确认。
- 本地热修复可能被后续官方更新覆盖；未更改上游版本号、未提交/推送或创建 PR。
