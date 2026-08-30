# 打包与 CI 失败经验（v4.0.2 发布实录）

> 记录时间：2026-08-29。背景：v4.0.2 发布（合并 PR #16/#18/#19/#20/#22 后），本地构建 + 三平台 GitHub Actions CI 共经历 4 轮迭代才全部通过。本文总结每类失败的现象、根因与预防方法，供后续打包发布参考。

---

## 一、本地 Windows 构建

### 1.1 make_icon.py 中文输出崩溃（UnicodeEncodeError）

**现象**：`scripts/build_onedir.ps1` 第一步「Generating app icon」失败：

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-3
make_icon failed: 1
```

**根因**：`make_icon.py` 的 `print("提取首帧: ...")` 等中文输出撞上控制台 cp1252 代码页（Windows 默认），`sys.stdout` 编码失败。

**修复**：构建前设置 `$env:PYTHONIOENCODING='utf-8'`（CI 的 build-*.yml 早已有此设置，本地漏了）。

**预防**：本地跑 `build_onedir.ps1` 前先 `$env:PYTHONIOENCODING='utf-8'`；脚本层面可在 `make_icon.py` 开头 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` 自愈。

### 1.2 Python 3.11 官方 urllib 缺陷（HTTPError(fp=None) 读取响应体）

**现象**：本地（Python 3.11.1，安装路径恰好是 Dev-Cpp 的文件夹）全量测试报 `KeyError: 'file'`，且 pytest 崩溃（INTERNALERROR）。

**根因**：这是 **CPython 3.11 官方实现**的行为（不是特殊发行版）——3.11 的 `urllib/response.py` 中 `addbase` 继承 `tempfile._TemporaryFileWrapper`（3.12 起重构为继承 object）。构造 `HTTPError(url, code, msg, hdrs, fp=None)` 时因 `fp is None` 不调用 `addinfourl.__init__`，实例缺 `file` 键；随后 `exc.read(2048)`（vision.py 429 处理）触发 `_TemporaryFileWrapper.__getattr__` → `KeyError: 'file'`。

**注意**：CI 的官方 Python 3.11.9 同样存在该行为——这段 429 重试代码（PR #19）是 v4.0.2 才引入的，首次在 CI 跑测试即暴露（被 os.name 污染的 INTERNALERROR 掩盖，见 2.2）。

**修复**：`pet/vision.py` 对 `exc.read()` 加 try/except 防御（读不到响应体按空处理，不影响状态码判断）——三平台都必要。

**教训**：
- 「本地过了」不等于「CI 过了」，反之亦然——Python 小版本（3.11.x 间行为也可能不同）、pytest 版本、Qt 版本都可能不同；
- 网络/文件对象路径（`urllib`、`subprocess`、`tempfile`）最容易暴露版本差异，写代码时对响应体读取做防御；
- 遇到 pytest INTERNALERROR（非测试失败而是 pytest 自身崩），先怀疑**失败异常的 repr 阶段**再崩（异常对象/链里有损坏对象）。

---

## 二、CI 测试套件失败（tag 触发，测试失败 = 构建失败）

CI 的 build-*.yml 都在打包前跑 `python -m pytest -q`，任何测试失败都会中止发布。以下是 v4.0.2 踩过的坑。

### 2.1 Windows：offscreen 下 QMessageBox 崩溃（access violation）

**现象**：`test_modern_settings_save_writes_autostart_wanted` 触发 `Fatal Python error: Aborted` + `Windows fatal exception: access violation`，崩溃栈在 `_apply_autostart` → `QMessageBox.warning`。

**根因**：测试未 mock 真实系统写入。CI runner 上 `autostart.enable()`（写 HKCU Run 注册表）返回 False（Run 键可能不存在），`_apply_autostart` 弹 `QMessageBox.warning`——offscreen 平台下模态框无人交互，Qt 崩溃（本地实测为**挂起**）。

**修复**：测试 mock `set_enabled`：`monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda enabled: True)`。

**预防**：凡是会弹框（QMessageBox）的代码路径，测试必须 mock 掉前置条件（写入/网络/对话框），**绝不能让它真实执行到弹框**；offscreen 下任何模态交互都是定时炸弹。

### 2.2 Linux/macOS：测试污染全局 os.name → pathlib 崩溃（INTERNALERROR）

**现象**：Linux/macOS 测试跑到约 78% 处 pytest INTERNALERROR，`NotImplementedError: cannot instantiate 'WindowsPath' on your system`，且同一异常在 repr 阶段再次崩溃（`Path(os.getcwd())`）。

**根因**：`tests/test_click_sound.py` 用 `monkeypatch.setattr(click_sound.os, "name", "nt")` 模拟 Windows 播放分支——`click_sound.os` 是**全局 os 模块**，等于把整个进程的 `os.name` 改成 `"nt"`。此后任何 `pathlib.Path(...)`（包括 pytest 内部）在非 Windows 上创建 `WindowsPath` → NotImplementedError。测试失败后 pytest 生成报告（repr）时再次踩中 → INTERNALERROR，吞掉了真正的失败信息，极难排查。

**修复**：改为替换**模块属性**（项目已有正确范例 `test_window_rendering.py`）：
```python
monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
```

**预防**：
- **测试绝不允许改全局 `os.name` / `sys.platform`**；要模拟平台分支就替换被测模块的 `os` / `sys` 引用（SimpleNamespace stub）；
- 排查 INTERNALERROR 先抓**第一个 traceback 的源头异常**（可能出现在 pytest 框架内部，不代表测试本身崩）；
- 全局状态污染还会导致**后续所有测试**（包括 pytest 自身收尾）连锁崩溃，影响面不可控。

### 2.3 Linux：外部角色目录测试的 APPDATA 假设

**现象**：`test_external_character_dirs_uses_variant_then_legacy_fallback` / `test_external_character_dirs_dedupes_base_dir` 在 Linux 上 `assert variant in dirs` 失败。

**根因**：实现按 `sys.platform` 选数据根（Windows→APPDATA、macOS→`~/Library/Application Support`、Linux→XDG_CONFIG_HOME），测试只 `monkeypatch.setenv("APPDATA", ...)`——Windows 上过，Linux/macOS 上根本没生效。

**修复**：测试按平台分支设置对应环境变量并计算期望路径。

**预防**：涉及平台分目录的测试，三个平台都要写对（`sys.platform == 'win32'/'darwin'/else`）；不要只在 Windows 上验证过就提交。

### 2.4 macOS：字体度量舍入导致省略号断言失败

**现象**：`test_elide_bubble_text_max_lines_6` 在 macOS 上 `'…' not in elided_30` 失败（30 字符 = 6 行本应无省略号，实际第 6 行出现省略号）。

**根因**：测试用 `line_w = metrics.horizontalAdvance(char) * 5` 推算「恰好 5 字符」的行宽。部分平台（macOS）对 CJK 字形的 advance 累加有亚像素舍入，`horizontalAdvance("测"*5)` 实际略大于 `5 × horizontalAdvance("测")` → 每行装不下 5 字符 → 30 字符被省略。

**修复**：行宽改用字符串实际度量：`line_w = metrics.horizontalAdvance(char * 5)`。

**预防**：字体度量类断言不要用「单字符宽度 × N」推算，直接用「N 个字符的字符串宽度」；这类测试天然平台敏感，注释里写明。

### 2.5 pytest 版本漂移

**现象**：本地 pytest 8.3.4，CI `pip install pytest`（未锁版本）装到 9.1.1——行号、内部行为（如 cacheprovider）都可能不同，本地无法完整复现 CI 的 INTERNALERROR。

**预防**：`requirements.txt`（或 CI 安装命令）锁定 pytest 主版本（如 `pytest==8.3.*` 或直接钉死）；排查 CI 特有崩溃时先确认版本差。

### 2.6 Linux/macOS：Windows 专用测试替身挂到不存在的 `winreg` 属性

**现象**：v4.0.5 发布时 Linux/macOS 测试在 `test_windows_autostart_variants_do_not_affect_each_other` 失败：

```
AttributeError: module 'pet.autostart' has no attribute 'winreg'
```

**根因**：`pet/autostart.py` 只在 `_IS_WIN` 为真时 `import winreg`，Linux/macOS 模块根本没有 `winreg` 属性；测试里 `monkeypatch.setattr(autostart_mod, "winreg", fake)` 在非 Windows 平台直接 AttributeError。

**修复**：Windows 专用测试挂替身时使用 `monkeypatch.setattr(autostart_mod, "winreg", fake, raising=False)`，属性不存在时自动补挂。

**预防**：平台条件导入的模块属性，在跨平台测试里用 `raising=False`；Windows 上能过的测试不代表 Linux/macOS 也能过。

---

## 三、发布流程经验

### 3.1 tag 是发布触发器

- 三平台 workflow 都 `on: push: tags: 'v*'`，**只有 tag 推送才构建并发布 Release**；
- 合并 PR 到 main 不会触发构建——v4.0.2 前合并的 PR #19/#20 从未跑过 CI，问题全在第一次发版时暴露；
- tag 指错了提交：删 tag → 重建指向新提交 → 推送（`git push origin :v4.0.2 && git tag v4.0.2 && git push origin v4.0.2`）。

### 3.2 softprops/action-gh-release 的行为

- 首个 workflow 到达 Publish 步骤时创建 Release；后续 workflow 向同一 tag 的 Release **追加/覆盖资产**——多轮 CI 后最终资产恰好 8 个（同名覆盖），不会累积重复；
- Release 描述默认 `generate_release_notes: true`，发布后用 `gh release edit v4.0.2 --notes-file <md>` 覆写正式说明。

### 3.3 update.json 一致性

- `update.json` 的 `version` 与 8 个资产 URL 必须与 Release 完全一致（桌宠「检查更新」按它下载）；
- 改版本号时同步：`pet/__init__.py`（唯一来源）→ `packaging/dsh-pet.iss` 默认值 → `update.json` → README；
- 发布后核对脚本：比较 Release 资产名集合与 update.json 的 key 集合（见文末）。

### 3.4 网络与推送通道

- 本项目 git remote 是 https，但 https 到 github.com 可能被墙/重置（Recv failure: Connection was reset / Could not connect to server）——**SSH 通道（git@github.com）通常可用**；
- 备选推送：`git push git@github.com:MerZlin/dsh-pet-indesktop.git main`（`gh api` 走代理也正常）；
- **注意**：用 SSH URL 推送不会更新本地 `origin/main` 引用，之后 `git status` 会误报 ahead——用 `git update-ref refs/remotes/origin/main <sha>` 或正常 fetch 同步。

### 3.5 版本号与构建参数要点（回顾）

- 安装包版本号由 CI 从 `pet/__init__.py` 单一来源注入（`/DMyAppVersion=$version`）；
- Linux/macOS 构建必须显式收集/打包：`--collect-all PySide6.QtMultimedia`（点击音效 MP3 等）、chat 变体 `--collect-all keyring`（API Key 安全存储）、`--add-data "integrations:integrations"`（DSH 桥接一键安装，PR #22 修复）；
- 构建后断言检查（`find dist -path "*integrations/dsh-pet-bridge/package.json"`）防漏打包。

---

## 四、检查清单（下次打包发布前）

- [ ] `$env:PYTHONIOENCODING='utf-8'` 再跑本地构建
- [ ] 本地全量测试通过（offscreen）；有 INTERNALERROR 先查源头异常
- [ ] 测试代码 grep 确认无 `setattr(..., "os", ...)` / `"sys.platform"` 全局污染（应替换模块属性）
- [ ] 平台分目录/字体度量/模态框相关测试三平台语义正确
- [ ] 版本号四处同步：`__init__.py` / `.iss` / `update.json` / README
- [ ] tag 指向最终提交后推送（CI 自动三平台构建）
- [ ] 发布后核对：Release 资产 = 8 个 = update.json 资产 key 集合
- [ ] Release 描述用 `gh release edit` 写入正式说明（修复清单 + 下载表 + 致谢）
