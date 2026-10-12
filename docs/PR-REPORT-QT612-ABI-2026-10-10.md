# PySide6 6.12.0 / Python 3.11 的单例返回值所有权修复

基线：`origin/main@ee699c7`；本报告覆盖 fork 集成分支的依赖构建修复，日期2026-10-10。
保持 CI 的 Python3.11 和 `PySide6>=6.5`，不通过版本上限规避故障。
相关：[打包流程](ONEDIR_PACKAGING.md)、[构建故障记录](BUILD-CI-FAILURE-NOTES-2026-08.md)、[第三方声明](../THIRD_PARTY_NOTICES.md)。

## 核心特性

Linux CI 的测试进程在约15%处以134退出。独立重现得到 `Fatal Python error: none_dealloc`，随后最小 Qt 程序证明6.12.0官方 Linux wheel 的普通 void 方法会消耗 None 引用。

同一个 Python3.11.16 进程，每组调用100次 QTimer.stop，6.12.0三组引用计数差均为−100；6.11.2均为0。setInterval/start也复现。反汇编中 `Sbk_ReturnFromPython_None()` 只返回 `_Py_NoneStruct` 地址，没有增加引用计数。

首轮补丁修复了该None返回函数，但全量退出阶段又发现bool_dealloc，最终修复见下方第二轮记录。使用Qt官方v6.12.0的固定提交 `f50facb83fc3dc5f3240c854668c7a6dd93871b2` 重建libshiboken，保留原扩展、包版本和动态库接口。应用代码不修改Python引用计数，不替换Qt方法。

安装依赖后执行：

```bash
python scripts/repair_pyside6.py
```

脚本仅处理实际探针失败的Linux Shiboken6.12.0。其他版本/平台不改动；重复执行探针已正确时直接退出。编译需要cmake、ninja-build、g++、qt6-base-dev以及当前Python的开发头文件。

## 修改文件说明

| 文件 | 增删 | 修改及原因 |
|---|---|---|
| `packaging/patches/qt-for-python-6.12-singleton-refs.patch`（新增） | +50/−0 | 修正stable-ABI头文件中的None/True/False返回宏；只构建libshiboken与libpyside运行库。 |
| `scripts/repair_pyside6.py`（新增） | +178/−0 | 子进程探针、固定源码获取、补丁检查、重建、原子替换与复验；失败恢复原库，并保留对应源码和说明。 |
| `tests/test_qt_timer_refcount.py`（新增） | +40/−0 | 独立进程检查普通方法与信号的None/True/False所有权，原生崩溃只判该测试失败。 |
| `.github/workflows/pr-test.yml` | +7/−1 | Linux增加构建依赖，在全量测试前执行同一修复入口。 |
| `.github/workflows/build-linux.yml` | +6/−1 | Linux打包任务同样先验证/修复绑定库。 |
| `scripts/build_linux.sh` | +9/−0 | 本地构建也执行同一入口；附带修改库的源码、许可证和重建说明。 |
| `README.md` | +2/−0 | 说明源码运行时的修复命令与编译依赖。 |
| `THIRD_PARTY_NOTICES.md` | +8/−0 | 记录源码版本、补丁、LGPL许可及动态库替换路径。 |
| 本报告（新增）、`docs/INDEX.md` | 见最终numstat | 登记重现、修复和发布验证证据。 |

精确最终行数使用 `git diff --numstat origin/main...HEAD`，本报告只记录依赖修复，不把其他功能改动计入。

## 性能分析

环境：Ubuntu24.04 x86_64、Python3.11.16、PySide6/Shiboken6.12.0、CMake3.28.3、16逻辑CPU。

- 实测命令：`/usr/bin/time -f '%e %M' .scratch/ci-venv/bin/python scripts/repair_pyside6.py --work-dir .scratch/shiboken-repair`。已有源码缓存的完整构建/替换/复验1次：4.74秒，进程树峰值RSS180236KiB。首次网络下载未计入，不宣称冷启动也只需4.74秒。
- 同命令重复执行1次：0.08秒，峰值RSS33376KiB；探针正确，没有编译或修改库。
- 修复前探针stop/setInterval/start各100次：`[-100,-101,-100]`；修复后为`[0,-1,0]`。一次性−1不随循环次数线性增长；回归门允许少量初始化变化，但拒绝每次调用丢引用。
- 稳态运行：None返回路径增加一次运行时引用计数API调用；不增加应用线程、定时器、文件轮询、网络或磁盘访问，也不为None持有额外永久引用。
- 构建期：首次获取约21MB Git对象，并运行CMake/Ninja；仅实际受影响环境编译。修复后的库和约461KB源码归档保存在该Python环境，打包时原样带入。没有向用户配置目录写入状态。
- Python3.12的最小探针虽然没有复现，但未采用升级解释器作为修复；CI继续使用3.11。

## 实机运行记录

未修改用户运行中的`.venv`。独立`.scratch/ci-venv`安装与CI相同的requirements解析结果，包括Qt6.12.0。

原始环境运行 `QT_QPA_PLATFORM=offscreen .scratch/ci-venv/bin/python -m pytest -q tests/test_qt_timer_refcount.py`：1failed，差值`[-100,-100,-100]`。

执行修复脚本后，相同用例及文件监听聚焦族：42passed、5skipped。真实本机X11水平双屏执行`tests/test_linux_overlay.py 对应的结构化气泡测试 tests/test_overlay_window.py::test_paint_event_clears_dirty_region_before_redraw`：16passed。

源码归档单独解压后，以 `cmake -S sources/shiboken6 -B build -G Ninja -DBUILD_TESTS=OFF -DFORCE_LIMITED_API=ON -DPython_EXECUTABLE=/path/to/python` 配置，再执行 `cmake --build build --target libshiboken --parallel 4`，33个构建步骤完成。归档保留embedding_generator需要的sources目录及build_scripts，不能只分发一个脱离目录结构的libshiboken子目录。

## 测试与验证

16个负载进程分别占用16个逻辑CPU；文件监听、结构化气泡及监控生命周期族三遍，每遍35passed，耗时3.02/3.22/3.37秒。所有负载子进程已回收。

全量与远端最终运行结果在下方追加。Windows/macOS继续使用原生官方wheel，原始集成提交的两个平台CI已通过；不能以它们代替最终提交的构建验证。

## 风险与回滚

修复仅作用于6.12.0对应源码和原生库；未知版本不会套用该补丁。原生库按构建机工具链生成，旧Linux发行版的二进制兼容性尚未实机验证。

脚本在当前Python环境的库目录原子替换动态库，先保存原库，复验失败会恢复。重新安装同版本官方shiboken6也可撤销，但将重新引入已复现问题。生成的Linux包允许替换独立动态库，源码与许可位于`_internal/third_party/shiboken6/`。

## 第二轮：信号布尔返回值与最终修复

第一轮全量执行完4326个非跳过用例，但退出阶段出现bool_dealloc。独立探针证明Signal.emit和Signal.disconnect每次消耗一份True/False引用。只修None不完整，因此最终补丁在公共sbkpython.h中对目标ABI低于Python3.12的构建明确使用运行时Py_IncRef/PyBool_FromLong，重建libshiboken与libpyside；不修改Python内存或应用层Qt方法。

Qt6.12.0 qtbase SDK由aqtinstall下载，用于匹配的公开/私有头文件；编译工具运行时使用wheel自带的Qt/ICU。生成库的RUNPATH仅为ORIGIN相对路径，未写入本机SDK目录。官方扩展模块和包版本仍保留6.12.0。

最终原始wheel到两库修复的完整流程实测1次：12.45秒、进程树峰值RSS392720KiB（源码/SDK已缓存，下载另计）。六项各100次调用的引用差从`[-100,-101,-100,-100,-100,-100]`变为`[0,-1,0,0,0,0]`；相关测试59passed。扩大后的门禁还验证非目标版本不修改、已修复环境重复运行直接退出。

现有逐只音效测试首轮仍失败。现场记录表明，它连续写入两个布尔字段后文件长度不变，且短时间内mtime_ns也不变；生产代码明确使用(mtime,size)筛选配置变化，测试未建立可观测修改的前提。仅修改tests/test_overlay_per_slot_settings.py的写入辅助函数（+7/−0），使文件mtime确定前进，不改生产音效逻辑；同族18passed。这与原生绑定修复是两项独立问题。

最终满载三遍各35passed，3.58/3.57/3.53秒。最终完整测试与远端构建结果后续追加。

## 最终本地门禁

Python3.11.16 + PySide6/Shiboken6.12.0修复库：`QT_QPA_PLATFORM=offscreen .scratch/ci-venv/bin/python -m pytest -q` **4328passed、42skipped、20warnings，237.15秒，进程正常退出**。ruff、shell语法及diffcheck通过。真实X11相关16passed，最终满载三遍各35passed。

稳态额外测量：同环境`timeit.repeat(method, number=100000, repeat=5)`，QTimer.stop每次中位数0.038µs，Signal.emit为0.180µs；各50万次调用后的对应单例引用差为0。这是修复库的绝对调用成本，不是与错误库的性能提升比例。最终对应源码归档为6995624字节，包含两套运行库源文件、构建辅助代码、许可证、补丁及精确修复脚本。

## CI 构建环境与并发用例补充

第二轮Linux安装包构建、macOS/Windows构建均成功；独立UbuntuCI在配置QtGuiPrivate时发现缺少XKB开发库。补充libxkbcommon-dev，并把wheel的Qt/ICU库路径同时传给CMake配置与编译阶段，避免qmake/rcc缺少ICU73。此项不改变Python或PySide6版本。

完整测试在并行/满载环境中另外暴露两条既有测试的前提问题：唯一记录用例沿用产品1秒锁超时后主动丢弃策略，却断言不能丢任何一条；另一用例把不同读者的观测用同一个最大值比较，线程切换时会把较早取得的合法快照判成回退。只修测试：写入序列化用例使用60秒锁预算，保留独立的短超时测试；每个读者只比较自己的连续观测，并保留最终121条完整记录断言。生产记忆代码不改动。

最终加入上述并发族的满载验证每遍69passed，三遍耗时39.39/25.43/15.71秒。最终全量4328passed、42skipped、20warnings，236.84秒，正常退出。

## 提示清理与采样线程用例补充

第三轮UbuntuCI已通过；macOS在SMTC线程所有权用例中采样了两次同名后台线程，却被旧断言“只能一次”判失败。改为断言所有记录均来自指定探针线程，仍禁止调用线程查询OS，不约束20ms采样循环恰好执行一次。Windows构建在恢复提示用例的全局processEvents清理处原生崩溃；改为只派发当前窗口的DeferredDelete，并在四条工作流中单独运行整个恢复提示文件，全部五个用例保留，不增加失败重试。

两族聚焦13passed；加入监控时序族后满载三遍各48passed（5.28/5.22/5.38秒）。最新本地全量4328passed、42skipped、21warnings，266.42秒，正常退出。

第二轮d646cc3的Linux安装包已在本机bubblewrap隔离配置、缓存、用户数据和网络后启动，offscreen GUI15秒存活；打包hook退出0、stdout为{}、stderr为空，无_MEI目录。另在独立Python进程中先加载包内两份原生库，校验/proc/self/maps确实指向安装包，再执行六项引用探针，结果全0。包根目录和包内子目录的重复运行库SHA256一致。该记录是候选包验证，最终发布构建需另核对提交与产物。
