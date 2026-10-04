"""Frozen HUMAN ACCEPTANCE ONLY: real production UI, Worker, network and vault.

This is not the automated acceptance driver or a release entry. A fixed guard
prevents source fallback. Only OS startup registration is deliberately excluded:
opening an acceptance build must not inspect, rewrite or delete real Run entries.
"""

from __future__ import annotations

import sys


def protect_autostart() -> None:
    """Exclude system startup registration, not screen/network/credential ports."""
    from pet import autostart

    autostart.cleanup_stale_entries = lambda: 0
    autostart.is_enabled = lambda: False
    autostart.enable = lambda: False
    autostart.disable = lambda: False
    autostart.set_enabled = lambda on: False


def main(argv: list[str] | None = None) -> int:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("requires frozen manual acceptance build; no source fallback")
    import validation_config

    from pet import feature_build_policy as policy

    if not (
        validation_config.MANUAL_ACCEPTANCE_ONLY is True
        and getattr(policy, "MANUAL_ACCEPTANCE_BUILD", False) is True
        and policy.VALIDATION_BUILD is True
        and policy.PROBE_BUNDLE_MANIFEST_SHA256
    ):
        raise RuntimeError("requires frozen manual acceptance policy and pinned helper")
    args = list(sys.argv if argv is None else argv)
    sys.argv = args
    if "--worker" in args:
        from pet.__main__ import _run_worker, _worker_id

        return _run_worker(_worker_id(args))
    protect_autostart()
    if "--settings" in args:
        from pet.__main__ import _run_settings

        return _run_settings()
    from pet.app import main as production_main

    return production_main(argv=args, enable_chat=validation_config.ENABLE_CHAT)


if __name__ == "__main__":
    raise SystemExit(main())
