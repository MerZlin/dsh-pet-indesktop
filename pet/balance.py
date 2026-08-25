# -*- coding: utf-8 -*-
"""DeepSeek 开放平台余额查询（GET /user/balance）。

余额气泡/小部件显示思路参考 MeteorNOX/DeepSeek-Balance-Whale-Widget
（见 README「参考项目」致谢），本实现为桌宠内置的轻量版本：
菜单「DeepSeek 余额」→ 后台查询 → 桌宠气泡显示；可在桌宠设置中
开启自动刷新（分钟级）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BALANCE_PATH = '/user/balance'


def _ssl_context(verify: bool):
    """延迟导入：无 Chat 变体排除 pet.chat 模块，顶层 import 会直接 ImportError。"""
    from .chat.providers import _make_ssl_context
    return _make_ssl_context(verify)


class BalanceError(RuntimeError):
    pass


def fetch_balance(base_url: str, api_key: str, timeout: float = 10.0,
                  verify_ssl: bool = True) -> dict:
    """查询余额。

    返回 {'is_available': bool, 'total': str, 'granted': str, 'topped_up': str}；
    未配置 Key / 端点不支持 / 网络失败抛 BalanceError。
    """
    endpoint = str(base_url or '').strip().rstrip('/') + BALANCE_PATH
    if not api_key:
        raise BalanceError('未配置 API Key')
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context(verify_ssl)) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise BalanceError(f'HTTP {exc.code}（该端点可能不支持余额查询）') from exc
    except urllib.error.URLError as exc:
        raise BalanceError(f'网络连接失败：{exc.reason}') from exc
    except (OSError, ValueError) as exc:
        raise BalanceError(str(exc)) from exc
    infos = data.get('balance_infos') if isinstance(data, dict) else None
    if not infos:
        raise BalanceError('响应中没有余额信息')
    info = infos[0] if isinstance(infos, list) else infos
    return {
        'is_available': bool(data.get('is_available', True)),
        'total': str(info.get('total_balance', '')),
        'granted': str(info.get('granted_balance', '')),
        'topped_up': str(info.get('topped_up_balance', '')),
    }


def format_balance(info: dict) -> str:
    """'余额 ¥12.34（充值 10.00 / 赠送 2.34）'；单一余额时简化。"""
    total = str(info.get('total', '') or '')
    granted = str(info.get('granted', '') or '')
    topped = str(info.get('topped_up', '') or '')
    if not total:
        return '余额信息为空'
    if granted and topped:
        return f'余额 ¥{total}（充值 ¥{topped} / 赠送 ¥{granted}）'
    return f'余额 ¥{total}'
