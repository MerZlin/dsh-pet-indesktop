"""Legacy built-in compatibility; excluded from the new small Core."""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from features.ai_chat.host.chat.models import (
        ChatMessage as ChatMessage,
    )
    from features.ai_chat.host.chat.models import (
        ChatSession as ChatSession,
    )
    from features.ai_chat.host.chat.models import (
        ChatSettings as ChatSettings,
    )
    from features.ai_chat.host.chat.models import (
        ProviderConfig as ProviderConfig,
    )
    from features.ai_chat.host.chat.models import (
        SecretStore as SecretStore,
    )

from pet.feature_distribution import BUILTIN_AI

if not BUILTIN_AI:
    raise ModuleNotFoundError("AI is not built in; use the verified official package", name="pet.chat")
from features.ai_chat.host.chat import models as _implementation

sys.modules[__name__] = _implementation
