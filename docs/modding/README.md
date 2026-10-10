# MOD 使用与制作指南（v1）

适用候选：Core **4.2.5**；AI / 识屏 DLC **1.0.4**。工程完成度以[持续状态](../../.scratch/mod-authoring-v1/STATUS.md)及[交付报告](../PR-REPORT-MOD-CENTER-V1-2026-10-10.md)为准，教程不是发布公告。

本页是全套 MOD 文档的入口。先看你是「加 MOD 的人」还是「做 MOD 的人」，再按 MOD 类型跳到对应教程与接口契约。

## 一、我只想加 MOD（使用者）

1. 打开 设置 → 扩展管理，用「导入 ZIP」或「导入目录」选一个完整包（所选目录根必须有 `manifest.json`，别选仓库根或单独的 `videos`）。
2. 新包默认**未启用**；点「启用」后入口与设置立即出现，不必关闭设置。
3. 角色包的「启用」只是加入可选列表，点「使用」才给当前实例换装。

完整步骤、更新 / 停用 / 回滚 / 批量删除、项目目录布局与常见问题：[MOD 使用指南](USER-GUIDE.md)。

> **已知缺陷（2026-10-10，未解决）**：ZIP 导入的外接 MOD 可能无法启用，同一份内容改用「导入目录」正常。定位前请**优先用「导入目录」**。详情见下面 [§四 已知缺陷](#四已知缺陷2026-10-10)。

## 二、我想做 MOD（作者）

先按类型选教程，写代码时对照[公共接口](API-V1.md)：

| MOD 类型 | 你得到什么 | 教程 |
|---|---|---|
| 角色素材包（纯内容，不执行代码） | 换角色、替换 / 新增动画 | [角色素材教程](CHARACTER-PACKS.md) |
| 台词 / 人格模板 | 只改台词与口吻，不做可卸载功能包 | [台词模板教程](PERSONA-TEMPLATES.md) |
| 功能扩展 · host-only | 增加菜单与设置，不需要独立进程 | [host-only 样例](HOST-ONLY.md) |
| 功能扩展 · host-worker | 在独立进程里跑耗时任务 | [Echo Worker 样例](HOST-WORKER.md) |

可运行样例源码在 [`examples/mods/`](../../examples/mods/README.md)：`hello-local`（host-only）、`echo-worker`（host-worker）、`persona-phrases.json`（台词模板）。

打包与自检命令（在仓库根目录执行，推荐独立虚拟环境）：

```bash
python -X utf8 -m scripts.build_mod_example --check examples/mods/hello-local
python -X utf8 -m scripts.build_mod_example examples/mods/hello-local <输出目录>
python -X utf8 -m scripts.build_character_mod_example <输出目录>
```

## 三、接口文档

- [pet.mod_api.v1 接口基线](API-V1.md)：定义与挂载、菜单与设置协议、自有配置 / 数据、Core 简易 API、WorkerClient 生命周期与边界。
- [兼容承诺与影响矩阵](COMPATIBILITY.md)：v1 的稳定边界，以及以后 Core 拆包时哪些做法不用重做。
- [两个官方 DLC 案例](OFFICIAL-DLC.md)：**AI 1.0.4（host-only）** 与 **识屏 1.0.4（host-worker）** 如何调用同一套接口，以及哪些是内部接线、不要复制。

两个 DLC 是**生命周期与端口设计的参考**，不是可直接改 ID 发布的第三方模板；第三方请从 `hello-local` / `echo-worker` 起步。真正有长期兼容保证的创作入口是 v1 薄接口。

## 四、已知缺陷（2026-10-10）

ZIP 接入的 MOD 无法启用（用户实机报告，**未解决**）：

- 症状：ZIP 导入的外接 MOD 无法启用；同一份内容走路径（导入目录）接入正常。
- 本轮已证实：ZIP 内多套一层文件夹时会被判成 `导入失败：请选择完整的 MOD ZIP 或包目录。manifest missing`，功能包与角色包都一样；把同一份内容解压后选内层目录即可正常导入、启用。
- 本轮**未能复现**「导入成功但点启用失败」这一形态，触发条件待用户补充（ZIP 来源与打包工具、包类型、界面报错文案）。
- 规避：现阶段优先用「导入目录」。根因定位与修复**未完成**，不要当成已修复。

完整结论、探针数据与复现命令见 [实施计划 · 已知未解决缺陷：ZIP 导入的 MOD 无法启用](MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md)。

## 工程入口

[总索引](../INDEX.md) · [实施计划](MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md) · [交付报告](../PR-REPORT-MOD-CENTER-V1-2026-10-10.md) · [持续状态](../../.scratch/mod-authoring-v1/STATUS.md)。所有命令在仓库根目录执行；推荐独立 Python 虚拟环境，不读取真实用户配置或 Key。

## 实际效果与限制

这是本地 MOD 管理与制作入口，不是联网商店。受信任 Python MOD 不是安全沙箱，只安装可信来源；角色资源不含执行代码。公开的是 v1 薄接口，不是 Core 的全部 Python 模块。
