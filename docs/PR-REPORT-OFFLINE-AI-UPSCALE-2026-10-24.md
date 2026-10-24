# 可选离线 AI 动画增强工具实测报告

> 基线：`34d810b`（main / v4.4.0）；范围：离线 AI 增强贡献分支。
> 日期：2026-10-24。范围仅为独立离线工具、测试、说明与一张署名对比图。
> 本报告区分「本地旧版原型」与「本 PR 的独立工具」，不把旧版运行结果当作主线验收。

## 一、核心特性

提供显式执行的 2 倍 AI 帧序列导出入口，保持片段 FPS、帧数和 alpha。
结果写到独立审阅目录；完成片段可通过源/模型身份和输出摘要复用。
不覆盖原素材，不触碰用户配置，不向运行时注入 ML 依赖。

**不变量**：不修改 `pet/`、默认 `requirements.txt`、`ENCODER_DESC`、
`packs-index.json`、素材发布门或播放器。AI 结果明确使用独立 encoder 身份，
不能冒充从母版位精确解出的帧。发布边界见 [使用说明](AI-FRAME-UPSCALE.md)
和 [现有素材包指南](PACK-PUBLISHING.md)。

## 二、修改文件说明

提交前以 `git diff --numstat` 核对；下表说明完整意图。

| 文件 | 增删 | 改动与原因 |
|---|---|---|
| `tools/ai_upscale_frameseq.py` | +217/-0 | 新增显式离线导出；校验连续帧、FPS、输入/输出隔离和 provider；完整片段原子发布，摘要校验恢复；无网络/运行时入口。 |
| `tools/export_ai_upscale_model.py` | +81/-0 | 新增固定官方权重摘要校验和安全加载；导出 uint8 NCHW 2 倍 ONNX；cubic 下采样使用等价 GPU 算子。 |
| `tools/requirements-ai-upscale.txt` | +7/-0 | 独立工具环境说明，不加入默认运行依赖。 |
| `tools/licenses/Real-ESRGAN-BSD-3-Clause.txt` | +29/-0 | 保留网络代码的 BSD-3-Clause 全文与版权。 |
| `tests/test_ai_upscale_frameseq.py` | +241/-0 | 新增源文件不变、透明/不透明帧、序号/FPS、失败不发布、身份恢复与输出篡改、模型安全边界等测试。 |
| `docs/AI-FRAME-UPSCALE.md` | +83/-0 | 环境、模型、命令、恢复和非位精确数据的明确边界。 |
| `docs/images/ai-upscale-2x-preview.png` | 二进制，634,618 字节 | 一张同显示尺寸的效果对比；源为旧版 Q70/v5，不声称代表当前 v6 的全部差异。 |
| `docs/PR-REPORT-OFFLINE-AI-UPSCALE-2026-10-24.md` | +136/-0 | 本报告，分别记录新工具与历史原型的证据。 |
| `docs/INDEX.md` | +3/-1 | 登记并互链新文档。 |
| `docs/PACK-PUBLISHING.md` | +4/-0 | 说明不能直接把 AI 输出用于现有发布链。 |
| `THIRD_PARTY_NOTICES.md` | +16/-0 | 补充网络代码与模型来源、独立依赖及角色素材署名。 |
| `.gitignore` | +3/-0 | 忽略 `dist-ai-upscale/` 生成数据，防止模型和整套动画入库。 |

未改动应用生命周期、播放节拍、GUI、配置、打包或角色包格式；不上传完整动画、
模型二进制、本机安装目录、配置或聊天记录。

## 三、实现要点

网络来自官方 Real-ESRGAN animevideov3 / SRVGGNetCompact。
原 x2 NCNN 路径是 x4 网络后接半尺寸 cubic，下采样系数 a=-0.75。
半尺寸 1D 核为 `[-3, 19, 19, -3] / 32`；分组卷积与边缘复制实现同一处理，
避免 unsupported cubic Resize 落入 CPU。输入/输出都为 uint8，图内做 FP16 归一化、
推理和舍入，减少主机与显卡间的宽张量传输。

RGB 用模型处理；alpha 不进神经网络，独立 bicubic 放大。
每段只在完全写入后发布；失败临时目录保留诊断，不自动删除未知数据。
恢复核对源帧内容摘要、模型摘要、FPS/画布以及已有输出摘要。

## 四、性能分析

环境：Windows 11，RTX 4050 Laptop GPU 6 GB；推理 Python 3.12、
ONNX Runtime DirectML 1.24.4；模型导出 Python 3.14、CPU PyTorch 2.13.0、
ONNX 1.23.2。只实测这台 Windows 机器，不外推三平台结果。

### 本 PR 工具（真实源帧）

```powershell
python tools/ai_upscale_frameseq.py --source INPUT_IDLE --output OUTPUT_IDLE --model MODEL.onnx --batch 8 --provider dml --device 0
```

原始输出：

```json
{"clip": ".", "frames": 241, "skipped": false, "elapsed_seconds": 73.512, "bytes": 72546738}
```

- 输入 640×360，输出 1280×720；帧率 24、帧数 241。
- 上述片段处理约 3.28 帧/秒，包含逐帧无损编码；计时不含命令启动、源摘要预检和
  推理会话初始化，不能把纯推理速度当作导出速度。
- 同环境模型导出器重新生成的文件 SHA-256 与已验证模型完全相同：
  `d28527caa2ea9c588d4ed487c6637a271e4158c9101df2e564f415f46795d145`。
- 单帧可见 RGB 与 NCNN 样片的平均绝对差约 0.21 / 255；不是逐位相等。

### 历史原型（不是本 PR 的整套验收）

本地 v4.3.4 原型曾导出 106 段 / 25,423 帧，并由用户确认样片效果。
该原型用并行 writer 与较低编码 effort，生成阶段约 27 帧/秒，完整结果约 10.15 GiB。
318 个抽查帧的解码/alpha 检查中位数 21.23 ms、p95 23.27 ms；旧版播放器日志映射 106 段。
这些数据说明可行性，但**不代表本 PR CLI 的端到端性能或 v4.4.0 运行时支持**。

稳态应用开销：没有新增应用调用路径，未对主线运行时做内存差值测量，不认领运行时优化。
新增成本只在显式运行工具时出现：源/输出哈希和图片读写、DirectML 会话及其 worker、GPU
显存和无损编码 CPU。无网络请求、无常驻服务、无配置轮询；输出体积与精度/编码有关。
本 PR CLI 峰值 RSS 未采集；旧原型约 394 MiB 为单点采样，不是峰值或本工具测量。

## 五、实机运行记录

- 固定官方 checkpoint 摘要通过；从新导出器重新导出 ONNX，模型文件摘要与验证模型一致。
- 真实 idle 241 帧通过 DirectML 导出，保留 alpha 和原始片段时长；上面的 JSON 来自工具原始输出。
- 首次展示的同尺寸对比样片由使用者确认可接受；对比图明确标为旧版 Q70/v5 基线。
- 不可信 checkpoint 在导入 PyTorch 前被拒绝；未知输出目录与损坏恢复摘要均被拒绝。
- 失败后不发布部分片段；不读取用户配置，也不自动改动正在运行的桌宠。
- 当前主线未接入 AI 输出，故不能自动确认其正式包安装/播放；缺少新的 AI pipeline 合约和对应发布门，
  此能力不是被省略的已验收项。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 红探针 | 新测试先于实现运行 | 缺少工具入口时 ImportError；实现后转绿 |
| 聚焦 | `python -m pytest -q tests/test_ai_upscale_frameseq.py` | 32 项；含大帧数元信息的分配前拒绝 |
| 新代码静态检查 | `python -m ruff check tools/ai_upscale_frameseq.py tools/export_ai_upscale_model.py tests/test_ai_upscale_frameseq.py` | All checks passed |
| 真实模型导出 | `python tools/export_ai_upscale_model.py --weights CHECKPOINT --output MODEL.onnx` | 摘要与验证模型相同 |
| 真实 DirectML | 上面的 idle 命令 | 241 帧完成 |
| 时序满载复跑 | 不适用 | 本 PR 无 Qt、定时器或新增自管线程路径 |
| 聚焦、架构和交付文档 | `python -m pytest -q tests/test_ai_upscale_frameseq.py tests/test_pr_report_discipline.py tests/test_architecture.py` | 279 passed |
| 仓库静态检查 | `python -m ruff check pet tests tools/ai_upscale_frameseq.py tools/export_ai_upscale_model.py` | All checks passed |
| Windows 主套件 | `python scripts/ci_shards.py` + 第一片复跑 | 最终四片分别 2224 / 2224 / 2337 / 2241 passed，共 9026 passed、19 skipped |
| WebM 隔离族 | `python -m pytest -q tests/test_webm_reader_lifecycle.py tests/test_webm_clip_lifecycle.py tests/test_webm_first_frame_lock.py` | 41 passed，24.02s |
| 低优预热隔离族 | `python -m pytest -q tests/test_low_priority_warm_interaction_yield.py` | 27 passed，32.06s；不需重跑 |

最终 Python 覆盖合计 **9094 passed、19 skipped**。主套件首轮第一片有三项失败：
两项脚本语法检查误用了指向不可用 WSL 的系统 bash；一项为本报告分支前缀触发文案黑名单。
仅在测试进程 PATH 中优先使用 Git Bash，并修正文档后，完整第一片 104 个文件复跑为
2224 passed、7 skipped。没有修改系统 PATH、构建脚本或放宽文案测试。
其他三片首轮通过；不能将首轮整个执行器的非零退出码说成零。
复跑使用同一份 `scripts/ci_shards.py::_collect_files()` 收集序的第一四分之一文件。
全量环境为 Windows / Python 3.12 / PySide6 6.12.0 / offscreen / null keyring，
测试数据目录隔离；平台相关跳过照常保留。三端远程 CI 仍以 PR 的实际结果为准。

## 七、已知限制与后续

- 这是离线实验工具，不是原生高清母版、完整发布版或自动素材包升级。
- 尚未验证 macOS/Linux 推理、其他 GPU、CPU 完整导出或逐帧完全无闪烁。
- 无损编码和磁盘体积可能成为主要成本；不要用纯 GPU 推理数字承诺整套耗时。
- 当前 schema 不接受这里的 AI encoder；后续若正式分发，应单独设计处理身份与发布门，
  不能伪造现有 encoder/token 或修改位精确校验结论。

## 八、风险与回滚

不影响默认安装、配置或播放器。撤回本 PR 即移除入口和文档，没有配置迁移。
用户指定的导出目录由用户决定保留或删除；工具不自动清理它。
原角色和模型权利不因 AI 处理改变，署名及非商业限制继续适用。
