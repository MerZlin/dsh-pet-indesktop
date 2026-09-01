# B9 重做设计：Agent 监视器后台读取线程

> 两轮失败档案：_plan/REVIEW_B9_FINDINGS.md（一审）与三审记录。本设计逐条对应已知坑。

## 核心模型

每个监视器一条专属 Python 线程，自持 1.5s 节奏循环（替代 GUI 线程 QTimer）：
GUI 线程零 I/O；结果经 Qt 信号（QueuedConnection 天然跨线程安全）回 GUI。

```
worker 循环：
  while not stop_event.is_set():
      if not paused_flag: 读文件/SQLite（offset 只在此推进）
      发信号前检查 accepting 标志
      stop_event.wait(1.5)   # 可被打断的睡眠，stop 立即醒
```

## 逐条对策

1. **丢事件（一审 pause 竞态）**：pause 只跳过读取，绝不推进 offset/rowid。恢复后从原位置继续，事件不丢。
2. **迟到信号污染（一审）**：stop() 先清 accepting 再置 stop_event 再 join。worker 发信号前查 accepting。join 成功则旧线程死透，不存在"重启后旧线程信号到达"；join 超时则 start() 拒绝（返回 False，记日志）——**绝不允许双 worker**。
3. **无界 join 冻结 GUI（二审）**：join 上限 2s。worker 每轮 I/O 有界（64KB tail、LIMIT 200、glob 有 50 文件上限），正常退出是毫秒级，2s 是病态兜底而非日常路径。
4. **旧窗口/角色切换泄漏（一审）**：PetWindow.closeEvent 和 PetApp.switch_character 都调 agent_link_manager.shutdown()（幂等 stop 全部 monitor）。
5. **OpenCode 常驻连接（一审）**：连接只在 worker 线程创建/使用/关闭（sqlite 线程亲和），stop 时 worker 自己关；join 超时的病态情况连接随线程死亡由 GC 收。
6. **子代理过滤留在读取层**（worker 内查 session.parent_id），不挪位置。

## 明确的非目标

- 不做统一线程管理器/任务框架（过度设计，这批只要生命周期正确）
- 不改 AgentLinkManager 的气泡/动画/音效调度逻辑
- 信号签名不变（state_changed(str, str) / activity(str, str)）——污染过滤靠 accepting+join 时序，不加代次字段

## 测试设计（全部确定性，无 sleep 猜时序）

- pause 期间写入事件文件 → 不读 → resume 后事件完整送达（不丢）
- stop 后旧事件文件再写入 → 无信号（accepting 已清）
- stop→start 快速循环 → 同一时刻最多一个 worker 线程
- join 超时（worker 被事件卡住不退出）→ start 拒绝且记日志
- closeEvent/角色切换 → 全部 monitor 停止
- OpenCode：常驻连接在 worker 线程使用；库文件删除后查询报错 → 降级不崩
