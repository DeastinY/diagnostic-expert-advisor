from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Entity:
    text: str
    label: str
    start: int
    end: int
    score: float = 1.0
    normalized: str | None = None  # canonical form, e.g. a dictionary concept or a code


@runtime_checkable
class Annotator(Protocol):
    """A model that finds entities in texts. ``annotate`` returns one list per input text."""

    name: str

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]: ...


class FunctionAnnotator:
    """Wrap any ``str -> list[Entity]`` function (handy for tests and quick rules)."""

    def __init__(self, fn: Callable[[str], list[Entity]], name: str = "function") -> None:
        self.fn = fn
        self.name = name

    def annotate(self, texts: Sequence[str]) -> list[list[Entity]]:
        return [self.fn(t) for t in texts]
