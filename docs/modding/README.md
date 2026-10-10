# MOD 使用与开发入口（v1）

适用候选：Core **4.2.5**；AI / 识屏 DLC **1.0.4**。工程完成度以[持续状态](../../.scratch/mod-authoring-v1/STATUS.md)及本轮交付报告为准，教程不是发布公告。

| 你想做什么 | 从这里开始 |
|---|---|
| 导入、启停、更新、批量删除别人做的 MOD | [用户指南](USER-GUIDE.md) |
| 换角色或替换动画 | [角色素材教程](CHARACTER-PACKS.md) |
| 只改台词、人格口吻 | [台词模板教程](PERSONA-TEMPLATES.md) |
| 增加菜单和设置，不需要独立进程 | [host-only 样例](HOST-ONLY.md) |
| 在独立进程执行耗时任务 | [Echo Worker 样例](HOST-WORKER.md) |
| 查具体方法、生命周期与边界 | [公共 v1 接口](API-V1.md) |
| 看现有 AI、识屏怎么使用接口 | [官方 DLC 案例](OFFICIAL-DLC.md) |
| 担心以后 Core 拆包要重做 MOD | [兼容承诺和影响矩阵](COMPATIBILITY.md) |

工程入口：[总索引](../INDEX.md)、[实施计划](MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md)。样例源码位于 [examples/mods](../../examples/mods/README.md)。所有命令在仓库根目录执行；推荐独立 Python 虚拟环境，不读取真实用户配置或 Key。

## 实际效果与限制
这是本地 MOD 管理中心，不是联网商店。受信任 Python MOD 不是安全沙箱；只安装可信来源。角色资源没有执行代码。公开的是 v1 薄接口，不是 Core 全部 Python 模块。
