# 稳定版构建冻结记录

> 本文件用于防止聊天功能开发、调试或重新打包时误覆盖稳定版产物。

## 冻结文件

以下两个文件名视为稳定版固定入口，未经用户明确要求不得覆盖、重打或改名替换：

- `dist/dsh-pet-standalone-webm.exe`
- `dist/dsh-pet-standalone-gif.exe`

## 当前基线

- 构建基线：`main`，提交 `420f20a`
- 构建方式：独立 main 构建工作树 + PyInstaller onefile/windowed
- Q 弹修复：保留 main 的动画参数，仅将 DPR 物理像素转换为 QWidget 逻辑尺寸

| 文件 | 大小（字节） | SHA-256 |
|---|---:|---|
| `dsh-pet-standalone-webm.exe` | 116346744 | `DCA3A37C00D149298A8C6CE7C2D1CC5904AC791B2CE964B4CB721723FAFECFD3` |
| `dsh-pet-standalone-gif.exe` | 469777365 | `B0C8A8213FAA9D6811FD84A02FE0EEA35DFF7B3132EAE23B6005469853AE564F` |

## 构建隔离规则

1. AI 聊天版必须使用带 `-chat` 后缀的输出名，例如 `dsh-pet-standalone-gif-chat.exe`。
2. 不使用 `dsh-pet-standalone-gif.spec` 或 `dsh-pet-standalone-webm.spec` 进行开发版构建。
3. 任何构建前先检查输出名；发现目标为上述两个稳定文件名时应停止构建。
4. 需要更新稳定版时，先由用户明确指定，再重新记录 SHA-256；否则只修改源码、测试或 `-chat` 构建产物。

## 说明

EXE 不由 Git 分支切换自动生成。PyInstaller 的 `--name` 或 spec 中的 `name=` 会直接决定 `dist` 下的文件名；如果使用稳定版名称运行构建命令，就会覆盖原文件。因此稳定版和聊天版必须使用不同输出名及独立构建目录。