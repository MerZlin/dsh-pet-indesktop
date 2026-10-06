"""Legacy built-in compatibility; excluded from the new small Core."""

import sys

from pet.feature_distribution import BUILTIN_AI

if not BUILTIN_AI:
    raise ModuleNotFoundError("AI is not built in; use the verified official package", name="pet.quick_chat")
from features.ai_chat.host import quick_chat as _implementation

sys.modules[__name__] = _implementation
