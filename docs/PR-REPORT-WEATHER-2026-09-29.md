# PR 报告：天气（查看天气 / 多城市）2026-09-29

> **基线**：`c7c9bac`（免打扰 + 今日汇总那批）
> **分支**：`main`（本地改造分支，未推送）　**日期**：2026-09-29
> **范围**：实现 10 个文件（3 个新增模块）+ 测试 6 个文件（3 新增 / 3 更新契约）+ docs 2
> **关联**：功能补齐方案（会话交付物，第 2 节 天气）、
> [`CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md`](CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md)

## 一、核心特性

桌宠会看天气了：右键菜单「查看天气」冒一句「汕头今天 26°，天气阴」；
「天气城市」子菜单里可以联网搜城市、点一下切换、手动填名字、删掉不要的。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 国内直连优先 | 中国天气网（`toy1` 搜城市 → `d1` 取实况）：不要 Key、中文直出、实测延迟低 |
| 2 | 兜底源 | open-meteo geocoding + forecast；WMO 天气码查中文对照表，不把英文吐给用户 |
| 3 | 地名容错 | 「鹿城区」自动退成「鹿城」再查；兜底时补「市」后缀（open-meteo 中文库多是全称） |
| 4 | 多城市 | 城市列表上限 12，**当前城市永远排第一**，菜单里打勾 |
| 5 | 不卡界面 | 取数走后台线程 + 队列 + 主线程 tick；6 秒超时，失败只说"没查到" |
| 6 | 缓存与去重 | 同城 10 分钟内不重复联网；查询进行中再点会提示"还在查呢" |
| 7 | 免打扰兼容 | 气泡走提醒队列（`alert_type="weather"`），免打扰期间被压住并攒着 |
| 8 | 设置页 | 「互动」域新增第三个页内标签「天气」：开关 + 默认城市 |

**红线 / 不变量**：

1. 天气**不依赖 API Key**，也不依赖 `pet.chat`（无 Chat 的打包变体必须同样能用）；
2. 取数绝不在 GUI 线程（`urllib` 阻塞）；
3. `pet/window.py` 一行未改；`modern_settings_dialog.py` 只增 3 行接线（预算按实测校准）。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/weather_source.py`（新增） | +287 / −0 | 纯逻辑层（不 import Qt）：地名变体、中国天气网「伪 JSON」与实况解析、WMO 码表、城市列表清洗、`fetch_weather` 编排；HTTP 出口（`_http_text` / `_http_json`）以参数注入，可离线单测 |
| `pet/weather_service.py`（新增） | +280 / −0 | 服务壳：后台线程抓取 → 队列 → 主线程 tick 冒泡；城市增删改与落盘；菜单用的小流程（搜索候选选择框、手动输入、删除） |
| `pet/settings_weather.py`（新增） | +66 / −0 | 设置页控件与 2 行：`weather_enabled`（开关）、`weather_city`（城市输入框）；写回时顺带把城市并进列表 |
| `pet/config.py` | +13 / −0 | 3 个新键 `weather_enabled` / `weather_city` / `weather_city_list` + 清洗（城市去空白限长、列表去重限 12 且当前城市排第一） |
| `pet/context_menus/registry.py` | +52 / −0 | 注册 `weather`（查看天气）与 `weather_cities`（子菜单：城市列表打勾切换 + 联网搜索 + 手动输入 + 删除） |
| `pet/context_menus/icons.py` | +6 / −0 | 新增 `weather` 图标（太阳 + 云），与余额/待办同级的语义图标 |
| `pet/settings_interaction.py` | +11 / −2 | 「互动」域新增第三个页内标签「天气」（`TAB_KEYS` / `TAB_LABELS` + 一行装配） |
| `pet/modern_settings_dialog.py` | +3 / −0 | 仅 import 与保存链 `settings_weather.apply_to_config(self)` |
| `pet/menu_templates/modern-default-v1.json` | +2 / −0 | 新版模板加 `weather` / `weather_cities` 两个 action 节点 |
| `pet/menu_templates/modern.json` | +1 / −1 | 旧版菜单 `tools` 分组补两个入口 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_weather_source.py`（新增） | +154 / −0 | 地名变体（含"鹿城区"）、伪 JSON 解析、`999` 占位与国外报错页、WMO 码表、城市列表清洗、`fetch_weather` 的"国内源优先 / 重试一次 / 兜底源 / 全失败返回 None"编排（HTTP 全注入假实现） |
| `tests/test_weather_service.py`（新增） | +181 / −0 | 未配城市/功能关闭不动作、抓取→队列→tick 冒泡（线程换成同步替身，无竞态）、缓存命中不重复联网、失败文案以「呜～」开头、搜索候选单选直接落库、空候选提示、城市增删改与落盘、气泡走提醒队列、子菜单结构契约 |
| `tests/test_settings_weather.py`（新增） | +69 / −0 | 控件初值来自 config、二次装配不重建控件、行 `objectName`、写回 3 键（含城市去空白与并入列表） |
| `tests/test_menu_layout.py` | +11 / −0 | 根节点 id、`registered` 集合、解析结果、QMenu 渲染序列四处补 `weather` / `weather_cities` |
| `tests/test_desktop_pet_features.py` | +3 / −0 | 根菜单文本序列补「查看天气」「天气城市」 |
| `tests/test_settings_interaction_tabs.py` | +4 / −2 | 互动域标签契约：新增 `("weather", "天气")` 与两行归属；"每行都能经标签到达"与搜索自动切标签的既有断言自动覆盖新标签 |
| `tests/test_config_schema.py` | +5 / −3 | reload 白名单快照补 3 键；文件头数字同步实测（默认值 135 / 白名单 130） |
| `tests/test_architecture.py` | +4 / −1 | `MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET` 2389 → 2392，理由写在常量上方（只多 import + 保存链两行） |

### 未改动（明确说明）

- `pet/window.py`：一行未改（菜单经注册表直接调服务；气泡走 `window_alerts`）。
- `pet/balance.py` 的 `_ssl_context`：**刻意不复用**——它经 `pet.chat.providers`，
  无 Chat 变体会整个排除 `pet.chat`，天气不该因此失效；天气自带 `certifi` 版本。

## 三、实现要点

1. **纯逻辑与 Qt 分层**：解析/清洗/编排在 `weather_source.py`（HTTP 出口可注入），
   线程/定时器/对话框在 `weather_service.py`。前者能在没有网络的环境里跑满断言。
2. **后台线程 + 队列 + tick**：`request()` 立刻返回，后台线程只写
   `host._weather_state["queue"]`，主线程 300ms tick 消费并冒泡——队列空了就把
   节拍停掉，不常驻空转。
3. **省份字段不写死下标**：参考实现取 `ref` 的第 10 段，但换批次会取到纬度；
   这里改成"从末尾往回找第一个含中文、且不是城市名本身"的字段，只影响括号里的后缀。
4. **免打扰天然兼容**：气泡统一走 `window_alerts.show_alert(alert_type="weather")`，
   免打扰期间会被压住并计入"攒了 N 条"，退出后一起汇报（无需天气侧写代码）。
5. **城市列表口径**：`clean_city_list()` 一处实现（去重 / 限 12 / 当前城市排第一），
   config 清洗、设置写回、菜单展示三处共用，避免规则漂移。

## 四、性能分析

**方法（可复现）**：`.venv\Scripts\python.exe work\bench_weather.py`（脚本在会话 work 目录）
环境：Windows 11 (10.0.26200)，Python 3.12.14；样本量 5 万次。

| 指标 | 实测 | 归属 |
|---|---|---|
| `parse_cn_sk()` | 1.98 µs/次 | 新增（每次查询 1 次） |
| `parse_cn_city_search()` | 2.66 µs/次 | 新增（每次查询 1~2 次） |
| `clean_city_list()` | 4.28 µs/次 | 新增（菜单构建 / 设置写回时） |
| `format_weather_line()` | 0.26 µs/次 | 新增（冒泡前 1 次） |

**结论**：

1. **稳态开销**：不点「查看天气」时为 0（没有常驻 tick——队列空即停表）。
2. **新增路径成本**：一次查询的纯逻辑合计 < 10 µs，全部成本在网络往返
   （国内源实测 0.1~0.4 秒量级，取自参考实现同源记录；本机沙箱无法联网复测，见第五节）。
3. **系统调用 / 网络 / 磁盘 / 线程**：仅**用户主动查询**时：1 个后台守护线程、
   1~4 次 HTTPS、缓存命中时 0 次网络；切换城市会写一次 `config.json`（`cfg.save()`）。
4. **内存**：每城一条缓存（温度 + 描述 + 时间戳），缓存不设上限但城市列表限 12，
   实际条目 ≤ 用户查过的城市数；无图片/无大对象。

## 五、实机运行记录

**环境**：本机 Windows 11 + Python 3.12.14（`.venv`：PySide6 6.11.2 等）。

1. **未联网的探针（失败即证据）**：本机沙箱禁网，实时接口**无法在本机复测**：

   ```text
   URLError [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试
   ```

   因此解析逻辑是用**注入的假响应**验证的（`tests/test_weather_source.py`），
   真实接口的字段布局按参考实现（deepseek-dafeiyu-pet）的注释与实测记录对齐，
   **未经本机联网复核**——这一条如实写在这里。
2. **联网能力由用户实机确认**：`查看天气` 首次点开若报「呜～…没查到」，
   请把城市换成不带「区/县」的写法再试一次（国内库用简称）。
3. **契约变更的实机表现**：`tests/test_settings_interaction_tabs.py` 与
   `tests/test_menu_layout.py` 在改动后**先红后绿**（分别对应"互动域多了一个标签"与
   "根菜单顺序变了"），修的是断言而不是产品行为，逐条列在第二节的测试表里。
4. **全量对照（本次改动 vs 基线 `2786c15`，同机同依赖同检出范围）**：

   ```text
   基线   ：69 failed, 2868 passed, 13 skipped
   本轮   ：67 failed, 2934 passed, 11 skipped
   逐条比对：无新增失败；净增通过用例 = 天气 29 条 + 其余为设置/菜单既有用例
   ```

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv\Scripts\python -m ruff check pet/ tests/` | ✅ All checks passed |
| 天气纯逻辑 | `pytest -q tests/test_weather_source.py` | ✅ 16 passed |
| 天气服务 | `pytest -q tests/test_weather_service.py` | ✅ 11 passed |
| 天气设置 | `pytest -q tests/test_settings_weather.py` | ✅ 3 passed |
| 契约（菜单 / 配置 / 互动标签 / 架构） | `pytest -q tests/test_menu_layout.py tests/test_config_schema.py tests/test_settings_interaction_tabs.py tests/test_architecture.py` | ✅ 98 + 6 passed |
| 全量 | `pytest -q`（`TMP/TEMP` 指向自建可写目录） | ✅ 无新增失败（见第五节第 4 条） |

## 七、已知限制与后续

1. **未做真实联网验收**（沙箱禁网，见第五节）；接口字段按参考实现对齐，若真机报"没查到"，
   优先怀疑城市名写法与接口改版。
2. **没做 IP 自动定位**：挂代理会定位到节点所在地，隐私与准确度都不划算；
   要做也是菜单里显式点一下。
3. **没做天气动画/图标联动**：本次只到"气泡报一句"，角色动画联动物料不在本次范围。
4. **设置页只给了两行**（开关 + 默认城市）：城市列表的增删在右键菜单里做，
   避免设置页再长一截。

## 八、风险与回滚

- **影响面**：新增菜单两项 + 互动域多一个标签；`window.py` 未改，`window_alerts`
  仅被复用（未改语义）。
- **网络依赖**：仅在用户点「查看天气 / 添加城市」时发起；超时 6 秒、失败只冒一句提示，
  不写入任何错误状态。
- **配置迁移**：3 个键均为纯新增键，`version` 不需升。
- **回滚**：`git revert` 本提交即可；已写入 `config.json` 的 3 个键按既有未知键保留策略留着。
