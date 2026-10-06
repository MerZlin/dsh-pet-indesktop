"""Legacy built-in compatibility; excluded from the new small Core."""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from features.ai_chat.host.file_interpret import FileInterpretController as FileInterpretController

from pet.feature_distribution import BUILTIN_AI

if not BUILTIN_AI:
    raise ModuleNotFoundError("AI is not built in; use the verified official package", name="pet.file_interpret")
from features.ai_chat.host import file_interpret as _implementation

sys.modules[__name__] = _implementation
