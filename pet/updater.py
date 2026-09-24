# -*- coding: utf-8 -*-
"""联网检查、下载并启动 dsh-pet 更新安装包。

更新协议刻意保持为静态 JSON + 完整 Inno Setup 安装包：manifest 可以通过
GitHub API / CDN 多源获取，安装包则按 manifest 中的 URL 顺序回退。客户端在
启动安装器前校验 HTTPS、大小和 SHA-256，避免把网络错误当成可执行文件运行。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

APP_VERSION = __version__
REPO = 'MerZlin/dsh-pet-indesktop'
RELEASE_API = f'https://api.github.com/repos/{REPO}/releases/latest'
REPO_URL = f'https://github.com/{REPO}'
QUARK_PAN_URL = 'https://pan.quark.cn/s/68fc681ae486'
_UPDATE_JSON_URLS = (
    f'https://cdn.jsdelivr.net/gh/{REPO}@main/update.json',
    f'https://fastly.jsdelivr.net/gh/{REPO}@main/update.json',
    f'https://gcore.jsdelivr.net/gh/{REPO}@main/update.json',
)
_USER_AGENT = 'dsh-pet-standalone-updater'
_TRUSTED_ASSET_HOSTS = {
    'github.com',
    'objects.githubusercontent.com',
    'github-releases.githubusercontent.com',
    'cdn.jsdelivr.net',
    'fastly.jsdelivr.net',
    'gcore.jsdelivr.net',
}
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024


def _fetch_json(url: str, timeout: float):
    """GET JSON；任何失败返回 None。"""
    try:
        req = urllib.request.Request(
            url,
            headers={'Accept': 'application/json', 'User-Agent': _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_MANIFEST_BYTES + 1)
            if len(raw) > _MAX_MANIFEST_BYTES:
                return None
            return json.loads(raw.decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _trusted_https_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url))
    except ValueError:
        return False
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip('.')
    if host in _TRUSTED_ASSET_HOSTS:
        if host.endswith('github.com') and host == 'github.com':
            return f'/{REPO}/' in parsed.path or parsed.path.startswith(f'/{REPO}/releases/')
        return True
    return False


def _asset_details(name: str, value) -> dict | None:
    """把旧版 name -> url 和新版结构化 asset 统一成内部结构。"""
    if isinstance(value, str):
        urls = [value]
        details = {}
    elif isinstance(value, dict):
        details = value
        urls = details.get('urls') or details.get('mirrors') or details.get('url')
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list):
            urls = []
    else:
        return None
    # 解析阶段保留旧版/测试/企业镜像地址；真正下载前再做 HTTPS +
    # trusted-origin 校验，这样检查更新仍能展示候选版本，同时不会执行不可信文件。
    urls = [str(url).strip() for url in urls if str(url).strip()]
    if not urls:
        return None
    result = {
        'name': str(details.get('fileName') or details.get('name') or name),
        'urls': urls,
        'url': urls[0],
        'size': int(details['size']) if str(details.get('size', '')).isdigit() else None,
        'sha256': str(details.get('sha256') or details.get('digest') or '').removeprefix('sha256:').strip().lower() or None,
        'platform': str(details.get('platform') or '').lower() or None,
        'kind': str(details.get('kind') or '').lower() or None,
        'variant': str(details.get('variant') or '').lower() or None,
    }
    return result


def _normalise_assets(raw_assets) -> tuple[dict[str, str], dict[str, dict]]:
    assets: dict[str, str] = {}
    details: dict[str, dict] = {}
    if not isinstance(raw_assets, dict):
        return assets, details
    for name, value in raw_assets.items():
        item = _asset_details(str(name), value)
        if item is None:
            continue
        key = str(name)
        assets[key] = item['url']
        details[key] = item
    return assets, details


def _release_from_payload(data: dict) -> dict | None:
    if not isinstance(data, dict) or not data.get('version'):
        return None
    assets, asset_details = _normalise_assets(data.get('assets') or {})
    return {
        'version': str(data['version']).lstrip('vV'),
        'html_url': str(data.get('html_url') or REPO_URL),
        'notes': str(data.get('notes') or ''),
        'assets': assets,
        'asset_details': asset_details,
    }


def latest_release(timeout: float = 5.0) -> dict | None:
    """多源查询最新版本信息（GitHub API 优先，静态 CDN 兜底）。"""
    release = _fetch_json(RELEASE_API, timeout)
    if isinstance(release, dict) and release.get('tag_name'):
        assets, asset_details = _normalise_assets({
            str(a['name']): {
                'url': str(a['browser_download_url']),
                'size': a.get('size'),
                'digest': a.get('digest'),
                'name': str(a['name']),
            }
            for a in release.get('assets', [])
            if a.get('name') and a.get('browser_download_url')
        })
        return {
            'version': str(release['tag_name']).lstrip('vV'),
            'html_url': str(release.get('html_url') or REPO_URL),
            'notes': str(release.get('body') or ''),
            'assets': assets,
            'asset_details': asset_details,
        }
    for url in _UPDATE_JSON_URLS:
        data = _fetch_json(url, timeout)
        result = _release_from_payload(data) if isinstance(data, dict) else None
        if result is not None:
            return result
    return None


def version_parts(tag: str) -> list[int]:
    out: list[int] = []
    for seg in str(tag).lstrip('vV').split('.'):
        digits = ''
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return out


def is_newer(latest_tag: str, current: str = APP_VERSION) -> bool:
    return version_parts(latest_tag) > version_parts(current)


def select_windows_installer(release: dict, *, include_chat: bool = True) -> dict | None:
    """按当前 Windows 构建变体选择 Inno Setup 安装包。"""
    if os.name != 'nt' or not isinstance(release, dict):
        return None
    wanted = 'dsh-pet-standalone-webm-chat-setup.exe' if include_chat else 'dsh-pet-standalone-webm-setup.exe'
    details = (release.get('asset_details') or {}).get(wanted)
    if details is not None:
        return details
    url = (release.get('assets') or {}).get(wanted)
    if url:
        return _asset_details(wanted, url)
    return None


def _safe_filename(name: str) -> str:
    value = Path(str(name)).name
    value = re.sub(r'[^A-Za-z0-9._() -]+', '_', value).strip(' .')
    return value or 'dsh-pet-update.exe'


def download_asset(
    asset: dict,
    target_dir: str | os.PathLike,
    *,
    timeout: float = 30.0,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """下载并验证安装包；某个镜像失败会尝试下一个镜像。"""
    urls = list(asset.get('urls') or ([asset.get('url')] if asset.get('url') else []))
    expected_size = asset.get('size')
    expected_hash = str(asset.get('sha256') or '').lower() or None
    if not any(_trusted_https_url(url) for url in urls):
        raise RuntimeError('untrusted URL: ' + (urls[0] if urls else ''))
    if expected_size is None or expected_hash is None:
        raise ValueError('更新资产缺少 size 或 SHA-256，已停止自动安装')
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / _safe_filename(asset.get('name') or 'dsh-pet-update.exe')
    errors = []
    for url in urls:
        if not _trusted_https_url(url):
            errors.append(f'untrusted URL: {url}')
            continue
        partial = final_path.with_suffix(final_path.suffix + '.part')
        digest = hashlib.sha256()
        total = int(expected_size) if expected_size is not None else None
        received = 0
        try:
            req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as response, partial.open('wb') as output:
                header_total = response.headers.get('Content-Length')
                if total is None and header_total and header_total.isdigit():
                    total = int(header_total)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > _MAX_DOWNLOAD_BYTES or (total is not None and received > total):
                        raise ValueError('download exceeds declared size')
                    digest.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(received, total)
            if total is not None and received != total:
                raise ValueError(f'size mismatch: expected {total}, got {received}')
            actual_hash = digest.hexdigest()
            if expected_hash and actual_hash != expected_hash:
                raise ValueError(f'SHA-256 mismatch: expected {expected_hash}, got {actual_hash}')
            partial.replace(final_path)
            return final_path
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f'{url}: {exc}')
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
    raise RuntimeError('；'.join(errors) or '没有可用的下载地址')


def can_auto_install() -> bool:
    """仅冻结的 Windows 安装版允许自动拉起安装器。"""
    return os.name == 'nt' and bool(getattr(__import__('sys'), 'frozen', False))


def start_installer(installer_path: str | os.PathLike) -> None:
    """启动 Inno Setup 安装器；调用方随后退出当前应用。"""
    path = str(Path(installer_path).resolve())
    if os.name != 'nt':
        raise RuntimeError('当前平台不支持自动安装，请打开下载页手动更新。')
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    subprocess.Popen(
        [path, '/SILENT', '/CLOSEAPPLICATIONS', '/NORESTART'],
        close_fds=True,
        creationflags=flags,
        cwd=str(Path(path).parent),
    )


def update_cache_dir(config_dir: str | os.PathLike | None = None) -> Path:
    if config_dir:
        return Path(config_dir) / 'updates'
    return Path(tempfile.gettempdir()) / 'dsh-pet-updates'
