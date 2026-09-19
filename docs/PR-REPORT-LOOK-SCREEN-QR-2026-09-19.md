# PR Report: 看看屏幕识别屏幕二维码 —— zxing-cpp 本地离线解码 + 内容直接输出 + 设置开关（upstream 零改动纯新增）

右键「看看屏幕」时，若屏幕上出现二维码，桌宠会在本地离线解码并把内容直接输出到
气泡与对话记录（`【二维码】内容` 前缀 + 模型点评）。设置页新增「识别屏幕二维码」
开关（默认开，可关闭）。**本 PR 对 upstream 现有代码零修改**：全部改动均为纯新增
（逐文件 `+N -0`，见下表），upstream 后续演进不会与本地扩展产生行级冲突。

---

## 一、动机与方案

- 视觉模型读不出二维码（像素级解码不是它的能力），干看截图只会「我看到一个二维码」。
- 方案：截图后用 **zxing-cpp**（Apache-2.0，自含二进制扩展的官方 wheel，三大平台均无
  外部 DLL/SO 依赖）在本地离线解码；解码在**原始分辨率**上进行——upstream 发模型的
  截图会缩到最长边 768，实测 2560×1440 屏幕上一个 140px 的小码缩放后必死、原图完美解出。
- 隐私约定不变：解码纯内存、不联网、不落盘，除既有聊天 API 外不发送任何数据。

## 二、纯新增承诺（`git diff --numstat` 对 upstream/main 逐文件实测）

| 文件 | 增/删 | 内容 |
|---|---|---|
| `pet/window.py` | **0 / 0** | upstream 原样，一行未动 |
| `pet/vision.py` | +134 -0 | 二维码扩展块整体追加在文件末尾（见「三」挂接原理） |
| `pet/config.py` | +3 -0 | DEFAULTS、reload 白名单、布尔规范化各插入一行 |
| `pet/chat/ai_settings_page.py` | +14 -0 | modern 设置页开关（控件 / 独立设置区 / 保存各插一段） |
| `pet/chat/settings_dialog.py` | +5 -0 | legacy 设置对话框同样纯插入 |
| `tests/test_config_schema.py` | +1 -0 | 快照登记一行（快照测试为「现状文档化」设计，加键必须登记） |
| `requirements.txt` | +9 -0 | `zxing-cpp>=2.2` 运行时依赖 + 选型/许可证/打包说明 |
| 三个构建脚本（win/linux/macos） | +1 -0 | `--collect-all zxingcpp`（二进制扩展需显式收集） |
| `THIRD_PARTY_NOTICES.md` | +19 -0 | zxing-cpp（Apache-2.0）条目 |
| `tests/test_look_screen_qr.py` | 新文件 | 全部 18 个新增用例收纳于独立文件 |

## 三、挂接原理（不改 upstream 一行如何生效）

upstream 的 `_look_worker`（`pet/window.py`）以 `vision_mod.capture_screen_bytes(...)`
/ `vision_mod.ask_about_screen(...)` 的**调用时属性查找**调用视觉模块。本 PR 在
`pet/vision.py` 末尾追加扩展块，把这两个模块名**重绑**到二维码感知的包装：

- `capture_screen_bytes` 包装：原始分辨率截图 → （开关开时）本地解码并把结果存入
  模块级状态 → 用与 upstream 逐行同款的缩放/编码参数出模型图；
- `ask_about_screen` 包装：把解码内容作为**确定性前缀**直接拼进回复（`【二维码】内容`
  + 模型点评）；模型请求失败时，已解出的二维码内容仍然输出，不吞进错误气泡；
  无二维码 / 开关关闭 / zxing-cpp 缺失 / 解码异常时，行为与 upstream 完全一致。

解码只认 2D 码（QR / DataMatrix / Aztec / PDF417），1D 商品条码刻意排除（屏幕上多为
噪音）；内容去重、单张最多输出 3 条。

## 四、设置项

- **modern 设置页**：「看看屏幕二维码」独立设置区（不并入「视觉能力」行组，复用聊天
  模型的显隐联动不波及它）；**legacy 对话框**：表单同一开关。
- 键 `look_screen_qr_enabled`（默认开）：进 reload 白名单——独立设置进程写入后主进程
  直接读盘生效（视觉扩展每次看看屏幕时新建 `Config()` 读盘，不依赖主进程内存态）；
  手改 config.json 写字符串布尔由 `_bool_or_default` 同规防误开。

## 五、行为边界（如实说明）

- 视觉模型不知道二维码内容（提示词注入需要改 upstream 函数体，本 PR 放弃）；
  用户侧 100% 能看到解码原文，模型点评紧随其后。
- 多开槽位（`--instance` 子肥鱼）读的是主实例配置的开关——主实例完全正确。
- zxing-cpp 缺失（如精简构建）时功能整体降级为「不识别二维码」，主流程不受影响。

## 六、测试与验证

- 新增 `tests/test_look_screen_qr.py` 18 用例：真实二维码编解码、去重上限、无码/缺库/
  None 降级、1D 排除、模块重绑守卫、包装三态（有码/无码/模型失败）、开关跳过解码、
  config 默认值与落盘往返、字符串布尔归一、两套设置页存取守卫。
- Python 3.13（本地）与 3.11（CI 同版本）双环境通过；ruff 全绿（重绑行按仓库既有
  `# noqa` 模式注记）。
- 全量套件：2565 通过 / 7 失败——7 处均为 `test_drag_move_coalescing` /
  `test_pet_interaction_locks` 存量失败（干净树上同样失败，与本 PR 无关，未触碰）。

## 七、依赖

`zxing-cpp>=2.2`（Apache-2.0，与 MIT 兼容；PyPI 官方二进制 wheel，自含 C++ 扩展，
无外部运行时依赖）。requirements.txt 声明 + 三个构建脚本 collect + THIRD_PARTY_NOTICES
登记，三件套齐全。
