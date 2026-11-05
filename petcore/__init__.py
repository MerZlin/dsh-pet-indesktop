"""petcore: dsh-pet 的 core 引擎、mod 宿主与桌面壳。"""

import time

__version__ = "0.5.0"

#: 进程导入起点（``run()`` 的分段日志用它补出「解释器引导 + 导入」这段）。
BOOT_T0 = time.perf_counter()
