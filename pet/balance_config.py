"""Core-owned balance authorization, independent of AI provider implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .credentials import CoreBalanceVault
from .feature_config import bind_feature_configuration, namespace_revision

OWNER = "core.balance"
DEFAULT_ENDPOINT = "https://api.deepseek.com"


@dataclass(frozen=True)
class BalanceRequest:
    base_url: str = DEFAULT_ENDPOINT
    api_key: str = field(default="", repr=False)
    verify_ssl: bool = True
    id: str = "core.balance"


def _endpoint(value):
    try:
        if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 33 for c in value):
            raise ValueError
        parts = urlsplit(value)
        if (
            not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or (parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "::1", "localhost"}))
        ):
            raise ValueError
        parts.port  # reject malformed ports before any credential write
        return value.rstrip("/")
    except (ValueError, TypeError):
        raise ValueError("balance_endpoint_invalid") from None


class BalanceConfiguration:
    def __init__(self, config, *, backend=None):
        layout = getattr(config, "runtime_layout", None)
        if layout is None:
            raise ValueError("balance_layout_required")
        self.vault = CoreBalanceVault(layout.data_root_id, str(config.instance_id or "primary"), backend=backend)
        self.port = bind_feature_configuration(config, OWNER, journal_path=None)
        self.reload()

    def reload(self):
        value = self.port.read_namespace()
        if value and (
            set(value) != {"format_version", "endpoint", "credential_ref"}
            or type(value.get("format_version")) is not int
            or value["format_version"] != 1
            or not isinstance(value.get("credential_ref"), str)
        ):
            raise ValueError("balance_configuration_invalid")
        if value:
            _endpoint(value["endpoint"])
        self.value = value
        self.revision = namespace_revision(value)

    def save(self, endpoint, secret, *, expected_revision):
        endpoint = _endpoint(endpoint)
        if not isinstance(secret, str) or len(secret) > 16384:
            raise ValueError("balance_secret_invalid")
        if self.port.revision() != expected_revision:
            raise ValueError("configuration_changed")
        previous_endpoint = self.value.get("endpoint", DEFAULT_ENDPOINT)
        ref = self.value.get("credential_ref", "") if endpoint == previous_endpoint else ""
        created = None
        try:
            if secret.strip():
                created = ref = self.vault.save("balance", endpoint, secret)
            self.port.commit_namespace({"format_version": 1, "endpoint": endpoint, "credential_ref": ref}, expected_revision=expected_revision)
        except Exception:
            if created:
                try:
                    self.vault.delete(created, authorized=True)
                except Exception:
                    pass  # retain only our orphan OS entry; never delete older/user secrets
            raise
        self.reload()

    def request(self):
        self.reload()
        endpoint = self.value.get("endpoint", DEFAULT_ENDPOINT)
        ref = self.value.get("credential_ref", "")
        secret = self.vault.acquire(ref, "balance", endpoint, "balance.query") if ref else ""
        return BalanceRequest(endpoint, secret)


def resolve_balance_request(config, *, backend=None):
    from .feature_distribution import BUILTIN_AI

    if BUILTIN_AI:
        # Old accepted complete/no-chat distributions retain their original route.
        provider = config.chat_settings().active_config
        provider.api_key = config.resolve_api_key(provider)
        return provider
    return BalanceConfiguration(config, backend=backend).request()
