# v4.3.1

> 从 **v4.3.0 到 v4.3.1** 的修复汇总：自 v4.3.0（2026-10-15 发布）之后进入 `main` 的 **8 个提交**，主题为拖拽手感、Markdown 渲染覆盖与主宠设置保存（#271）。
>
> **本文件分两段用**：`## 📦 下载` 至 `## 🙏 致谢` 是**发布正文**；`RELEASE-BODY-END` 注释之后的维护者清单**只在仓库内使用，不随 Release 发布**。
>
> 生成发布正文 = 取 `RELEASE-BODY-END` 注释行之前的内容（末尾 `---` 一并去掉）：
> `python -c "import pathlib;L=pathlib.Path('docs/RELEASE-v4.3.1.md').read_text(encoding='utf-8').splitlines();i=[k for k,l in enumerate(L) if l.strip().startswith('<!--') and 'RELEASE-BODY-END' in l][0];pathlib.Path('body.md').write_text('\n'.join(L[:i]).rstrip().rstrip('-').rstrip()+'\n',encoding='utf-8')"` → `gh release edit v4.3.1 --notes-file body.md`。

---

## 📦 下载

发布构建由 tag 自动触发；产物生成后会附到本 Release：

| 平台 | 变体 | 安装版 / 绿色版 |
|---|---|---|
| Windows x64 | WebM + Chat（AI 对话） | `dsh-pet-standalone-webm-chat-setup.exe` / `dsh-pet-standalone-webm-chat-portable.zip` |
| Windows x64 | WebM 无 Chat（纯桌宠） | `dsh-pet-standalone-webm-setup.exe` / `dsh-pet-standalone-webm-portable.zip` |
| macOS arm64 | WebM + Chat | `dsh-pet-standalone-webm-chat-macos-arm64.zip` |
| macOS arm64 | WebM 无 Chat | `dsh-pet-standalone-webm-macos-arm64.zip` |
| Linux x86_64 | WebM + Chat | `dsh-pet-standalone-webm-chat-linux-x86_64.zip` |
| Linux x86_64 | WebM 无 Chat | `dsh-pet-standalone-webm-linux-x86_64.zip` |

均为 **onedir** 形态：安装版与绿色版运行期都不解压、不产生临时缓存，启动快、卸载干净。

## ⚠️ 升级说明

- 直接覆盖安装或换用新版便携版即可；配置目录不变，**无需迁移**。
- 本版无行为变更：「物理拖拽」「静止放手下落」等开关的默认值与 v4.3.0 相同，设置里的既有选择原样保留。

## 🐛 修复内容

- **拖拽手感（单合成窗拓扑）**：拖拽恢复弹簧阻尼跟随（与旧版手感一致）；弹簧档松手不再瞬移到光标位置（就地起抛/落地）；慢速拖拽的亚像素步进也会实时上屏，不再一格一格跳；弹簧参数调活（滞后约减半、轻微过冲）。开关位置不变：设置 → 桌宠 →「物理拖拽」，默认仍关。
- **Markdown 渲染补齐**：快捷气泡（快捷提问/灵动岛问答）与经典聊天窗接入 Markdown 渲染，表格、代码块、列表与现代聊天窗同一口径。
- **主宠设置保存生效**（#271）：主桌宠设置页里的每宠设置（出图概率、气泡样式、点击朗读等）此前「保存成功但不生效、重开设置回默认值」——写入被静默丢弃。现已修复：主宠与子宠设置页走同一条保存通道，改动真实落到主宠档案并即时生效。

## 🧪 测试与工程

本版测试规模 **7512 项**；三端 CI（Windows / macOS / Linux）为合并门禁。修复批逐项过「lint + 全量测试 + 真机验证」三道门，#271 另经双盲审（审查发现的一处升级路径数据风险已返修并入）。逐项证据见 `docs/INDEX.md` 里的 PR 报告存档。

---

## 🙏 致谢

- [klxxya](https://github.com/klxxya)——拖拽物理批、Markdown 覆盖、#271 修复。
- **Xaisx-sys**——#271 报告（含精确的代码定位与主/子宠对照实验，直接锁定了根因）。

---

<!-- RELEASE-BODY-END -->

## 📋 发布前清单（维护者）

1. **版本号**：`pet/__init__.py` 的 `__version__` 已改为 `4.3.1`（CI 从 `__version__` 注入 `packaging/dsh-pet.iss`）。
2. **正式发布**：`git tag v4.3.1 && git push origin v4.3.1` → 三端构建 job 的 Publish Release 步骤自动创建 Release。
   ⚠️ 三个 workflow 都带 `generate_release_notes: true`——发布后**必须**按文件头命令生成 `body.md` 再 `gh release edit v4.3.1 --notes-file body.md` 覆盖，不要把本文件整份贴出去。
3. **同步**：`docs/INDEX.md` 登记本文件；README 状态块更新。
4. **回滚**：本版无配置迁移与行为变更，产物可直接换回 v4.3.0。
