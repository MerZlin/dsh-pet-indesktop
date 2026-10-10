# MOD 制作手册与注意事项

**基线**：Core **4.2.5**、AI / 识屏 DLC **1.0.4**、`pet.mod_api.v1`；记录日期 2026-10-10。适用于本地受信任 MOD。工程完成度以[持续状态](../../.scratch/mod-authoring-v1/STATUS.md)与[交付报告](../PR-REPORT-MOD-CENTER-V1-2026-10-10.md)为准。

本手册是作者向的**唯一完整参考**：每种 MOD 怎么做、放哪里、接口是什么、有哪些坑。快速入门与按类型教程见[指南入口](README.md)；方法级契约见[API-V1.md](API-V1.md)；官方 DLC 案例见[OFFICIAL-DLC.md](OFFICIAL-DLC.md)；以后 Core 拆包的影响见[COMPATIBILITY.md](COMPATIBILITY.md)。

## 0. 先分清三种 MOD、四种形态

| 形态 | 执行代码 | 可启停 / 卸载 | 制作入口 | 接口 |
|---|---|---|---|---|
| 角色 / 动画**资源包** | 否 | 是（启用 + 使用） | `scripts/build_character_mod_example` | 只有 manifest（本文 §2） |
| **台词 / 人格模板** | 否 | 否，是文本导入 | 手写 JSON | `persona-phrases/v1`（本文 §3） |
| 功能扩展 · **host-only** | 是（与 Core 同进程） | 是 | `scripts/build_mod_example` | `pet.mod_api.v1`（本文 §4） |
| 功能扩展 · **host-worker** | 是（独立进程） | 是 | `build_mod_echo` + `build_mod_example --worker-bundle` | `pet.mod_api.v1` + `worker_v1`（本文 §5） |

不需要跑独立进程就不要做 host-worker。**只有需要跑耗时或容易崩的任务才拆 Worker。**

## 1. 所有 MOD 的通用规则

**包布局**：包根必须直接有 `manifest.json`；打成 ZIP 时 `manifest.json` 要在**压缩包根**，多套一层文件夹会被判 `manifest missing`（导入直接失败）。

**放哪里**（`<项目目录>` 是有 `portable.json` 的 Setup 目录；开发模式按当前 Runtime Layout 的数据根替换 `data`）：

```text
<项目目录>/
  data/
    characters/<角色ID>/videos/                      # 手动角色素材（不需要包 manifest）
    content/characters/<角色ID>/versions/<版本>/      # 受管理的角色包
    plugins/<功能ID>/versions/<版本>/                 # 受管理的功能包
    feature-data/<功能ID>/                            # 功能个人数据；删除 MOD 会保留
    feature-runtime/                                  # Worker 运行目录（项目内可信路径）
```

管理器的活动版本状态（`active.json`、账本、事务日志）**不要手工编辑**。外部 ZIP / 源目录由管理器之外保管，删除 MOD 不会动它。

**身份与版本**：包 `id` 与代码里的 owner 必须一致（两个位置一起改）；改名不改 ID 会覆盖同 ID 的包。改了内容必须升 `version`——同版本同内容会被判「已存在，不重复导入」，同版本不同内容必须换版本号重打包。用 `core_requires` 声明最低 Core。

**信任模型**：本地 MOD 是**受信任的 Python**，不是安全沙箱。host 与 Core 同进程；Worker 只是进程隔离。只安装你信任来源的包。**不要把 Key 写进包、配置命名空间或日志。**

**已知缺陷（2026-10-10，未解决）**：用 ZIP 导入两个**示例功能包**（`hello-local`、`echo-worker`）会报 `worker_probe_failed`、操作未完成；改用**「导入目录」正常**；角色资源包走 ZIP 正常。做功能扩展现阶段请优先用目录导入。详见[指南的已知缺陷](README.md)与[实施计划](MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md)。

## 2. 角色 / 动画资源包

纯内容，**不执行 Python**。最快做法（输出目录必须不存在；脚本会复制内置 `content/characters/shenshen` 并把待机动作换成挥手，便于肉眼确认真的加载了副本）：

```powershell
python -X utf8 -m scripts.build_character_mod_example .scratch/my-mods/mod-demo-shenshen
```

手工制作则复制一个现有角色目录，改 `id` / `name` / `version`，替换媒体后重算 `integrity.sha256`。

**包内布局**

```text
我的角色包/
  manifest.json
  videos/
    manifest.json
    idle/待机呼吸休闲.webm
    click/点击回应-元气挥手.webm
    ...
```

**顶层 `manifest.json` 字段**

| 字段 | 说明 |
|---|---|
| `id` | 包 ID；换新角色必须改，不能只改文件夹或显示名 |
| `name` / `description` | 列表显示用；旧包缺省时回退到 ID 与「作者未提供说明」 |
| `version` | 改内容必须升版本 |
| `kind` | 固定 `"content"`；导入时据此路由到角色资源 |
| `api_version` | 目前 `"1"` |
| `core_requires` | 例如 `">=4.2.5,<6.0.0"` |
| `platforms` | `["windows","macos","linux"]` 之类 |
| `dependencies` | 目前空数组 |
| `capabilities` | 角色包用 `["character","animation"]` |
| `entrypoint` | 资源包为 `null`（资源包不执行代码） |
| `content.characters` | 本包提供的角色 ID 列表，权威依据 |
| `integrity.sha256` / `signature` | 内容摘要；由打包 / 制作脚本重算，不要复制旧值 |

**媒体约定**：透明 WebM / VP9，沿用原目录的动作命名与 `videos/manifest.json`；画布、帧率、时长要匹配对应动作；丢失透明通道会变黑底。`body_box` 是源像素里的稳定身体区域、`head_box` 是头部交互区域——换体形要**重新测量**，不要照抄别的角色；省略 `body_box` 会回退到整画布定位，不等于自动检测。

**怎么装**：设置 → 扩展管理 → 导入；新包默认停用。**「启用」只加入可选列表，点「使用」才给当前实例换装**，其他实例不跟着换。停用 / 删除使用中的包时，受影响实例先切回内置角色。

**手动素材**：直接把视频目录放到 `data/characters/<角色ID>/videos/` 也能用（不需要包 manifest），但它是兼容来源，**没有版本 / 启停 / 删除生命周期**，管理中心会标注「手动素材」且不提供删除。

## 3. 台词 / 人格模板（`persona-phrases/v1`）

这是**已有设置的导入 / 导出格式**，不是可卸载的功能包，不会出现在 MOD 列表里。无需写 Python。

```json
{
  "template": "persona-phrases/v1",
  "mode": "custom",
  "name": "我的问候风格",
  "phrases": { "thinking": ["让我想一想……"] }
}
```

| 字段 | 说明 |
|---|---|
| `template` | 固定 `"persona-phrases/v1"` |
| `mode` | 表达风格模式 |
| `name` | 模板名 |
| `phrases` | **事件名 → 字符串列表** 的映射；从「导出台词模板」的空白模板复制真实事件名与占位符，不要自己发明事件 |
| `entries` / `variables` / `agents` | 可选，属现有编辑信息；最小例子不需要 |

保留所用事件要求的占位符（例如 `{text}`）；不要写入 Key 或个人聊天记录。导入是**文本粘贴**（不是选文件），导入后还要在编辑页保存才正式生效。

校验：`python -X utf8 -m json.tool examples/mods/persona-phrases.json`（语法有效只是第一步，仍要实际导入、保存、触发事件）。示例：[examples/mods/persona-phrases.json](../../examples/mods/persona-phrases.json)。

## 4. 功能扩展 · host-only

与 Core 同进程，适合菜单 + 设置 + 轻量逻辑。样例：[examples/mods/hello-local](../../examples/mods/hello-local/host/factory.py)。

**作者源码布局**（`manifest.json` 由打包器生成，不要手写）

```text
我的功能包源码/
  mod.json              # id / name / description / version / factory / execution_kind
  host/
    __init__.py
    factory.py          # create_host() -> FeatureDefinition
    settings.py         # QWidget + 草稿/保存/放弃/释放协议
```

**打包与自检**

```powershell
python -X utf8 -m scripts.build_mod_example examples/mods/hello-local .scratch/my-mods/hello-local-1
python -X utf8 -m scripts.build_mod_example .scratch/my-mods/hello-local-1 --check
```

发布包只含 `host/` 与 `manifest.json`（后者含文件大小与 SHA-256 清单），不需要带开发环境、密钥或 `.pyc`。

**接口 A：`create_host()`**

```python
from pet.mod_api.v1 import Contribution, FeatureDefinition

def create_host():
    return FeatureDefinition(OWNER, (Contribution("hello", "menu", command="hello", factory=menu),),
                             settings, runtime_factory=lambda context, **kw: Runtime(context))
```

| `FeatureDefinition` 字段 | 说明 |
|---|---|
| `owner` | 必须等于包 `id` |
| `menus` | `Contribution` 元组 |
| `settings_factory` | `settings_factory(context, parent=None) -> QWidget`（或带 `.widget` 的组件） |
| `runtime_factory` | `runtime_factory(context, **kwargs) -> Runtime` |
| `manual_factory` / `policy_factory` / `worker_launch_factory` | 官方接线用；普通 v1 MOD 不用，host-only 不能声明 Worker |
| `allow_in_process` | host-only 须为 `True`（默认） |
| `mount_contract` | 由 `pet.mod_api.v1.FeatureDefinition` 默认设为 `"mod/v1"`，Core 据此通用挂载；**不要给旧 AI / 识屏包补这个值**，否则重复挂载 |

| `Contribution` 字段 | 说明 |
|---|---|
| `id` | 包内唯一 |
| `kind` | `"menu"` |
| `command` | 对应 `Runtime.commands` 的键 |
| `factory` | 菜单构建函数，签名 `(menu, handle)`；省略时显示贡献 ID |

菜单回调里先判断 `handle.active` 再 `handle.invoke()`；不要保存 Core 窗口对象。`handle.descriptor.id` 可区分同一 factory 的多个贡献（见 Echo 样例）。

**接口 B：`Runtime`**——在 GUI 线程提供 `commands: dict[str, callable]`、`start()`、`stop()`、`close()`。启用调 `start`，停用撤销旧命令并 `stop`，Core 退出 `close`。`start`/`stop` 要可重复；`close` 释放定时器、窗口、连接、任务；不要阻塞 GUI；停用后旧菜单句柄不能再执行。

**接口 C：设置组件协议**（`settings_factory` 的返回值必须实现）

| 方法 | 约定 |
|---|---|
| `dirty()` | 是否有未保存编辑 |
| `draft()` | 返回自身草稿，不写磁盘 |
| `confirm_save()` | 校验 / 原子保存；成功 `True`，失败 `False` 并保留编辑 |
| `discard_changes()` | 重新载入已保存值 |
| `dispose()` | 断开连接、取消任务、释放资源；不保存草稿 |

**接口 D：`FeatureHostContext` 端口**（Core 绑定，不要自己构造）

| 端口 | 用法 |
|---|---|
| `context.execution_authorized()` | 执行前确认当前授权仍有效 |
| `context.configuration.revision()` / `read_namespace()` / `commit_namespace(values, expected_revision=...)` | 自己的配置命名空间；提交有版本冲突检测，冲突时保留草稿、不要假装保存成功 |
| `context.documents["name"].read()` / `.write(value)` | 单拥有者文档（例如计数 / 记忆） |
| `context.state_documents["name"]` | 跨进程读改写用：`with state.locked(): ...` |
| `context.api.resolve(purpose)` | 每次请求前解析，得到 `ApiRequest` 不可变快照；不要缓存 Key 或旧模型 |

可用 `purpose`：`chat.send`、`files.interpret`、`balance.query`、`manual_look`、`analyze_frame`。`ApiRequest` 只解析配置、**不替你发网络请求**；字段与辅助方法（`effective_version` / `metadata` / `subscribe`）以 `pet/api_ports.py` 的公开值对象为准。Key 不落盘、不拼进错误消息。

`context` 上还有供旧官方接线用的端口（`desktop`、`window`、`user_data` 等），**通用 v1 MOD 不应假定它们可用**；v1 不提供任意桌宠窗口操作、截图授权或跨 DLC 对象访问。

**注意**：`factory.py` 的导入与 `create_host()` 必须**无副作用、可在没有 GUI 的环境下导入**——不要创建 `QApplication`、联网、截图或起线程；Qt、Settings 类、业务依赖放进 `create_settings` / Runtime 构造里延迟导入（生产探针没有 Qt，顶层 import `QWidget` 会让包直接校验失败）。

## 5. 功能扩展 · host-worker

宿主负责菜单 / 设置，Worker 在独立进程干活。样例：[examples/mods/echo-worker](../../examples/mods/echo-worker/host/factory.py)。

**发布包布局**

```text
发布包/
  manifest.json       # execution_kind=host-worker；含 Worker 相对路径与文件清单
  host/               # 只用公共 v1，不自己拼执行路径
  worker/
    mod-echo.exe
    _internal/        # PyInstaller 生成，完整保留
```

**构建（Windows：CPython 3.11 x64、PyInstaller 6.20.0、x64 GCC，与本仓库 native probe 基线一致）**

```powershell
# 1) 首次准备 native 输入
python -X utf8 -m pip download --no-deps --no-binary=:all: pyinstaller==6.20.0 --dest .scratch/my-mods/inputs
python -X utf8 -m scripts.build_feature_probe_native .scratch/my-mods/native --source .scratch/my-mods/inputs/pyinstaller-6.20.0.tar.gz --compiler gcc
# 2) 冻结 Worker，再打成包
python -X utf8 -m scripts.build_mod_echo .scratch/my-mods/echo-build-1 --probe-bootloader .scratch/my-mods/native/probe-run.exe --native-extension .scratch/my-mods/native/_dsh_probe_native.pyd
python -X utf8 -m scripts.build_mod_example examples/mods/echo-worker .scratch/my-mods/echo-1 --worker-bundle .scratch/my-mods/echo-build-1/dist/mod-echo
python -X utf8 -m scripts.build_mod_example .scratch/my-mods/echo-1 --check
```

不能只跑裸 PyInstaller 就把 EXE 塞进包；非 Windows 必须在对应平台重建并指定 `--worker-name`，不能跨系统复制二进制。

**接口 E：宿主侧 `WorkerClient`**（只在 Runtime 构造里导入；Core 只给已校验的启动授权，永远不传入 EXE 路径）

| 成员 | 约定 |
|---|---|
| `start() -> bool` | 请求启动；返回「流程已开始」，**不等于 READY** |
| `is_ready` | 当前授权有效且已完成 HELLO / READY |
| `request(operation, arguments=None) -> str \| None` | 只能 READY 后发；返回请求 ID，自己保存对应 UI |
| `cancel(id) -> bool` | 取消已知请求并过滤迟到响应 |
| `stop()` | 取消 pending、请求自然退出 |
| `close()` | 再解除生命周期绑定，不可复用 |
| `ready` / `response(request_id, dict)` / `error(str)` | 信号；`error` 是脱敏故障码，应显示可操作提示 |

**接口 F：Worker 侧 `serve`**

```python
from pet.mod_api.worker_v1 import serve

def echo(operation, arguments, cancel):
    if cancel.is_set():
        return {}
    return {"echo": str(arguments.get("text", ""))[:4096]}

if __name__ == "__main__":
    raise SystemExit(serve("demo.echo-worker", echo))
```

handler 签名 `(operation, arguments, cancel_event) -> dict`，返回可 JSON 序列化的字典；**stdout 专供 JSONL 协议，日志写 stderr**；消息有大小上限。handler 里不要依赖 Qt 或主进程对象，必须自己设超时或检查 cancel Event——取消是**合作式**的。`serve` 的顺序是**先接管租约再发 HELLO**，不要改。

**启用不会自动启动 Worker**（样例里 `start()` 是空实现）；停用时不再接受新请求、清 pending、发 shutdown、关闭输入，等 Worker 自然退出并释放租约。

## 6. 接口速查

| 对象 | 关键成员 | 属于 |
|---|---|---|
| 包 `manifest.json` | 功能包：`id/name/description/version/format_version/key_id/api_version/core_requires/platforms/capabilities/worker/files`；资源包：`kind/content.characters/integrity` | 全部 |
| `pet.mod_api.v1.FeatureDefinition` | `owner`、`menus`、`settings_factory`、`runtime_factory`、`allow_in_process`、`mount_contract="mod/v1"` | 功能扩展 |
| `Contribution` | `id`、`kind`、`command`、`factory(menu, handle)` | 功能扩展 |
| `Runtime` | `commands`、`start()`、`stop()`、`close()` | 功能扩展 |
| `SettingsComponent` | `dirty/draft/confirm_save/discard_changes/dispose` | 功能扩展 |
| `FeatureHostContext` | `execution_authorized()`、`configuration`、`documents`、`state_documents`、`api` | 功能扩展 |
| `ApiRequest` | 每次请求的地址 / 模型 / 超时 / TLS / 临时 Key 快照 | 功能扩展 |
| `WorkerClient` | `start/is_ready/request/cancel/stop/close` + `ready/response/error` | host-worker |
| `worker_v1.serve` | `serve(owner, handler)`；handler `(operation, arguments, cancel_event) -> dict` | host-worker |
| `persona-phrases/v1` | `template/mode/name/phrases` | 台词模板 |

## 7. 制作注意事项清单

1. 包根有 `manifest.json`；ZIP 时不套外层文件夹。
2. 唯一 `id`，与代码 owner 一致；改内容必须升 `version`。
3. `create_host()` 无副作用、无 Qt 顶层导入。
4. 只 import `pet.mod_api.v1`；不碰 `pet.app`、`PetWindow`、全局 `Config`、其他 DLC 私有模块。
5. `start`/`stop` 可重复，`close` 释放一切；不阻塞 GUI。
6. 设置组件实现五个协议方法；保存原子、失败保留编辑；不用后台刷新覆盖草稿。
7. 数据走 `configuration`/`documents`/`state_documents`，不自己拼路径；Key 不落盘、不进日志。
8. 需要独立进程才做 host-worker；取消要合作式；`_internal/` 完整保留。
9. 资源包不改 ID 就换媒体会覆盖同 ID 素材；`body_box`/`head_box` 换体形要重测。
10. 保留最小回归：导入停用 → 启用 → 设置保存 → 执行 → 停用取消 → 更新 → 删除；保留源 ZIP。
11. 发布不覆盖同版本不同内容；旧代码仍被占用时等自然退出。
12. 功能扩展现阶段优先用「导入目录」（ZIP 已知缺陷未解决）。

## 实际效果与限制

按本手册可以独立做出四类 MOD：资源包换角色与动画、台词模板改口吻、host-only 加菜单与设置、host-worker 在独立进程跑任务；都只通过 `pet.mod_api.v1` 公开契约接入，Core 内部搬家不需要重做。限制：本地管理、不是联网商店；受信任 Python 不是沙箱；v1 不提供桌宠窗口操作、截图授权或跨 DLC 对象访问；host 的额外第三方依赖不属于自动打包承诺（重依赖请自带 Worker）。ZIP 导入功能扩展的缺陷**尚未修复**。
