"""Old-style API list adapted to the existing Core vault and request ports.

Only connection preferences live here; optional DLC execution remains in its
owner. No network requests or credential reads occur while opening the form.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, replace

from .api_config import ApiService, CoreApiConfiguration, validate_endpoint
from .credentials import CredentialError

PURPOSES = {"chat.send": "main", "files.interpret": "main", "balance.query": "main", "manual_look": "vision", "analyze_frame": "vision"}
DEFAULT_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True)
class SimpleApiProfile:
    service: ApiService
    vision: ApiService | None = None
    vision_url: str = ""
    vision_model: str = ""


@dataclass(frozen=True)
class SimpleApiSettings:
    active_id: str
    profiles: tuple[SimpleApiProfile, ...]


def new_profile():
    return SimpleApiProfile(ApiService(uuid.uuid4().hex, "DeepSeek", "https://api.deepseek.com", model=DEFAULT_MODEL, balance_protocol="deepseek"))


def read_simple(document):
    """Validate persisted selectors without exposing or reading secret values."""
    state = document.get("simple")
    if state is None:
        return None
    try:
        active, profiles = state["active_id"], state["profiles"]
        if not isinstance(active, str) or not isinstance(profiles, dict) or active not in profiles or not isinstance(state["epoch"], str):
            raise ValueError
        result = []
        for pid, entry in profiles.items():
            service = ApiService(**document["services"][pid]).validate()
            vision_id, url, model = entry["vision_id"], entry["vision_url"], entry["vision_model"]
            if not isinstance(vision_id, str) or not isinstance(url, str) or not isinstance(model, str) or len(model) > 256:
                raise ValueError
            if url:
                validate_endpoint(url)
            visual = ApiService(**document["services"][vision_id]).validate() if vision_id else None
            if pid != service.service_id or (visual and visual.service_id != vision_id):
                raise ValueError
            result.append(SimpleApiProfile(service, visual, url, model))
        return SimpleApiSettings(active, tuple(result))
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("api_configuration_invalid") from None


def simple_selection(document, purpose):
    state = read_simple(document)
    if state is None or purpose not in PURPOSES:
        return None
    profile = next(p for p in state.profiles if p.service.service_id == state.active_id)
    main = profile.service
    visual = PURPOSES[purpose] == "vision"
    dedicated = visual and profile.vision is not None and bool(profile.vision.credential_ref)
    service = profile.vision if dedicated else main
    model, fallback = service.model, ""
    if visual:
        # An unused foreign vision draft must not redirect the main credential
        # or choose a model that belongs only to that other provider.
        same_endpoint = not profile.vision_url or profile.vision_url == main.base_url
        model = profile.vision_model if dedicated or same_endpoint else ""
        fallback = main.model if not model else ""
    if purpose == "balance.query":
        service = replace(service, balance_protocol="deepseek")
    return service, {
        "service_id": service.service_id,
        "authorized_endpoint": service.base_url,
        "grant_id": document["simple"]["epoch"],
        "model": model,
        "simple_visual": visual,
        "fallback_model": fallback,
        "credential_source": "vision" if dedicated else "main",
    }


class SimpleApiConfiguration:
    def __init__(self, cfg, *, backend=None):
        self.api = CoreApiConfiguration(cfg, backend=backend)

    def revision(self):
        return self.api.revision()

    def read(self):
        document = self.api.document()
        state = read_simple(document)
        if state is not None:
            return state
        services = self.api.services()
        if not services:
            p = new_profile()
            return SimpleApiSettings(p.service.service_id, (p,))
        # Read-only adaptation of the previous delivered UI; this is not a
        # runtime owner/factory dispatch rule and does not grant execution.
        bindings = document["bindings"]
        chat = bindings.get("official.ai-chat", {}).get("chat.send", {})
        screen = bindings.get("official.screen-understanding", {})
        visual = screen.get("manual_look") or screen.get("analyze_frame") or {}
        active = chat.get("service_id")
        if active not in services:
            active = bindings.get("core.balance", {}).get("balance.query", {}).get("service_id")
        if active not in services:
            active = next(iter(services))
        vp = services.get(visual.get("service_id"))
        result = []
        for pid, service in services.items():
            if vp and pid == vp.service_id and pid != active:
                continue
            if pid == active:
                service = replace(service, model=chat.get("model") or service.model or DEFAULT_MODEL)
                independent = vp if vp and vp.service_id != active else None
                result.append(
                    SimpleApiProfile(
                        service, independent, vp.base_url if independent else "", visual.get("model") or (vp.model if vp else service.vision_model)
                    )
                )
            else:
                result.append(SimpleApiProfile(service, vision_model=service.vision_model))
        return SimpleApiSettings(active, tuple(result))

    def save(self, profiles, active_id, *, expected_revision, keys=None, vision_keys=None, clear_vision=()):
        profiles = tuple(profiles)
        keys, vision_keys = dict(keys or {}), dict(vision_keys or {})
        if not profiles or len({p.service.service_id for p in profiles}) != len(profiles) or active_id not in {p.service.service_id for p in profiles}:
            raise ValueError("api_service_invalid")
        for secret in (*keys.values(), *vision_keys.values()):
            if not isinstance(secret, str) or len(secret) > 16384:
                raise ValueError("api_secret_invalid")
        api = self.api
        with api.port.operation():
            api._recover()
            if api.revision() != expected_revision:
                raise ValueError("configuration_changed")
            before = api.document()
            services = api.services()
            target = copy.deepcopy(before)
            entries, writes = {}, []

            def prepare(service, secret, *, required):
                service = service.validate()
                previous = services.get(service.service_id)
                ref = previous.credential_ref if previous and previous.base_url == service.base_url else ""
                if secret:
                    ref = api.vault.reserve()
                    writes.append((ref, service, secret))
                if required and not ref:
                    raise CredentialError("credential_missing")
                service = replace(service, credential_ref=ref)
                target["services"][service.service_id] = asdict(service)
                return service

            for profile in profiles:
                main = prepare(
                    replace(profile.service, balance_protocol="deepseek"),
                    keys.get(profile.service.service_id, ""),
                    required=profile.service.service_id == active_id,
                )
                pid = main.service_id
                url = validate_endpoint(profile.vision_url) if profile.vision_url else ""
                model = profile.vision_model.strip()
                if len(model) > 256:
                    raise ValueError("api_service_invalid")
                visual = None
                if pid not in clear_vision and (vision_keys.get(pid) or profile.vision):
                    visual_id = profile.vision.service_id if profile.vision else uuid.uuid4().hex
                    # Never overwrite a main service with a dedicated visual key.
                    if visual_id in {p.service.service_id for p in profiles}:
                        raise ValueError("api_service_invalid")
                    transport = profile.vision or main
                    visual = prepare(
                        ApiService(
                            visual_id,
                            (main.name + " · 视觉")[:128],
                            url or main.base_url,
                            transport.chat_path,
                            model or main.model,
                            verify_ssl=transport.verify_ssl,
                            timeout=transport.timeout,
                        ),
                        vision_keys.get(pid, ""),
                        required=True,
                    )
                entries[pid] = {"vision_id": visual.service_id if visual else "", "vision_url": url, "vision_model": model}
            previous_simple = before.get("simple", {})
            target["simple"] = {
                "active_id": active_id,
                "profiles": entries,
                "epoch": previous_simple.get("epoch", uuid.uuid4().hex) if previous_simple.get("active_id") == active_id else uuid.uuid4().hex,
            }
            read_simple(target)
            journal = {"phase": "staging", "created_refs": [r for r, _, _ in writes], "expected_revision": expected_revision}
            api.port.write_journal(journal)
            try:
                for ref, service, secret in writes:
                    api.vault.save(service.service_id, service.base_url, secret, ref=ref)
                api.port.commit_namespace(target, expected_revision=expected_revision)
            except Exception:
                try:
                    api._recover()
                except CredentialError:
                    pass
                raise
            try:
                api.port.write_journal({**journal, "phase": "completed"})
            except OSError:
                pass
        api._publish()
