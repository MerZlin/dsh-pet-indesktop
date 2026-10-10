"""Explicit minimal text probe using Core's existing standard-library HTTP stack."""

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from .api_config import CoreApiConfiguration
from .credentials import CredentialError
from .http_compat import normalize_chat_endpoint


@dataclass(frozen=True)
class ProbeResult:
    """Only bounded outcome metadata, never a Provider body or exception text."""

    reason: str
    http_status: int | None = None


def probe_text(cfg, service, secret="", *, backend=None):
    service = service.validate()
    if not service.model:
        raise ValueError("api_model_required")
    if not secret:
        api = CoreApiConfiguration(cfg, backend=backend)
        stored = api.services().get(service.service_id)
        if stored is None or stored.base_url != service.base_url:
            raise CredentialError("credential_missing")
        secret = api.vault.acquire(stored.credential_ref, stored.service_id, stored.base_url, "api.resolve")
    context = ssl.create_default_context()
    if not service.verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        normalize_chat_endpoint(service.base_url, service.chat_path),
        data=json.dumps({"model": service.model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1, "stream": False}).encode("utf-8"),
        headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=min(service.timeout, 15), context=context) as response:
            return ProbeResult("probe_text_ok" if 200 <= response.status < 300 else "probe_http_error", response.status)
    except urllib.error.HTTPError as exc:
        # Never read or reflect the Provider body/URL; it may echo its inputs.
        try:
            exc.close()
        except (AttributeError, KeyError, OSError):
            pass
        return ProbeResult("probe_unauthorized" if exc.code in {401, 403} else "probe_http_error", exc.code)
    except ssl.SSLError:
        return ProbeResult("probe_tls_error")
    except urllib.error.URLError as exc:
        reason = (
            "probe_tls_error" if isinstance(exc.reason, ssl.SSLError) else "probe_timeout" if isinstance(exc.reason, TimeoutError) else "probe_network_error"
        )
        return ProbeResult(reason)
    except TimeoutError:
        return ProbeResult("probe_timeout")
    except OSError:
        return ProbeResult("probe_network_error")
