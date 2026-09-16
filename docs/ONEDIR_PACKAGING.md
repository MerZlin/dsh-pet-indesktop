---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: e1503192c50cbf4a9392ca071882b90f_d44cd8a3b12211f18039525400461939
    ReservedCode1: 5WS8ZIlf31sY8kOZGi3OuLj7wtTR4orlRD+uQa4vpN/zbG9icu3IcPiWHfrwX3FNAnkDwfOlDoaC6pO5on6QU9u6BhQY8Zvk9FgkMQ7xBy5xT9qvgmVNFq1i/Rd205JLy3QoCQjQoilIbKr0n59e8Zxm5NeUzvEkLeDmjfwJRUMj8DxzCVnfPdOP0Hs=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: e1503192c50cbf4a9392ca071882b90f_d44cd8a3b12211f18039525400461939
    ReservedCode2: 5WS8ZIlf31sY8kOZGi3OuLj7wtTR4orlRD+uQa4vpN/zbG9icu3IcPiWHfrwX3FNAnkDwfOlDoaC6pO5on6QU9u6BhQY8Zvk9FgkMQ7xBy5xT9qvgmVNFq1i/Rd205JLy3QoCQjQoilIbKr0n59e8Zxm5NeUzvEkLeDmjfwJRUMj8DxzCVnfPdOP0Hs=
---

# onedir 打包流水线（绿色版 zip + Inno Setup 安装包）

目标：**运行期零解压**——不再产生 `C:\...\Temp\_MEIxxxxxx` 缓存。

- onefile：每次启动把全部素材解压到系统临时目录；崩溃/强杀/断电残留；启动慢（GIF 版 800MB 每次全解压）
- onedir：直接从安装目录加载，任何盘都不产生 `_MEI`，启动快，卸载即净

## 一、构建 onedir + zip 绿色版

应用图标由待机封面帧生成（`python scripts/make_icon.py` → `assets/icon.ico`），
exe 与安装包共用；换形象后重新生成即可。

```powershell
# 全部变体：
#   webm-chat（默认）| webm | gif-chat | gif（GIF 变体加 -Gif 先生成 GIF 素材）
powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm-chat
```

产物：

```
dist-onedir\dsh-pet-standalone-webm-chat\          ← onedir 目录（绿色版 = 整个文件夹）
dist-onedir\dsh-pet-standalone-webm-chat-portable.zip
```

绿色版用法：解压 zip 到任意盘（E:\、D:\、U 盘均可），双击 exe 即用；无安装、无缓存。

### 包体瘦身（默认开启，`-SkipSlim` 可关闭）

构建在 Qt runtime 复制之后、中文编码自检之前自动执行 `python scripts\slim_bundle.py`：

- **移除白名单**：Qt Quick/QML 栈（`Qt6Quick` / `Qt6Qml*`）、`Qt6VirtualKeyboard` + 平台输入法插件、`Qt6OpenGL`、`Qt6Pdf` + `qpdf.dll` 图像插件、`opengl32sw.dll`、非中英 `*.qm` 翻译（保留 12 个中英）、PIL `_avif` 扩展 —— 合计 124 文件 / 53 MB；
- **安全校验**：移除前做依赖闭包校验（pefile 反向 import 检测，保留的二进制若仍引用待删文件即中止）与必需清单校验（核心运行文件齐全），任一失败即中止构建；
- **效果**：onedir 目录 309 MB → 256 MB，portable zip 176.2 MB → 146.5 MB（−12.8%）；
- 后续需要完整 Qt 栈（例如启用 QML 界面）时加 `-SkipSlim`；
- 冒烟建议：把配置切到 `voice_chime_schedule=every_minute` 启动 exe，确认 `%APPDATA%\dsh-pet-standalone-<variant>\voice_chime_cache` 新增 mp3（edge-tts 合成落盘），验证完恢复配置。

## 二、Inno Setup 安装包（正式分发）

本机已装：`E:\tools\InnoSetup6\ISCC.exe`（便携模式，免管理员）。
通用脚本 `packaging\dsh-pet.iss` 用 `/D` 定义编译任意变体：

```powershell
# webm-chat（脚本默认值）
E:\tools\InnoSetup6\ISCC.exe packaging\dsh-pet.iss

# webm
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-webm /DMyAppExeName=dsh-pet-standalone-webm.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-webm "/DMyAppId={{ED2590E4-A968-4E8D-B7C4-75DFE012D0E9}}" "/DMyAppDisplay=dsh-pet-standalone (WebM)" packaging\dsh-pet.iss

# gif-chat
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-gif-chat /DMyAppExeName=dsh-pet-standalone-gif-chat.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-gif-chat "/DMyAppId={{7FE1EEDD-91DB-4F4B-834D-894DFE782256}}" "/DMyAppDisplay=dsh-pet-standalone (GIF Chat)" packaging\dsh-pet.iss

# gif
E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-gif /DMyAppExeName=dsh-pet-standalone-gif.exe /DMyAppDir=..\dist-onedir\dsh-pet-standalone-gif "/DMyAppId={{308454BF-3FD5-4A30-B0FF-1D23BF31DCF1}}" "/DMyAppDisplay=dsh-pet-standalone (GIF)" packaging\dsh-pet.iss
```

产物：`dist-onedir\<shortname>-setup.exe`

安装包特性：

- 免管理员（`PrivilegesRequired=lowest`），默认装 `%LOCALAPPDATA%\Programs\...`，向导中**用户可自行选择任意盘符**
- 创建开始菜单/桌面快捷方式；装完可选立即运行
- 控制面板可卸载，卸载时清理 `_MEI*` 防御条目
- 语言：简体中文 + English

## 三、冒烟验证清单

1. 启动 `dist-onedir\<name>\<name>.exe`，8 秒后确认进程存活
2. **系统临时目录无新增 `_MEI*`**（onedir 根本不解压）
3. exe 同目录无 `_MEI*` 残留
4. 退出后进程全部结束

## 四、无 Chat 变体兼容性

无 Chat 入口使用 `packaging/pet_entry_no_chat.py`，构建规格明确排除 `pet.chat`
和 `keyring`。配置仍可能来自曾经启用 Chat 的用户目录，因此运行时
`Config._migrate_plaintext_keys_to_keyring()` 对缺失的 `pet.chat` 只跳过迁移，
不能让配置加载失败；除该明确缺失模块外的导入错误仍应抛出。

打包验收至少包含：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_chat_subsystem.py::test_no_chat_packaging_uses_isolated_entrypoint
python -m pytest -q tests/test_config_key_migration.py
```

## 五、注意事项

- **开机自启**：onedir 不需要 `start /D` 切目录（无解压），`pet/autostart.py` 现有命令无害可保留
- **旧 onefile 遗留清理**：`pet/app.py` 启动时的 `_cleanup_stale_runtime_dirs` 保留，会顺带清掉旧 onefile 版本在系统 Temp 留下的 `_MEI` 目录
- **本机遗留旧自启项**：注册表 `HKCU\...\Run` 里的 `DesktopPet = E:\software\AI\AI的有用工具\打字统计\dist\DesktopPet.exe` 是 7 月的旧 onefile 构建（无 `start /D`、解压在 C 盘 Temp），建议删除或替换，避免开机双桌宠 + 继续污染 C 盘
- GIF 变体体积大（800MB+），zip/安装包较慢；WebM 变体约 124MB
*（内容由AI生成，仅供参考）*
