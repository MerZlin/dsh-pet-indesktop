from __future__ import annotations

import hashlib
import json
import logging
import os
import platform as platform_mod
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from .. import __version__
from ..net.errors import NetworkError
from ..net.http import build_request, is_allowed_https_url, urlopen
from ..performance import runtime_metrics
from .model import (
    UpdateAsset,
    UpdateManifest,
    UpdateResult,
    _normalize_architecture,
    _normalize_platform,
    _safe_asset_name,
    compare_versions,
    parse_manifest,
)

logger = logging.getLogger("petcore.update")

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_UPDATE_BYTES = 512 * 1024 * 1024
DEFAULT_MANIFEST_URL = "https://api.github.com/repos/MerZlin/dsh-pet-indesktop/releases/latest"
DEFAULT_FALLBACK_MANIFEST_URLS = (
    "https://cdn.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/update.json",
    "https://fastly.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/update.json",
    "https://gcore.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/update.json",
)
TRUSTED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
        "raw.githubusercontent.com",
        "cdn.jsdelivr.net",
        "fastly.jsdelivr.net",
        "gcore.jsdelivr.net",
    }
)


class UpdateService:
    def __init__(
        self,
        root: Path,
        *,
        current_version: str = __version__,
        manifest_url: str | None = None,
        fallback_manifest_urls: tuple[str, ...] | None = None,
        trusted_hosts: tuple[str, ...] | None = None,
        platform: str | None = None,
        architecture: str | None = None,
        frozen: bool | None = None,
        fetcher: Callable[..., object] | None = None,
        launcher: Callable[[Path], object] | None = None,
    ) -> None:
        self.root = Path(root)
        self.update_dir = self.root / "updates"
        self.current_version = str(current_version).lstrip("v")
        self.manifest_url = manifest_url or DEFAULT_MANIFEST_URL
        fallbacks = fallback_manifest_urls if fallback_manifest_urls is not None else DEFAULT_FALLBACK_MANIFEST_URLS
        self.fallback_manifest_urls = tuple(
            url for url in dict.fromkeys(str(item).strip() for item in fallbacks if str(item).strip())
            if url != self.manifest_url
        )
        self.trusted_hosts = frozenset(trusted_hosts or TRUSTED_HOSTS)
        self.platform = _normalize_platform(platform or sys.platform)
        self.architecture = _normalize_architecture(architecture or platform_mod.machine() or "x64")
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        self.fetcher = fetcher
        self.launcher = launcher
        self._cancel = threading.Event()
        self._manifest: UpdateManifest | None = None
        self._status = UpdateResult(True, "idle", version=self.current_version)

    def _allowed_url(self, url: str) -> bool:
        return is_allowed_https_url(url, self.trusted_hosts)

    def _request(self, url: str, *, headers: dict[str, str] | None = None):
        if not self._allowed_url(url):
            raise ValueError("update URL is not an allowed HTTPS host")
        request = build_request(
            url,
            method="GET",
            headers=tuple((str(key), str(value)) for key, value in (headers or {"Accept": "application/json"}).items()),
        )
        runtime_metrics.inc("network_requests")
        if self.fetcher is not None:
            response = self.fetcher(request)
            final_url = str(getattr(response, "geturl", lambda: url)() or url)
            if not self._allowed_url(final_url):
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise ValueError("update redirect is not an allowed HTTPS host")
            return response
        # 更新检查是第三方 CDN 的一次性取数：它的成败不代表整个进程该固定直连。
        response = urlopen(request, timeout=15, pollute=False)
        final_url = str(getattr(response, "geturl", lambda: url)() or url)
        if not self._allowed_url(final_url):
            close = getattr(response, "close", None)
            if callable(close):
                close()
            raise ValueError("update redirect is not an allowed HTTPS host")
        return response

    def check(self, *, force: bool = False) -> UpdateResult:
        if not force:
            cached = self._read_cached_manifest()
            if cached is not None:
                self._manifest = cached
                return self._result_for(cached)
        errors: list[str] = []
        urls = (self.manifest_url, *self.fallback_manifest_urls)
        etag = self._cached_etag() if force else ""
        for index, manifest_url in enumerate(urls):
            if self._cancel.is_set():
                return self._cancelled("已取消检查更新")
            try:
                headers = {"Accept": "application/json"}
                # An ETag is tied to a source.  Reusing it for the fallback sources
                # can turn a valid source into a misleading 304, so only send it to
                # the primary URL that created the cache.
                if etag and index == 0:
                    headers["If-None-Match"] = etag
                response = self._request(manifest_url, headers=headers)
                status = int(getattr(response, "status", 200) or 200)
                response_headers = getattr(response, "headers", {}) or {}
                try:
                    raw = response.read(MAX_MANIFEST_BYTES + 1)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                if status == 304:
                    manifest = self._read_cached_manifest()
                    if manifest is None:
                        raise ValueError("update manifest returned 304 without a valid cache")
                    self._manifest = manifest
                    return self._result_for(manifest)
                if status < 200 or status >= 300:
                    raise ValueError(f"unexpected update manifest HTTP status {status}")
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("update manifest exceeds size limit")
                manifest = parse_manifest(json.loads(raw.decode("utf-8")))
                self._manifest = manifest
                self._write_cached_manifest(raw, etag=str(response_headers.get("ETag") or ""))
                return self._result_for(manifest)
            except Exception as exc:  # noqa: BLE001 - try the next trusted source
                errors.append(f"{type(exc).__name__}@{manifest_url}")
        cached = self._read_cached_manifest()
        if cached is not None:
            self._manifest = cached
        detail = "; ".join(errors[:3])
        result = UpdateResult(
            False,
            "check_failed",
            f"更新检查失败：{detail}"[:240],
            version=self.current_version,
            manifest=cached,
        )
        self._status = result
        return result

    def download(self, asset: UpdateAsset, *, cancel=None, progress=None) -> Path:
        candidate_urls = asset.candidate_urls()
        if not _safe_asset_name(asset.name) or not candidate_urls or not all(self._allowed_url(url) for url in candidate_urls):
            raise ValueError("update asset URL is not an allowed HTTPS host")
        if asset.size <= 0 or asset.size > MAX_UPDATE_BYTES:
            raise ValueError("update asset size exceeds limit")
        self.update_dir.mkdir(parents=True, exist_ok=True)
        final = self.update_dir / asset.name
        part = final.with_suffix(final.suffix + ".part")
        meta = part.with_suffix(part.suffix + ".json")
        state = {"urls": list(candidate_urls), "size": asset.size, "sha256": asset.sha256, "name": asset.name}
        if meta.exists():
            try:
                old = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = None
            legacy_state = None
            if isinstance(old, dict) and old.get("url"):
                legacy_state = {
                    "urls": [str(old.get("url"))],
                    "size": old.get("size"),
                    "sha256": old.get("sha256"),
                    "name": old.get("name"),
                }
            if old != state and legacy_state != state:
                part.unlink(missing_ok=True)
        meta_temp = meta.with_suffix(".tmp")
        meta_temp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(meta_temp, meta)
        errors: list[str] = []
        preserve_resume = False
        for url in candidate_urls:
            if self._cancel.is_set():
                raise InterruptedError("update download cancelled")
            try:
                self._download_from_url(asset, url, part, cancel=cancel, progress=progress)
                path = self._finalize_download(asset, part, final, meta)
                self._status = UpdateResult(
                    True,
                    "downloaded",
                    version=self._manifest.version if self._manifest else "",
                    path=path,
                    manifest=self._manifest,
                )
                return path
            except InterruptedError:
                # Cancellation is a caller decision, not a mirror failure.  Keep
                # the partial state and return control immediately.
                raise
            except (ConnectionError, OSError, NetworkError) as exc:
                if self._cancel.is_set() or (cancel is not None and cancel()):
                    raise InterruptedError("update download cancelled") from exc
                # A transport interruption leaves the bytes already received
                # and the matching metadata usable for the next attempt.  Do
                # not throw away a safe resume point merely because every
                # trusted mirror is currently unavailable.
                preserve_resume = True
                errors.append(type(exc).__name__)
                continue
            except ValueError as exc:
                # Protocol, size and digest failures are not safe to preserve as a
                # resume point.  A later trusted mirror may still provide a good
                # asset, so clear the bad partial before trying it.
                part.unlink(missing_ok=True)
                errors.append(type(exc).__name__)
                continue
        if not preserve_resume:
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
        message = "更新下载失败"
        if errors:
            message = f"更新下载失败：{errors[-1]}"
        self._status = UpdateResult(
            False,
            "download_failed",
            message,
            version=self._manifest.version if self._manifest else "",
            manifest=self._manifest,
        )
        raise ConnectionError(message)

    def _download_from_url(self, asset: UpdateAsset, url: str, part: Path, *, cancel=None, progress=None) -> None:
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Accept": "application/octet-stream"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        response = self._request(url, headers=headers)
        try:
            status = int(getattr(response, "status", 200) or 200)
            if offset and status == 200:
                offset = 0
                part.unlink(missing_ok=True)
            elif offset and status == 416:
                if offset == asset.size:
                    return
                part.unlink(missing_ok=True)
                offset = 0
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                response = self._request(url, headers={"Accept": "application/octet-stream"})
                status = int(getattr(response, "status", 200) or 200)
            if status not in (200, 206):
                raise ValueError(f"unexpected update HTTP status {status}")
            if status == 206:
                content_range = self._content_range(response)
                if content_range is None or content_range[0] != offset or content_range[2] != asset.size:
                    raise ValueError("invalid update Content-Range")
            elif offset:
                # A 200 response to a ranged request restarts from byte zero.
                offset = 0
            mode = "ab" if offset and status == 206 else "wb"
            done = offset if mode == "ab" else 0
            with part.open(mode) as output:
                while True:
                    if self._cancel.is_set() or (cancel is not None and cancel()):
                        raise InterruptedError("update download cancelled")
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    done += len(block)
                    if done > asset.size or done > MAX_UPDATE_BYTES:
                        raise ValueError("update asset exceeds declared size")
                    output.write(block)
                    runtime_metrics.inc("disk_writes")
                    if progress is not None:
                        progress(done, asset.size)
            if done != asset.size:
                raise ConnectionError("update download is incomplete")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _content_range(response) -> tuple[int, int, int] | None:
        headers = getattr(response, "headers", {}) or {}
        value = str(headers.get("Content-Range") or headers.get("content-range") or "")
        if not value.lower().startswith("bytes "):
            return None
        try:
            range_text, total_text = value[6:].split("/", 1)
            start_text, end_text = range_text.split("-", 1)
            start, end, total = int(start_text), int(end_text), int(total_text)
        except (ValueError, TypeError):
            return None
        if start < 0 or end < start or total <= end:
            return None
        return start, end, total

    def _finalize_download(self, asset: UpdateAsset, part: Path, final: Path, meta: Path) -> Path:
        digest = hashlib.sha256()
        total = 0
        with part.open("rb") as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                digest.update(block)
        if total != asset.size or digest.hexdigest().lower() != asset.sha256.lower():
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            raise ValueError("update asset digest or size mismatch")
        os.replace(part, final)
        meta.unlink(missing_ok=True)
        self._write_verified_meta(final, asset)
        runtime_metrics.inc("disk_writes")
        return final

    def cancel(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        """新任务提交时清掉上一次的取消标记（不能在 worker 里清，会把排队撤销吞掉）。"""
        self._cancel.clear()

    def _verified_meta_path(self, path: Path) -> Path:
        return path.with_suffix(path.suffix + ".verify.json")

    def _write_verified_meta(self, path: Path, asset: UpdateAsset) -> None:
        payload = {
            "name": path.name,
            "size": int(asset.size),
            "sha256": str(asset.sha256).lower(),
        }
        meta = self._verified_meta_path(path)
        temp = meta.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(temp, meta)
        except OSError:
            logger.debug("update verified meta write failed", exc_info=True)

    def _read_verified_meta(self, path: Path) -> dict | None:
        try:
            payload = json.loads(self._verified_meta_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("name") != path.name:
            return None
        return payload

    def _package_is_verified(self, path: Path) -> bool:
        """安装前重算摘要：侧车记录让重启后仍可安装，重算挡住下载后被替换的包。"""
        payload = self._read_verified_meta(path)
        if payload is None:
            return False
        sha = str(payload.get("sha256") or "").lower()
        size = payload.get("size")
        if not sha or not isinstance(size, int):
            return False
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("rb") as source:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    digest.update(block)
        except OSError:
            return False
        return total == size and digest.hexdigest().lower() == sha

    def _expected_asset(self) -> UpdateAsset | None:
        """清单里登记的、本平台应安装的那个资产（重启后从缓存清单恢复）。"""
        manifest = self._manifest
        if manifest is None:
            try:
                manifest = self._read_cached_manifest()
            except Exception:  # noqa: BLE001 - 缓存坏了就当没有清单
                manifest = None
            self._manifest = manifest
        return self.select_asset(manifest)

    def _matches_expected(self, path: Path) -> bool:
        """安装包必须与清单登记的文件名/大小/摘要三者一致。

        侧车（`.verify.json`）与包在同一目录、同一用户可写，**它不能自证**：
        真正的信任锚是下载时校验过的那份清单条目。
        """
        asset = self._expected_asset()
        if asset is None:
            return False
        if str(asset.name) != path.name:
            return False
        payload = self._read_verified_meta(path)
        if payload is None:
            return False
        try:
            size = int(payload.get("size"))
        except (TypeError, ValueError):
            return False
        sha = str(payload.get("sha256") or "").lower()
        return size == int(asset.size) and sha == str(asset.sha256).lower()

    def can_auto_install(self) -> bool:
        return self.platform == "windows" and self.frozen

    def select_asset(self, manifest: UpdateManifest | None = None) -> UpdateAsset | None:
        manifest = manifest or self._manifest
        if manifest is None:
            return None
        candidates = [
            asset for asset in manifest.assets
            if _normalize_platform(asset.platform) in {self.platform, "any", "all"}
            and _normalize_architecture(asset.architecture) in {self.architecture, "any", "all"}
            and asset.variant in {"installer", "setup", "portable"}
            and asset.kind in {"installer", "package", "update"}
        ]
        installers = [asset for asset in candidates if asset.variant in {"installer", "setup"} or asset.name.lower().endswith((".exe", ".msi"))]
        return (installers or candidates or [None])[0]

    def install(self, package_path: Path) -> UpdateResult:
        path = Path(package_path)
        if not self.can_auto_install():
            result = UpdateResult(False, "unsupported_runtime", "源码运行或非 Windows 环境不执行原地更新", path=path, manifest=self._manifest)
            self._status = result
            return result
        try:
            path.resolve().relative_to(self.update_dir.resolve())
        except ValueError:
            result = UpdateResult(False, "invalid_package", "更新包必须来自已校验的 updates 缓存目录", path=path, manifest=self._manifest)
            self._status = result
            return result
        if (
            not path.is_file()
            or path.suffix.lower() not in {".exe", ".msi"}
            or not self._package_is_verified(path)
        ):
            result = UpdateResult(False, "invalid_package", "更新包尚未完成下载与校验", path=path, manifest=self._manifest)
            self._status = result
            return result
        if not self._matches_expected(path):
            result = UpdateResult(
                False,
                "invalid_package",
                "更新包与更新清单登记的文件名/大小/摘要不一致，已拒绝安装",
                path=path,
                manifest=self._manifest,
            )
            self._status = result
            return result
        try:
            if self.launcher is not None:
                self.launcher(path)
            else:
                subprocess.Popen([str(path), "/CLOSEAPPLICATIONS", "/NORESTART"], close_fds=True)
            result = UpdateResult(True, "installer_started", "安装器已启动，应用将退出以完成更新", path=path, manifest=self._manifest)
        except OSError as exc:
            result = UpdateResult(False, "installer_failed", f"安装器启动失败：{type(exc).__name__}", path=path, manifest=self._manifest)
        self._status = result
        return result

    @property
    def manifest(self) -> UpdateManifest | None:
        return self._manifest

    def status(self) -> UpdateResult:
        return self._status

    def diagnose(self) -> dict[str, object]:
        return {
            "manifest_url_allowed": self._allowed_url(self.manifest_url),
            "fallback_manifest_urls": list(self.fallback_manifest_urls),
            "fallback_manifest_urls_allowed": all(self._allowed_url(url) for url in self.fallback_manifest_urls),
            "trusted_hosts": sorted(self.trusted_hosts),
            "current_version": self.current_version,
            "platform": self.platform,
            "asset_available": self.select_asset() is not None,
            "architecture": self.architecture,
            "frozen": self.frozen,
            "can_auto_install": self.can_auto_install(),
        }

    def _result_for(self, manifest: UpdateManifest) -> UpdateResult:
        state = "available" if compare_versions(manifest.version, self.current_version) > 0 else "up_to_date"
        message = f"发现新版本 {manifest.version}" if state == "available" else "当前已是最新版本"
        result = UpdateResult(True, state, message, version=manifest.version, manifest=manifest)
        self._status = result
        return result

    def _cancelled(self, message: str) -> UpdateResult:
        result = UpdateResult(
            False,
            "cancelled",
            message,
            version=self.current_version,
            manifest=self._manifest,
        )
        self._status = result
        return result

    def _cache_paths(self) -> tuple[Path, Path]:
        return self.update_dir / "manifest.json", self.update_dir / "manifest.meta.json"

    def _cached_etag(self) -> str:
        _, meta = self._cache_paths()
        try:
            value = json.loads(meta.read_text(encoding="utf-8")).get("etag", "")
        except (OSError, ValueError, TypeError):
            return ""
        return str(value or "").strip()

    def _read_cached_manifest(self) -> UpdateManifest | None:
        path, _ = self._cache_paths()
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_MANIFEST_BYTES:
                return None
            return parse_manifest(json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None

    def _write_cached_manifest(self, raw: bytes, *, etag: str = "") -> None:
        self.update_dir.mkdir(parents=True, exist_ok=True)
        path, meta = self._cache_paths()
        temp = path.with_suffix(".tmp")
        temp.write_bytes(raw)
        os.replace(temp, path)
        meta_temp = meta.with_suffix(".tmp")
        meta_temp.write_text(
            json.dumps({"etag": str(etag or "")}, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(meta_temp, meta)
        runtime_metrics.inc("disk_writes", 2)


__all__ = ["UpdateService"]
