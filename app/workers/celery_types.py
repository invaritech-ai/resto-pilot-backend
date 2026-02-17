from __future__ import annotations

from typing import Any, Mapping, Protocol


class CeleryDelayable(Protocol):
    def delay(self, *args: Any, **kwargs: Any) -> Any: ...


class CeleryApplyAsync(Protocol):
    def apply_async(
        self,
        args: tuple[Any, ...] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> Any: ...

