from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentResult:
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    error: Optional[str] = None
