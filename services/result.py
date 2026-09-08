from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    code: str
    data: dict[str, Any] = field(default_factory=dict)
