# 2026-08-22 构建产物记录

## 本轮范围

本轮在 `modern/ai-chat` 工作区完成聊天窗口透明背景修复、参考动画素材更新、动作等待间隔、自言自语气泡和设置窗口非模态化，并重新构建 Chat 与无 Chat 版本。构建没有覆盖原有稳定版非 Chat 根目录 EXE。

## 产物

| 类型 | 路径 | 大小（字节） | SHA-256 |
|---|---|---:|---|
| Chat GIF（已替换） | `dist/dsh-pet-standalone-gif-chat.exe` | 466,618,340 | `96EAB2652F94E72348F7220A1ACA7261D99CD6A47E2CDCEBAFA867AFD3E52F3A` |
| Chat WebM（已替换） | `dist/dsh-pet-standalone-webm-对话.exe` | 124,835,152 | `DC3DDA7730BE7A87491AE0B15F0B5E25AE643224AE22402458165C5C66838413` |
| 无 Chat GIF v2（新增） | `dist/dsh-pet-standalone-gif-v2.exe` | 466,541,583 | `7A0CA845B2795758C94529AD83C24A4811E75694183577369F29F0A8B08F4761` |
| 无 Chat WebM v2（新增） | `dist/dsh-pet-standalone-webm-v2.exe` | 124,756,808 | `D397EAE065DABBF06571ED260CBA54A2ABBEA6375884D1489ADAB6657BD35671` |

对应的独立构建目录为：

- `dist/chat-gif-next/`
- `dist/chat-webm-next/`
- `dist/nonchat-gif-v2/`
- `dist/nonchat-webm-v2/`

## 稳定版保护

以下两个原有稳定版文件保留未覆盖：

- `dist/dsh-pet-standalone-gif.exe`
- `dist/dsh-pet-standalone-webm.exe`

本轮仅新增 `-v2` 无 Chat 文件，并替换 Chat 文件。

## 自动验证

- `QT_QPA_PLATFORM=offscreen python -m pytest -q`：26 passed。
- `python -m py_compile pet/window.py pet/app.py pet/settings_dialog.py pet/speech_bubble.py`：通过。
- 四个新/替换 Chat 与无 Chat 根目录 EXE 均使用临时 `APPDATA` 启动冒烟；每个进程等待 8 秒仍保持运行，随后正常结束。
- PyInstaller 构建日志未发现模块缺失或构建失败标记。

## 人工验收建议

1. 启动 Chat GIF 或 Chat WebM，确认聊天窗主体为不透明暖色/浅色背景，消息区和输入区不再透出桌面。
2. 右键桌宠或打开托盘菜单进入“桌宠设置”，在设置窗口保持打开时拖动桌宠，确认设置窗口为非模态，不阻塞桌宠操作。
3. 将“动作等待间隔”设置为非零，观察非待机动作之间只播放待机/转向；恢复为 `0` 后应回到连续播放。
4. 开启自言自语，确认随机气泡出现；修改文本池和最小/最大间隔后重新观察。
5. 分别启动 `dist/dsh-pet-standalone-gif-v2.exe` 与 `dist/dsh-pet-standalone-webm-v2.exe`，确认无 AI 版本不显示 AI 对话入口且桌宠动画可正常播放。

本轮未执行 `git add`、`git commit`、`git push`，未修改或清理 `.research/` 和 `注意事项.txt`。

## 2026-08-26：macOS 四变体重构版

通过 `scripts/build_macos.sh` 在 `build/macos/` 重新生成并临时签名以下独立 bundle：

- `dsh-pet-standalone-webm-chat.app`
- `dsh-pet-standalone-webm.app`
- `dsh-pet-standalone-gif-chat.app`
- `dsh-pet-standalone-gif.app`

构建前重新同步 91 个 WebM 动画到 GIF 素材目录；四个 PyInstaller bundle 均完成 `COLLECT`、`BUNDLE` 和 ad-hoc codesign。`iconutil` 未接受生成的 iconset，脚本按预期改用 Pillow ICNS 回退，未中断应用构建。

本轮完整回归为 `137 passed`。WebM Chat bundle 已通过 bundle 内二进制启动并保持运行，供界面验收。
## 第二轮：气泡居中、GIF 全量同步与 WebM 速率修复

本轮以 `assets/characters/**/*.webm` 作为动画唯一源，使用 `scripts/convert_to_gif.py --force --clean` 全量生成 GIF，并修复 WebM 新动画切换前设置播放速率不生效的问题。当前 WebM/GIF 相对路径集合均为 91 个，差异为 0。

| 类型 | 路径 | 大小（字节） | SHA-256 |
|---|---|---:|---|
| Chat GIF（已替换） | `dist/dsh-pet-standalone-gif-chat.exe` | 828,697,676 | `3D1FB5B08062FABB6FF1E2F88C283E63D0865747AEBEB3C98522CEF819BD1F58` |
| Chat WebM（已替换） | `dist/dsh-pet-standalone-webm-对话.exe` | 124,832,518 | `102DE9AD1E2C4968D4F0C4ABEB84FC0FD6443C8DBD9429F93D3075CB7FC39C69` |
| 无 Chat GIF v3（新增） | `dist/dsh-pet-standalone-gif-v3.exe` | 828,620,164 | `E3B32F59AE666AA7D0C7C3C296EB6746C23FDE75E389CD25B9C03AA03C5B225D` |
| 无 Chat WebM v3（新增） | `dist/dsh-pet-standalone-webm-v3.exe` | 124,758,199 | `D94B87A6C7E6903D951624E1341C6F44C66E4089869A1B7F3E800C1D675F6ED5` |

对应独立构建目录：

- `dist/chat-gif-v3/`
- `dist/chat-webm-v3/`
- `dist/nonchat-gif-v3/`
- `dist/nonchat-webm-v3/`

## 第二轮验证

- `QT_QPA_PLATFORM=offscreen python -m pytest -q`：29 passed。
- `python -m compileall -q pet tests packaging scripts`：通过。
- WebM/GIF 素材同步检查：WebM 91、GIF 91、stale 0。
- WebM 播放速率单测：切换前/启动后 2.0x interval 约 21ms，0.5x interval 约 83ms。
- 气泡定位单测：默认位于角色可见边界正上方并水平居中。
- 四个独立构建产物均启动后保持运行超过 8 秒，随后正常结束；冒烟期间使用工作区临时 `APPDATA`。
- 原有稳定版 `dist/dsh-pet-standalone-gif.exe`、`dist/dsh-pet-standalone-webm.exe` 未覆盖；无 Chat v2 产物也保留。

本轮未执行 `git add`、`git commit`、`git push`，未修改或清理 `.research/` 和 `注意事项.txt`。
