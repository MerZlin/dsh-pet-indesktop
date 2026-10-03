# Phase 4B-1：唯一安装状态与安全恢复实施报告

日期：2026-09-30。源码基线 `ce164847dc1f3553a2e62d0cb5dc13c2fb9bd339`，本轮未提交、未推送。

权威合同：[4B 设计 §2.1](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；[任务](../.scratch/phase4b-local-management/PLAN.md)、[交接](../.scratch/phase4b-local-management/HANDOFF.md)、[状态](../.scratch/phase4b-local-management/STATUS.md)。

## 1. 目标与当前判断

仅实现状态层：严格校验、revision CAS、operation ID 幂等、状态提交中断与损坏恢复、现有签名验证器的只读衔接。不解压、移动或删除版本，不加载 factory，不启动 Worker，不改变生产调用方。

状态层实现及本轮自动化验收已完成：全量 3547 passed、12 skipped、13 warnings；真实进程竞争与中断族连续三次通过。4B-2 租约、4B-3 安装事务、4B-4 管理 UI、4B-5 冻结产物完整使用流程均未开始。

## 2. 修改文件说明

以下为相对 ce16484 的本轮增量。已有文件使用 `git diff --numstat`，新增未跟踪文件按完整文本行数记为新增；没有文件删除或移动。原始日志留在忽略目录，不代替本报告。

| 文件 | 新增/删除行 | 改了什么与原因 |
|---|---:|---|
| `pet/feature_install_state.py`（新增） | +461 / −0 | 不可变状态、严格 JSON、CAS/幂等、提交证据、安全恢复与签名描述解析；集中唯一权威，不侵入角色 DLC 格式。 |
| `pet/feature_state_io.py`（新增） | +143 / −0 | 有界读取、路径检查、同目录原子写和区分错误的内核锁；避免把权限错误视为竞争。 |
| `tests/test_feature_install_state.py`（新增） | +817 / −0 | 86 项行为测试；真实竞争、持锁进程退出、八个提交中断点和临时签名包。 |
| `docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md` | +32 / −1 | 细化 4B-1 数据/恢复合同、frontier 证据、性能及后续接入限制。 |
| `.scratch/phase4b-local-management/PLAN.md` | +11 / −7 | 稳定任务编号、red/green 与逐门完成状态；后续门保持未开始。 |
| `.scratch/phase4b-local-management/HANDOFF.md` | +36 / −1 | 当前准确停点、实际命令、性能和限制；保留历史检查点。 |
| `.scratch/phase4b-local-management/STATUS.md` | +9 / −4 | 区分实现/自动化/人工/提交状态，说明下一步及无新增用户操作。 |
| `docs/PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md`（新增） | +203 / −0 | 本轮逐文件、性能、真实 Windows 进程/文件系统证据及可复现命令。 |
| `docs/INDEX.md` | +3 / −2 | 登记报告并同步设计、交接与状态导航。 |
| `LOG.md` | +6 / −0 | 追加本轮实现与验证事实，不倒写历史结果。 |
| `LOG-INDEX.md` | +1 / −0 | 新增本轮日志/报告入口，不作为第二份状态权威。 |

## 3. 实现合同与 TDD 证据

- `state.json` 是唯一安装状态权威。`transactions/op-*.json` 保存准备/提交/中止记录；`commit-frontier.json` 仅证明最后一次提交尝试，不能选择 active 或执行代码。
- 正常提交：持锁重读 → CAS/幂等 → prepared → frontier → state 原子替换 → committed。每次成功只递增一次 revision。
- 准备前/替换前退出保留旧状态；替换后可由一致的准备证据补齐提交。旧操作重试只能得到原收据，不能覆盖新的卸载状态。
- 缺少最新收据、frontier、未知后续提交、冲突证据或未来 schema 时不猜测恢复；损坏原件另存用于诊断。只有可证明的最新确切提交才能恢复。
- `resolve_verified()` 锁外校验签名、完整文件及 manifest 原始字节摘要，锁内复查 revision。返回的是瞬时描述，不是版本租约或持续执行许可。
- 原子写覆盖刷新/fsync/replace 和进程中断，不承诺所有文件系统上的断电持久性。读取和写入均不执行包代码。

先建可收集接口骨架，再记录行为失败：初始 24 failed；恢复/证据缺口分别出现 4、2、1、3 个断言失败，修复后转绿。锁类型用例先实际观察到八次查询创建八个 ctypes 结构类型（1 failed / 85 deselected），改为惰性配置并复用原生函数/类型后通过。不是以文件缺失或收集错误作为 red。

中途曾出现测试清理挂起：终止持锁子进程后父进程再次设置其 multiprocessing Event。通过栈定位改为进程退出后不触碰被终止方同步原语，仅清理本测试持有的子进程；属于本轮测试基础设施问题，不是生产锁死。未删除断言或新增 skip 规避。

## 4. 性能分析

环境：Windows `10.0.26100`，Python `3.11.1`，同一仓库所在 E 盘临时目录，无真实应用数据。50 次递增提交；在 50 条收据下读取 100 次；另 100 次读取用 tracemalloc 采样，不新增长期 soak。

| 路径 | 样本 | 中位 ms | p95 ms |
|---|---:|---:|---:|
| 提交（历史从 0 增至 50） | 50 | 67.219 | 116.047 |
| 提交锁持有 | 50 | 65.924 | 114.734 |
| 读取（50 条收据） | 100 | 38.119 | 71.752 |
| 读取锁持有 | 100 | 37.399 | 69.489 |

- 50 次提交产生 200 次 fsync、200 次 replace；100 次只读查询没有 fsync/replace。最终 53 个文件、41,217 字节（50 收据 + frontier + state + 锁文件）。
- 100 次额外读取的 Python traced retained 30,473 字节、peak 1,182,796 字节；不是 RSS，不涵盖原生分配，不据此承诺长期内存稳定。
- 本模块不创建线程、进程、定时器或网络请求；成本仅在调用时发生。当前没有生产调用方，因此没有新增常驻轮询。测试子进程只为验证并发/崩溃。
- **读取会遍历历史证据，随记录增长；不可直接用于 GUI 高频授权检查。**4B-2 接入前必须明确后台读取/有界快照及 revision 失效策略，不能为了提速跳过安全校验。本轮不加缓存、不扩展通知系统。
- Windows LockFileEx 配置惰性初始化一次；不再每次查询创建新 ctypes 类型。前期测量与最终测量环境负载不严格一致，不声称数字差异都是优化收益。
- 记录上限 4,096；达到上限明确拒绝继续提交，不自动删除恢复证据。维护/压缩策略尚未实现。

复现实测的最小步骤：在仓库根目录，使用临时数据根创建 `FeatureInstallStateStore`；循环 `expected_revision=0..49` 和唯一 `operation_id=bench-N`，提交 `StateChange({"1.0.0": "a" * 64}, active="1.0.0", enabled=bool(N % 2))`；随后 `read()` 100 次。使用 `perf_counter_ns()` 计时、`statistics.median` 及排序后第 `int((N-1)*0.95)` 个样本计算表格；上下文包装 `feature_state_io.state_lock` 只测进入后至退出前的持有时间。`os.fsync`/`os.replace` 包装器计数但委托真实调用；内存采样前撤销包装及样本列表增长，gc 后启动 tracemalloc，读取 100 次后 gc 再记录。所有临时目录完成后清理，不生成安装包。

实际测量命令（本地临时助手不纳入版本控制）：

```powershell
python -c "exec(open('.scratch/phase4b-local-management/bench_state.py', encoding='utf-8-sig').read())"
```

下面提供等价的完整复现命令，不依赖该被忽略助手或原始 JSON。它只在仓库 `.scratch` 下创建并清理本次临时数据根，真实委托内核锁和文件操作；运行时负载不同会改变耗时，不要求数字逐位相等。

```powershell
@'
"""Local bounded metadata benchmark; no real application data or feature execution."""
import gc
import hashlib
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from pet.feature_install_state import FeatureInstallStateStore, StateChange
from pet import feature_state_io as io

root = Path.cwd()
report = {'platform': platform.platform(), 'python': platform.python_version(), 'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('pet/feature_install_state.py'), Path('pet/feature_state_io.py')]}}

def stats(samples):
    ordered = sorted(samples)
    return {'n': len(ordered), 'median_ms': round(statistics.median(ordered), 3), 'p95_ms': round(ordered[int((len(ordered) - 1) * .95)], 3)}

with tempfile.TemporaryDirectory(prefix='state-bench-', dir=root / '.scratch/phase4b-local-management') as temp:
    store = FeatureInstallStateStore(Path(temp))
    native_lock, native_fsync, native_replace = io.state_lock, io.os.fsync, io.os.replace
    held, commits, reads = [], [], []
    calls = {'fsync': 0, 'replace': 0}
    @contextmanager
    def measured_lock(*args, **kwargs):
        with native_lock(*args, **kwargs):
            start = time.perf_counter_ns()
            try:
                yield
            finally:
                held.append((time.perf_counter_ns() - start) / 1e6)
    def fsync(fd):
        calls['fsync'] += 1
        return native_fsync(fd)
    def replace(*args, **kwargs):
        calls['replace'] += 1
        return native_replace(*args, **kwargs)
    io.state_lock, io.os.fsync, io.os.replace = measured_lock, fsync, replace
    try:
        for revision in range(50):
            start = time.perf_counter_ns()
            store.commit(StateChange({'1.0.0': 'a' * 64}, active='1.0.0', enabled=bool(revision % 2)), expected_revision=revision, operation_id=f'bench-{revision}')
            commits.append((time.perf_counter_ns() - start) / 1e6)
        report['commit'] = stats(commits)
        report['commit_lock_held'] = stats(held)
        report['commit_syscalls'] = dict(calls)
        held.clear()
        calls.update(fsync=0, replace=0)
        for _ in range(100):
            start = time.perf_counter_ns()
            assert store.read().state.revision == 50
            reads.append((time.perf_counter_ns() - start) / 1e6)
        report['read_at_50_receipts'] = stats(reads)
        report['read_lock_held'] = stats(held)
        report['read_syscalls'] = dict(calls)
    finally:
        io.state_lock, io.os.fsync, io.os.replace = native_lock, native_fsync, native_replace
    files = [p for p in Path(temp).rglob('*') if p.is_file()]
    report['disk'] = {'files': len(files), 'bytes': sum(p.stat().st_size for p in files)}
    # No lock-duration list or other growing benchmark instrumentation here.
    for _ in range(10):
        store.read()
    gc.collect()
    tracemalloc.start()
    for _ in range(100):
        store.read()
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report['memory_100_reads'] = {'retained_python_bytes': retained, 'peak_python_bytes': peak, 'note': 'tracemalloc, not process RSS; native allocations excluded'}
    report['final_revision'] = store.read().state.revision
print(json.dumps(report, ensure_ascii=False, indent=2))
'@ | python -
```

## 5. 实机运行记录

本机真实 Windows 文件系统与内核锁：真实 spawn 的两个进程竞争同一 revision，恰好一方成功；持锁进程异常退出后另一方可继续；真实 `os._exit(73)` 覆盖 prepared 临时/落盘、frontier 临时/落盘、state 临时/替换、receipt 临时/提交八个中断点。不是仅 mock CAS 或用睡眠猜时序。

命令：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_feature_install_state.py tests/test_feature_packages.py tests/test_feature_config_ports.py tests/test_feature_settings_ports.py tests/test_screen_configuration.py
# 216 passed, 1 skipped in 14.73s
python -m pytest -q tests/test_feature_install_state.py -k "two_real_processes or kernel_lock_busy or real_process_interruption"
# 连续三次：10 passed, 76 deselected；3.67s / 3.76s / 3.82s
```

不读取真实 keyring，不截图，不调用模型。签名测试使用临时 Ed25519 密钥，factory 故意设置为执行即报错，以验证状态解析不会执行它。

本步未接入桌宠启动/UI，未重建冻结产物，没有新的可见桌面操作需要验收。当前版本识屏、真实安全存储、自动识屏及托盘退出仍未获用户验收；Windows 文件系统实测和 offscreen 回归不替代这些人工门。POSIX 锁分支未在本机实跑。

## 6. 验证结果

| 项目 | 本轮结果 |
|---|---|
| 状态 + 包验证 + 配置相关回归 | 216 passed，1 skipped，14.73s |
| 真实进程竞争与中断短程重复 | 3/3；每次 10 passed |
| Ruff lint | 通过 |
| Ruff format | 463 files already formatted |
| mypy（两个新增模块） | 通过 |
| 全量 pytest | 3547 passed、12 skipped、13 warnings；343.74s |
| 文档/报告/产品文案/差异/保护摘要 | 114 份 Markdown 链接通过；报告纪律 + desktop features 140 passed / 12.62s；diff check 通过；三份保护文件摘要不变 |

与 ce16484 之前最近一次已留档全量的 3459 passed / 12 skipped / 14 warnings 相比：新增 88 个通过项（86 个状态测试、2 个新报告纪律参数项）；12 个 skip 未变。13 个 warning 仍为原有 Qt QImage.mirrored/QHoverEvent 弃用提示；此次未再出现 `test_try_move_success_still_builds_plan_and_moves` 的一次 QImage.mirrored 提示。该测试和相关产品代码未修改、未增加过滤或跳过，不把提示次数变化当作产品修复。

静态命令：

```powershell
python -m ruff check pet features tests scripts packaging/phase4a_synthetic_worker.py packaging/phase4a_validation_entry.py
python -m ruff format --check pet features tests scripts packaging/phase4a_synthetic_worker.py packaging/phase4a_validation_entry.py
python -m mypy pet/feature_install_state.py pet/feature_state_io.py
python -m pytest -q
python scripts/check_docs.py
python -m pytest -q tests/test_pr_report_discipline.py tests/test_desktop_pet_features.py
```

## 7. 限制与回滚

状态摘要不是发布者身份认证，包仍需现有签名和文件校验。记录损坏能安全拒绝，但不能抵抗有能力将**全部**状态及证据一致回滚/改写的本地攻击者；不声称防篡改账本。缺证据时只能提示人工修复，不扫描残留版本推断安装。

尚无跨进程版本租约、安装/卸载文件事务、应用内管理、状态通知、默认构建切换或新用户操作。描述返回后可能失效，4B-2 必须在协调锁下接续占用与授权。当前不提交、推送；以后独立提交可分别回滚新增服务/测试/本轮记录，不影响当前识屏或 Phase 1 资源状态。

## 8. 实际可体验效果与限制

桌宠菜单和识屏体验不变，暂时没有新增用户操作，也还不能在桌宠内安装卸载。新增的是可测试的安装状态账本：并发不相互覆盖，中断不误激活，旧备份不偷偷复活已卸载功能。只有本步全量门通过，才继续设计与实施 4B-2；不把状态服务完成写成整个本地管理闭环完成。
