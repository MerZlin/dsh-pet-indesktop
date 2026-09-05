# 打包与 CI 失败经验（持续更新）

> 记录时间：2026-08-30。本文汇总近期三平台打包/CI 反复踩过的坑与最终解法；旧的 v4.0.2 逐轮实录已精简，只保留仍然有效的经验。

---

## 一、构建与 CI 统一入口

### 1.1 三平台构建脚本是唯一入口（PR #39）

- Windows：`scripts/build_onedir.ps1`
- macOS：`scripts/build_macos.sh`
- Linux：`scripts/build_linux.sh`（PR #39 新增）

CI workflow 不再内联 PyInstaller 命令，而是直接调用上述脚本：

```yaml
- name: Build onedir variants
  run: bash scripts/build_macos.sh --dist dist --variants webm-chat,webm
```

**经验**：本地构建与 CI 必须共用同一份脚本，否则会出现“本地漏收集 QtMultimedia / keyring，CI 却正常”或反之的漂移。

**典型案例（PR #39 修复）**：
- `scripts/build_macos.sh` 曾漏掉 `--collect-all PySide6.QtMultimedia`（点击音效 MP3 等多媒体资源）和 chat 变体的 `--collect-all keyring`（API Key 系统安全存储）；
- macOS 本地脚本还多打包了无人引用的 `pet/chat/styles.qss`；
- Linux 此前没有本地构建脚本，只能照抄 CI 内联命令，同样存在漂移风险。

**教训**：改任何构建参数（collect/add-data/exclude）时，只改脚本，不要同时维护 workflow 内联命令。

### 1.2 构建前编码隔离

本地跑构建前设置：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
```

否则中文输出/资源可能被 Windows 控制台代码页误解码。

---

## 二、CI 测试挂起 / 崩溃

### 2.1 模态 QMessageBox 在无人值守环境挂起/崩溃（PR #39 / issue #37）

**现象**：GitHub Actions Windows runner 上测试偶发整步挂起或 `Fatal Python error: Aborted`。

**根因**：`settings_dialog._save()` 写开机自启失败时弹出**模态** `QMessageBox.warning`，offscreen/无人值守环境无人点击，永久阻塞或崩溃。

**根治**：`tests/conftest.py` 用 autouse fixture 全局把 `QMessageBox` 静态弹窗方法替换为 no-op：

```python
@pytest.fixture(autouse=True)
def _no_modal_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    for method in ("warning", "information", "critical", "question", "about"):
        monkeypatch.setattr(QMessageBox, method, staticmethod(lambda *a, **k: None))
```

**经验**：
- 不要继续“单个测试逐个 mock 弹窗前置条件”，新调用点会复发；
- 需要断言弹窗行为的测试自行 `monkeypatch` 覆盖即可；
- 生产代码中弹模态框对真实用户合理，但无人值守环境是定时炸弹，未来可考虑改非模态/托盘提示。

### 2.2 平台条件导入的属性在跨平台测试中不存在

**现象**：v4.0.5 发布时 Linux/macOS 测试报：

```
AttributeError: module 'pet.autostart' has no attribute 'winreg'
```

**根因**：`pet/autostart.py` 只在 Windows 下 `import winreg`，非 Windows 模块根本没有该属性。

**修复**：

```python
monkeypatch.setattr(autostart_mod, "winreg", fake, raising=False)
```

**经验**：平台条件导入的模块属性，跨平台测试用 `raising=False` 补挂替身。

### 2.3 禁止改全局 os.name / sys.platform

模拟平台分支时替换被测模块自己的 `os` / `sys` 引用，不要改全局：

```python
monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
```

改全局会导致 pathlib 等后续代码在非 Windows 上崩出 `WindowsPath`，并可能引发 pytest INTERNALERROR。

**最近排查**：`tests/test_proactive.py` 曾用 `monkeypatch.setattr("pet.proactive.sys.platform", "darwin")`——这改的是**全局 `sys.platform`**。已改为替换模块内的 `sys` 引用：

```python
monkeypatch.setattr("pet.proactive.sys", SimpleNamespace(platform="darwin"))
```

**检查方式**：grep 测试代码确认没有 `setattr(..., "sys.platform", ...)` / `setattr(..., "os.name", ...)`。

---

## 三、平台特有经验

### 3.1 Linux Wayland：默认改用 xcb / XWayland（PR #39 / issue #38）

**现象**：GNOME Wayland 会话下桌宠无法拖动、透明区域拖影。

**根因**：Qt 原生 wayland 插件不允许客户端自行 `QWidget.move()`，且透明无边框窗口重绘有残留。

**修复**：`pet/app.py` 在创建 `QApplication` 前检测 Wayland 会话，默认设置 `QT_QPA_PLATFORM=xcb`；用户显式设置过该变量时尊重不覆盖。

**经验**：
- 透明桌面挂件在 Wayland 下经 XWayland 运行是成熟路径；
- CI 已显式设置 `QT_QPA_PLATFORM=offscreen`，不受影响。

### 3.2 macOS：字体度量不要用“单字符宽度 × N”

CJK 字形 advance 累加有亚像素舍入，测试行宽应直接用整串字符串度量：

```python
line_w = metrics.horizontalAdvance(char * 5)
```

### 3.3 Linux/macOS：平台分目录测试要写对

涉及 APPDATA / XDG_CONFIG_HOME / `~/Library/Application Support` 的测试，三平台都要设置对应环境变量并计算期望路径。

---

## 四、发布流程经验

### 4.1 tag 是 Release 触发器；workflow_dispatch 只验证不发布

- 三平台 workflow `on: push: tags: 'v*'`，只有 tag 推送才执行 `Publish Release`；
- 用 `gh workflow run` / Actions 页面手动 dispatch 到 `main` 时，**只跑测试+构建+上传 artifact，不会创建/修改 Release**——适合验证 CI 是否修复，不会污染 Release；
- 需要确认“CI 不发布 Release”时，直接看 workflow 的 `Publish Release` 步骤是否为跳过（`-`）。

### 4.2 softprops/action-gh-release 行为

- 首个 workflow 到达 Publish 步骤时创建 Release；
- 后续 workflow 向同一 tag 的 Release 追加/覆盖资产；
- 最终资产应为 8 个（Windows 安装包×2 + Windows 便携×2 + macOS×2 + Linux×2）。

### 4.3 tag 指错提交 / 需要重跑

```bash
git push origin :refs/tags/v4.0.5
git tag -d v4.0.5
git tag -a v4.0.5 -m "..."
git push origin v4.0.5
```

- 旧 tag 触发的多余 workflow 可以 `gh run cancel <run-id>` 取消；
- Windows 构建较慢，若已用新 tag 重跑，旧 Windows run 不必等。

### 4.4 update.json 一致性

- `update.json` 的 `version` 与 8 个资产 URL 必须与 Release 完全一致；
- 改版本号时同步：`pet/__init__.py` → `packaging/dsh-pet.iss` 默认值 → `update.json` → README；
- 发布后核对 Release 资产集合与 `update.json` 的 key 集合。

---

## 五、当前实现类似问题排查记录

对现有代码做了一次同类问题扫描：

### 5.1 QMessageBox 模态弹窗

- 生产代码仍有 20+ 处 `QMessageBox.warning/information/question/critical`，对真实用户合理；
- 测试侧已由 `tests/conftest.py` 全局 mock，无人值守环境不会再挂起；
- 新增“保存/弹窗”类测试时，注意自行覆盖或依赖全局 mock。

### 5.2 QWidget.move() 与 Wayland

- 桌宠窗口、聊天窗、气泡、灵动岛都依赖 `QWidget.move()`；
- `pet/app.py` 已在 Wayland 会话默认切到 `xcb`（XWayland），因此默认环境下这些 move 都可用；
- 已知边界：如果用户显式设置 `QT_QPA_PLATFORM=wayland`，原生 Wayland 下 move 仍会被合成器限制；这是协议级限制，非本仓库 bug。

### 5.3 平台条件导入 / 全局平台变量

- `pet/autostart.py` 的 `winreg` 条件导入已通过 `raising=False` 处理；
- 测试中 `click_sound.os` 使用 `SimpleNamespace` 替换模块属性，符合规范；
- 已修复 `tests/test_proactive.py` 中一处误改全局 `sys.platform` 的问题（改为替换模块内 `sys` 引用）；
- 后续新增 Windows 专用测试时，继续遵守“替换模块属性，不改全局”。

---

## 六、发布前检查清单

- [ ] 构建前设置 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`
- [ ] 本地先运行非压力主套件：`QT_QPA_PLATFORM=offscreen python -m pytest -q -k "not cross_process_concurrent_publish_read_stress"`
- [ ] 非压力主套件完整结束且无 native abort 后，再单独运行
      `tests/test_decode_broker_shm.py::test_cross_process_concurrent_publish_read_stress`
- [ ] 确认 `tests/conftest.py` 的全局 QMessageBox mock 存在；新增弹窗相关测试注意自行 monkeypatch 覆盖
- [ ] 平台条件导入属性用 `raising=False` 补挂
- [ ] 无全局 `os.name` / `sys.platform` 污染
- [ ] 版本号四处同步：`__init__.py` / `.iss` / `update.json` / README
- [ ] tag 指向最终提交后推送，CI 自动三平台构建
- [ ] 发布后核对：Release 资产 = 8 个 = `update.json` 资产 key 集合
- [ ] Release 描述用 `gh release edit` 写入正式说明
