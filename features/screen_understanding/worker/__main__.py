"""Self-contained screen worker entry; no GUI startup."""

from .runtime import run_proactive_screen_worker

if __name__ == "__main__":
    raise SystemExit(run_proactive_screen_worker())
