# host-worker：独立 Echo Worker

[入口](README.md) · [API](API-V1.md) · [源码](../../examples/mods/echo-worker/host/factory.py)

此样例不截图、不访问 Provider。宿主负责菜单 / 设置，Worker 回传输入文本。不要把开发者机器的 Python 绝对路径写进包。

## 构建与校验（Windows）
Windows 构建使用 **CPython 3.11 x64、PyInstaller 6.20.0 和 x64 GCC**（与本仓库 native probe 构建基线一致）。Worker 必须携带相同的 native 启动支持，不能只运行裸 PyInstaller 后把 EXE 放进包。

第一次准备 native 输入（已有本仓库经校验的 `probe-run.exe`、`_dsh_probe_native.pyd`、`native-build.json` 时可复用同一目录）：
```powershell
python -m pip download --no-deps --no-binary=:all: pyinstaller==6.20.0 --dest .scratch/my-mods/inputs
python -m scripts.build_feature_probe_native .scratch/my-mods/native --source .scratch/my-mods/inputs/pyinstaller-6.20.0.tar.gz --compiler gcc
```
脚本检查固定源码 SHA-256，再编译本项目的 native 支持；失败时停止，不手动跳过检查。`gcc` 须在 PATH 中，或换成编译器的绝对路径。下载仅是构建准备，MOD 运行不联网。

构建并校验样例（所有输出目录须尚不存在）：
```powershell
python -m scripts.build_mod_echo .scratch/my-mods/echo-build-1 --probe-bootloader .scratch/my-mods/native/probe-run.exe --native-extension .scratch/my-mods/native/_dsh_probe_native.pyd
python -m scripts.build_mod_example examples/mods/echo-worker .scratch/my-mods/echo-1 --worker-bundle .scratch/my-mods/echo-build-1/dist/mod-echo
python -m scripts.build_mod_example .scratch/my-mods/echo-1 --check
```
第一个命令从闭合源码清单冻结小型 Worker，并审核 PYZ 不含 Qt、GUI、密钥存储或 Agent；同时保存模块清单。源码未变也应核对输入和哈希，不从旧发布包随意拷 EXE。非 Windows 必须在对应平台重建并指定 `--worker-name mod-echo`，不能跨系统复制 Windows 二进制；本轮实测平台为 Windows，其他平台尚未做原生构建验收。

```text
发布包/
  manifest.json       # execution_kind=host-worker；Worker 相对路径与文件清单
  host/               # 只使用公共 v1，不自己拼执行路径
  worker/
    mod-echo.exe
    _internal/         # 随 PyInstaller 生成，完整保留
```
开发源码在 `examples/mods/echo-worker/worker-src/entry.py`，通过 `pet.mod_api.worker_v1.serve` 接入子进程协议。

## 操作与结果
1. 导入、启用。仅启用不会自动启动 Worker。
2. 设置消息并保存；右键 → 扩展 → 离线 Echo。Core 校验包与执行状态、租约交接，Worker 先 HELLO，再接受配置并 READY；返回后显示相同文本。
3. 「取消 Echo」取消该请求并忽略迟到返回；多次点击只保留新请求。处理器必须检查取消 Event，不能无限占用线程。
4. 停用或退出：不再接受新请求，清 pending、发 shutdown、关闭输入，Worker 自然退出并释放租约。

## 作者要点
`WorkerClient(context)` 只使用 Core 提供的已校验 launch；`ready` 后 `request(op, args)` 返回请求 ID，`response` 带该 ID；`cancel(id)` 本地立即丢弃迟到结果并通知子进程；`error` 是脱敏故障码，应显示可操作提示。

Worker handler 接收 `(operation, arguments, cancel_event)`，返回可 JSON 序列化的字典。stdout 专供 JSONL，日志写 stderr；消息有大小上限，不能一次塞整个大文件。handler 不应依赖 Qt 或主进程对象。`serve` 先接管租约，再发送 HELLO；切勿自行删掉这个顺序。

## 常见错误
- `worker_launch_rejected` / `worker_start_failed`：检查完整 Worker 文件、平台及当前启用状态。`worker_handshake_timeout` / `worker_heartbeat_timeout` / `worker_exited` 区分握手、失联与退出；修正后可重试。不要原样显示环境变量或未筛选的子进程异常。
- 持续无 READY：检查相对 EXE 路径、完整 `_internal`、平台、stdout 是否混入 print、handler 导入是否失败。
- 停用后仍显示结果：必须按请求 ID 与当前执行授权过滤；直接连接 QProcess 输出不属于推荐用法。
- 更新等待退出：Worker 或 host 仍持有旧版本，先自然退出，不能假装代码热更新。

## 实际效果与限制
进程分离不是安全沙箱，Worker 仍是受信任可执行程序。该示例仅演示离线传输，屏幕采集和其他跨 DLC 服务不由 v1 自动授予。
