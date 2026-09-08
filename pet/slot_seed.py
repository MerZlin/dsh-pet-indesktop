# -*- coding: utf-8 -*-
"""子槽配置播种的单一换算来源（纯 Python，无 Qt）。

多进程「生小肥鱼」与单进程多开两条生产路径都会先调
``slot_manager.seed_slot_config_from_main()`` 把主 config.json 写为
slot-N 配置文件，随后才构造 ``Config(instance_id=...)``。该函数必须与
``Config._seed_slot_config_from_main()`` 保持同一套副槽化语义，否则
``Config`` 内部那份带 spawn_scale 换算的 seed 会因为文件已存在而永不执行
（表现为「关闭继承大小后子肥鱼仍是主鱼/最大号」）。

本模块把唯一换算集中到这里，两个调用方都委托它，避免两份逻辑再次分叉。
"""
from __future__ import annotations

import copy
from typing import Any

from . import catalog


def _as_bool(value: Any, default: bool) -> bool:
    """与 config._bool_or_default 同语义的本地实现（避免 config↔slot_manager 环）。

    只信任真正的 bool 与常见字符串布尔；其余按 default 返回，防止手改配置里
    ``"false"`` 字符串被 ``bool("false")`` 误判为 True。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return bool(default)


def build_slot_seed(main_data: dict[str, Any]) -> dict[str, Any] | None:
    """把主配置原始 dict 转换成新子槽的落种内容。

    - 副槽不继承主桌宠的位置/屏幕/朝向/自启（新鱼避免叠位、不重复拉起服务）；
    - ``spawn_inherit_size=True`` 保留主 scale；``False`` 用 ``spawn_scale``；
    - ``spawn_inherit_dynamic_island`` 决定新鱼灵动岛是否启用；
    - 写盘副本沿用主配置脱敏策略，不把明文 API Key 复制进副槽。
    """
    if not isinstance(main_data, dict):
        return None
    seed = copy.deepcopy(main_data)
    seed["version"] = 4
    # 副槽不继承主桌宠的位置/屏幕/朝向，避免新鱼叠在旧鱼身上；自启仍仅主槽。
    # 用 pop 而非写 None：slot_manager 路径有「每窗状态键不得继承」的落种测试，
    # 且 Config reload 对缺失键会回落到默认值，二者行为等价。
    for key in ("rx", "ry", "screen_name", "facing"):
        seed.pop(key, None)
    seed["autostart_wanted"] = False
    seed["harness_autostart"] = False

    # 生小肥鱼大小策略：开启继承 → 保留主配置 scale；
    # 关闭继承 → 用主配置里给“小肥鱼”单独选择的 spawn_scale。
    inherit_size = _as_bool(seed.get("spawn_inherit_size"), True)
    seed["spawn_inherit_size"] = inherit_size
    if not inherit_size:
        try:
            seed["scale"] = float(seed.get("spawn_scale", catalog.DEFAULT_SCALE))
        except (TypeError, ValueError):
            seed["scale"] = catalog.DEFAULT_SCALE

    # 生小肥鱼灵动岛策略：默认不继承 → 小肥鱼不开启自己的灵动岛；
    # 开启继承 → 保留主配置的 dynamic_island（含是否启用）。
    inherit_island = _as_bool(seed.get("spawn_inherit_dynamic_island"), False)
    seed["spawn_inherit_dynamic_island"] = inherit_island
    island = seed.get("dynamic_island")
    if isinstance(island, dict):
        island["enabled"] = bool(inherit_island)
    else:
        seed["dynamic_island"] = {"enabled": bool(inherit_island)}

    chat = seed.get("chat")
    if isinstance(chat, dict):
        providers = chat.get("providers")
        if isinstance(providers, dict):
            for provider in providers.values():
                if isinstance(provider, dict):
                    provider.pop("api_key", None)
                    provider.pop("vision_api_key", None)
    return seed
