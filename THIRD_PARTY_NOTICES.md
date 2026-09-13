# Third-Party Software Notices and Information

This project includes third-party software and assets subject to their respective licenses.

---

## 1. DeepSeek Balance Whale Widget Sound Assets

- **Files**:
  - `assets/sounds/duck/Ya1.mp3`
  - `assets/sounds/duck/Ya2.mp3`
- **Origin / Source Repository**: [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)
- **License**: MIT License

---

## 2. Character Animation Assets（深深 / 小鲸鱼 webm 素材）

- **Files**: `assets/characters/**`（640×360 / 24fps / VP9-alpha 透明 webm）
- **Origin / Source Repository**: [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（动作素材做法与素材来源；本项目经整理搬运）
- **Notes**: 角色 OC「溟月」出自画师上善无形，素材由社区成员整理制作。
  此类同人素材按 CC BY-NC-SA 类条款发布，**仅限个人非商业使用**，
  使用须保留署名与来源，不得用于任何商业/盈利场景。

### MIT License Text

```text
MIT License

Copyright (c) 2025 MeteorNOX

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 3. BongoCat runtime（键鼠跟随模式）

- **Files**: 打包产物内的 `external/bongocat/**`（`BongoCat.exe` 与 Live2D 预置模型
  `assets/models/**`）。**本仓库不提交这些二进制**：它们由 CI 在打包时从我们
  fork 的仓库构建并复制进来（见 `scripts/build_bongo_runtime.ps1`）。
- **Origin / Source Repository**: [ayangweb/BongoCat](https://github.com/ayangweb/BongoCat)
  （fork：[MerZlin/BongoCat](https://github.com/MerZlin/BongoCat) 的 `dsh-pet` 分支）
- **License**: MIT License
- **Notes**: 该运行时只在用户主动切到「键鼠跟随」模式时启动，作为独立进程运行；
  我们的 fork 只做最小改动（新增「切回原桌宠」菜单项、隔离应用标识、禁用自动更新、
  开机自启默认关闭），改动清单见 `docs/KEY-MOUSE-MODE-2026-09-13.md`。

### MIT License Text

```text
MIT License

Copyright (c) 2024 ayangweb

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
