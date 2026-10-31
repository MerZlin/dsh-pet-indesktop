# DSH v2 真机联调报告（2026-10-31）

> **基线**：`0dadd90a`（反馈批次 2 备份提交；本批改动未提交）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-10-31
> **范围**：4 个产品/测试文件 + 6 个文档（另含本批既有 90 个工作区改动）
> **关联**：[反馈批次 3 报告](PR-REPORT-FEEDBACK-BATCH3-2026-10-31.md)、
> [DSH 协议](AGENT_LINK_PROTOCOL.md)、[互通架构](INTEROP-ARCHITECTURE.md)

## 一、核心特性

用户提供了本机 DSH 桌面端路径（`D:\APP\DSH\DeepSeek Harness.exe`，外壳
44.7.0，内部 `desktopVersion=0.2.0-rc.2`），把上一批唯一没做的「真机联调」
补上。真机上暴露并修复了 5 个问题：v1→v2 升级迁移只改了一半、历史接口把
DSH 注入上下文当用户发言、审批通行证永远匹配不上导致重复弹卡、
`pet_control` 工具静默注册失败、换会话遗留旧审批卡。

| # | 能力 | 说明 |
|---|---|---|
| 1 | v1→v2 升级迁移 | 重指向旧 junction、摘除 profile patch 里的旧 `insert` 条目、清 `node_modules/dsh-pet-channel` 链接 |
| 2 | 历史只回放真人发言 | 只放行 `source.kind=user`；`runtime-context / skill-catalog / time-context / plugin` 全部过滤 |
| 3 | 审批一次性通行证 | 指纹忽略 `description` 等说明字段；同意后重试直接放行，不再二次弹卡 |
| 4 | `pet_control` 真注册 | 标准 JSON Schema；`ping.commands` 如实反映注册结果 |
| 5 | 换会话清审批 | `/pet/new` 与 `cancel` 共用清卡逻辑，旧卡收 `cancelled=true` |

**红线 / 不变量**：仍只绑 `127.0.0.1` + `x-pet-token`；状态 JSONL 仍只写脱敏
元数据；审批仍绑定 `(tool, argsHash)` 且用掉即焚；`/pet/history` 不因过滤而
丢掉真人消息与工具卡。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/platform/dsh_install.py` | +155 / −3 | ① `_link_plugin` 发现已有 symlink/junction 时比较目标，指向旧构建目录就摘掉重建（真实目录保守不动）；② 新增 `_strip_legacy_patch_entries` / `_prune_insert`：按行解析 profile 的 `cordis.patch.yml`，只删除声明旧包名的 `insert` 列表项，注释与其它项字节不变，改前备份 `.dsh-pet-backup-<ts>`；③ `_migrate_legacy` 清 `node_modules/dsh-pet-channel` 旧链接（仅重解析点）。安装成功后执行，失败不回滚主流程。 |
| `integrations/dsh-pet-bridge/channel.js` | 新文件 1078 行（本轮约 60 行） | ① `isInternalUser` 改为「`kind` 非空且非 `user`」即内部；② 新增模块级 `approvalFingerprint(exec)`（排序键、忽略说明字段）；`gate()` 用它做通行证匹配；③ 新增 `clearPending()`，`cancel` 与 `ensureLane(forceNew)` 共用；④ `registerPetControl` 改用标准 JSON Schema（`required: ["action"]`、`additionalProperties: false`）并返回布尔；⑤ `ping.commands` 改读 `petControlReady`。 |
| `tests/test_petcore_dsh.py` | +109 / −10 | 新增 2 个安装器回归：旧 patch `insert` 条目被摘除且其余项保留、旧 bridge junction 被重指向且旧 channel 链接被删；复用 `_junction()`（`mklink /J`，非 Windows 跳过）。 |
| `tests/test_bridge_approval_fingerprint.js` | 新增 51 行 | 4 条：只改 `description` 指纹不变、命令变化指纹变、参数键顺序不影响、注入上下文（4 种 kind）不算真人发言。 |

### 未改动（有意）

- `petcore/net/dsh.py`：客户端只消费 `/pet/history` 与 SSE，过滤口径在插件侧
  收口，客户端无需改。
- `petcore/ui/chat/dsh_view.py`：审批卡与线程模型已在上一批修过，本轮真机只验证。
- 旧 `integrations/dsh-pet-channel/` 目录：v2 起不再安装，但保留源码供回滚对照。

## 三、实现要点

1. **迁移要按「DSH 实际加载什么」清**：DSH 的 loader 同时看 `bundles`、
   `dependencies` 与 profile 的 `cordis.patch.yml`。只删前两处，显式 `insert`
   仍会加载旧包；只删本地插件目录，`node_modules` 里的 junction 仍指向旧构建。
   三处必须一起迁移，且 patch 编辑只删命中项、其余字节原样保留（用户手写的
   配置不能被重排）。
2. **审批指纹只对「要执行什么」负责**：DSH 重试时会改写工具调用的
   `description`，说明文字不属于授权范围。实现按参数键排序后 JSON 序列化，
   忽略 `description/justification/explanation/reason/timeout/timeout_ms`，
   保留 `command/path` 等实质参数。
3. **工具注册必须用标准 JSON Schema**：链接安装的插件解析不到
   `@deepseek-ai/dsh-tools`，`defineTool` 的作者简写路径不可用；
   `tools.register()` 只校验 `output.schema` 是受支持子集（`type/oneOf/
   properties/required/additionalProperties/items/enum/const` + 注释关键字）。
   写 `required: true` 会抛错且被 `catch` 吞掉——所以同时把
   `ping.commands` 改成真实状态，避免再次「静默消失 + 谎报可用」。
4. **历史过滤放在插件侧**：桌宠侧只消费 `items`，过滤口径集中在
   `isInternalUser`，一处修改即对所有通道生效；未知 `source`（旧版本无
   `source` 字段）仍按真人处理，避免误删真实消息。

## 四、性能分析

实测环境：Windows 本机，DSH 44.7.0 常驻，插件 2.0.0，客户端
`DshClient(timeout=8)`。

| 指标 | 实测 |
|---|---|
| 本地 HTTP 延迟（3 次） | `/pet/ping` 1.08–1.21 ms；`/pet/history?limit=200` 1.19–1.44 ms |
| 审批请求 → 客户端 resolved 帧 | 372 ms（客户端 0.5 s 轮询粒度，真实值 ≤372 ms） |
| 允许 → 续跑工具结果 | 2.62 s（含一次完整模型轮 + 工具执行） |
| 历史过滤收益（首会话） | 真人 4 条 / 325 字符；注入 3 条 / **10,292 字符**（技能目录单条 9,314）→ 过滤后少渲染 97% 的用户侧文本 |
| 修复前重复弹卡 | 允许后 4.4 s 出现第二张卡，`pending=1`，命令仍未执行 |
| 修复后重复弹卡 | 0 张；命令真实执行（`Remove-Item` 路径不存在，退出码 1，符合预期） |

- **稳态开销**：`approvalFingerprint` 每次被拦下的工具调用执行一次
  `JSON.stringify`（参数字符串 ≤1.2 KB）+ SHA-1，微秒级；`isInternalUser`
  每条 `user/message` 一次属性读取。无新增线程、定时器、系统调用、网络或磁盘。
- **内存**：`grants` 每会话最多 1 条、用掉即删；`pending` 每会话至多 1 条；
  `frameRing` 512 帧上限不变；`resolvedApprovals` 200 条上限不变。
- **安装器**：多一次 patch 读取/写入（仅在发现旧条目时）与一次 junction
  目标解析；不递归删除、不扫描插件目录。

## 五、实机运行记录

**1）升级前现场**

```
DSH version: 44.7.0
/pet/ping -> {"plugin":"dsh-pet-channel","version":"0.1.0", "askApproval":true}
node_modules/@dsh-pet/bridge -> E:\Work\dsh-pet\dsh-pet-indesktop\...\dist-onedir\...
cordis.patch.yml: - insert: ... name: dsh-pet-channel
```

**2）安装 v2（`PYTHONPATH=. python .scratch/dsh_real_install.py`）**

```
backup: .scratch\dsh-backup-20261031-231220
result: InstallResult(ok=True, ...)
after: bundles=[dsh-base, dsh-web-app, dsh-plugin-marketplace, @dsh-pet/bridge]
patch: dsh-pet-channel 条目已消失；bridge link -> ~/.dsh/local-plugins/dsh-pet-bridge
legacy link exists: False
```

**3）重启 DSH 后**

```
/pet/ping -> {"plugin":"dsh-pet-bridge","version":"2.0.0","commands":true, ...}
```

**4）对话 / 历史**

- 发送「用一句中文回答：你是谁？」→ SSE：
  `lane → user → turn-start → step → assistant → turn-end(completed) → idle`。
- `/pet/history`：修复前含 `runtime-context / skill-catalog / time-context`
  三条伪用户消息；修复后只剩真实 user + assistant。
- 记忆注入：`context={"memory":"【长期记忆】主人最喜欢的颜色是蓝色，第二喜欢绿色。"}`
  → 回答「蓝色，绿色。」；`history` 不出现注入正文。

**5）审批闭环**

- 发送「请用 pwsh 执行：`Remove-Item -LiteralPath E:/.../approval-nonexistent.txt -Force`」
  → `approval-request`（tool=pwsh，含 argsHash）→ `answer(allow=true)` →
  `approval-resolved` → 续跑轮 `tool-result`：`找不到路径…[exit code: 1]`。
- 修复前：允许后第二次审批卡出现、命令未执行；修复后：0 张新卡。

**6）`pet_control`**

- 修复前问模型「有没有 pet_control」→「没有」。
- 修复后发送「请调用 pet_control，action=say，text=DSH 已连接」→
  `tool-call pet_control` → `tool-result ok`；
  `%APPDATA%/dsh-pet-bridge/dsh-petcmd-<pid>.jsonl` 新增
  `{"cmd":"say","text":"DSH 已连接",...}`。

**7）换会话 / 取消**

- `/pet/new` → 旧会话 `archived`，旧待审批收到 `approval-resolved(cancelled=true)`，
  `approvals=[]`，新会话 `history=[]`。
- 长文生成中 `cancel` → `turn-end(reason=unknown)+idle`，无残留审批。

**8）真实 UI（`ChatWindow` + `DshHub` + `DshClient`）**

- 界面输入并发送消息，6 秒内渲染回答（「我是 DeepSeek Harness 中的编码智能体…」）。
- 发送 `Remove-Item` 请求 → 渲染「需要你确认：要跑一下 pwsh」+ 允许/拒绝；
  点「允许」→ 卡片消失、头部「DSH 正在执行…」、续跑命令执行完成；
  窗口全程可响应（连续截图两次均正常渲染，无冻结）。

**9）回归**

```
node --test tests/test_bridge_*.js           -> 48 passed
node integrations/dsh-pet-bridge/verify_import.mjs -> OK
ruff check petcore/ tests/ integrations/     -> All checks passed
QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py -q
  -> 2731 passed, 1 skipped in 192.44s
```

**10）安装包**

```
pwsh -File scripts/build_petcore.ps1 -Clean -Installer -OutputRoot rel-dsh-real-2026-10-31
  -> packaged --check --strict: strict.problems=[] / daily=1200/1200 / version=0.3.0
  -> Inno: Successful compile (100.969 sec)
  -> setup: 301,117,166 B
     SHA-256 8a37990024d068af084d8f94caad5b86da8c1fa637fa8f69bf9abe6e07cb3b92
包内 _internal/integrations/dsh-pet-bridge/channel.js 含 approvalFingerprint；
打包 onedir 真机启动 10 s：进程存活、窗口 dsh-pet-core、Responding=True。
```

## 六、未验证 / 风险

- **真机审批只覆盖 pwsh**：文件写入类工具的「无 path → fail closed」分支未在
  真机触发，仅由既有单测覆盖。
- **DSH 自身审批策略**：本机 session 的 runtime-context 写「Approval prompts
  are disabled」，插件的 `askApproval` 闸是独立拦截层；两者语义不要混用。
- **桌面端升级**：DSH 升级可能改变 `source.kind`、工具定义校验或 loader
  行为；升级后先跑本报告的 3 个探针（ping / history / pet_control）。
- **未修复观察**：联调时桌宠窗口在光标位于角色上时仍带
  `WS_EX_TRANSPARENT`（逐像素穿透轮询判透明），外部自动化点不到角色；
  疑似混合 DPI 下 `mapFromGlobal` 与 `is_transparent_at` 坐标口径不一致，
  本轮未复现/未修，建议单独开单。
- **本机改动**：用户真实 profile 已切到 v2 并加了 `askApproval: true` 覆盖；
  备份在 `.scratch/dsh-backup-20261031-231220/` 与 profile 的
  `*.dsh-pet-backup-*`。旧 `~/.dsh/local-plugins/dsh-pet-channel` 目录保留。
