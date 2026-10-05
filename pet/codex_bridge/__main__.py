"""Launch the optional Codex edition with an isolated user-owned profile."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

from .rpc import catalog
from .startup import resolve_executable, saved_models


def child_command(mode):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--codex-local-bridge" if mode == "server" else "--slot", *([] if mode == "server" else ["0"])]
    return [sys.executable, "-m", "pet.codex_bridge.server" if mode == "server" else "pet"]


def select_model(models, requested=None):
    """A catalog is not an entitlement check; inference can still be refused."""
    if not models:
        return requested or ""
    if requested:
        if not any(item.get("model") == requested for item in models):
            raise ValueError("Requested model is absent from the Codex catalog.")
        return requested
    return next((item["model"] for item in models if item.get("isDefault")), models[0]["model"])


def initialize_profile(profile, executable, models, requested=None):
    from ..config import Config
    from ..chat.models import ProviderConfig, SecretStore

    profile = Path(profile).resolve()
    config = Config(base=profile)
    config.dir.mkdir(parents=True, exist_ok=True)
    path = config.dir / "codex-bridge.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    model = select_model(models, requested or value.get("model"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    value.update(codexExecutable=executable, model=model, modelCatalog=models, port=port, localToken=value.get("localToken") or secrets.token_urlsafe(32))
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        os.chmod(temporary, 0o600)
        json.dump(value, output)
    os.replace(temporary, path)
    store = SecretStore()
    reference = "codex-companion/" + str(profile) + "/local-bridge"
    if not store.set(reference, value["localToken"]):
        raise RuntimeError("A working operating-system keyring is required to save the local bridge credential.")
    settings = config.chat_settings()
    settings.providers["codex-gpt"] = ProviderConfig(
        "codex-gpt", name="GPT · Codex", base_url=f"http://127.0.0.1:{port}", model=model, api_key_ref=reference, timeout=270
    )
    settings.active_provider, settings.enabled = "codex-gpt", bool(model)
    config.set_chat_settings(settings)
    # First-run defaults only: later launch must preserve user-disabled monitors.
    if not config.path.exists():
        config.set("codex_usage_enabled", True)
        config.set("codex_work_status_enabled", True)
    if not config.save():
        raise OSError("Unable to save the Codex edition configuration.")
    return path, value


def wait_ready(process, value, timeout=40):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"http://127.0.0.1:{value['port']}/health", headers={"Authorization": "Bearer " + value["localToken"]})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The local Codex bridge exited before becoming ready.")
        try:
            with opener.open(request, timeout=1) as response:
                if json.load(response).get("ok"):
                    return
        except OSError:
            time.sleep(0.1)  # Bounded startup retry, not a timing-test assumption.
    raise TimeoutError("The local Codex bridge did not become ready.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex")
    parser.add_argument("--model", help="A model ID returned by --list-models")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--profile", type=Path)
    args, pet_args = parser.parse_known_args(argv)
    from ..config import _default_base, APP_DIR_NAME

    profile = (args.profile or _default_base() / "dsh-codex-edition").resolve()
    stored = profile / APP_DIR_NAME / "codex-bridge.json"
    existing = json.loads(stored.read_text(encoding="utf-8")) if stored.exists() else {}
    args.codex = resolve_executable(args.codex or existing.get("codexExecutable"))
    if args.list_models:
        models = catalog(args.codex)
        print(json.dumps([{"id": item["model"], "name": item.get("displayName", item["model"])} for item in models], ensure_ascii=False))
        return 0
    models = saved_models(existing)
    if not models or args.model and not any(item["model"] == args.model for item in models):
        try:
            models = catalog(args.codex)
        except Exception as error:
            logging.warning("Codex catalog unavailable at startup: %s", type(error).__name__)
    path, value = initialize_profile(profile, args.codex, models, args.model)
    environment = dict(os.environ, DSH_CODEX_COMPANION="1", DSH_CODEX_PROFILE=str(profile), DSH_CODEX_BRIDGE_CONFIG=str(path))
    environment.pop("PET_RENDER_TOPOLOGY", None)  # The edition currently targets PetWindow.
    options = {"cwd": str(Path(__file__).resolve().parents[2]), "env": environment, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    bridge = None
    pet = None
    try:
        try:
            bridge = subprocess.Popen(child_command("server") + ["--config", str(path), "--parent-pid", str(os.getpid())], **options)
        except OSError as error:
            logging.warning("Local chat bridge unavailable: %s", type(error).__name__)
        pet = subprocess.Popen(child_command("pet") + pet_args, **options)
        return pet.wait()
    finally:
        if pet is not None and pet.poll() is None:
            pet.terminate()
            pet.wait(timeout=8)
        if bridge is not None:
            bridge.terminate()
            bridge.wait(timeout=8)


if __name__ == "__main__":
    raise SystemExit(main())
