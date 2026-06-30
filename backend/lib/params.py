"""Typed parameter annotations for slider extraction (plan §6).

Slider ranges as *typed Python*, not a magic comment:

    from typing import Annotated
    from lib.params import Range

    WIDTH: Annotated[float, Range(20, 160)] = 80
    TEETH: Annotated[int, Range(8, 40, step=1)] = 20

At runtime the value is just the number; ``Range`` is annotation metadata the
editor reads to build the slider. Plays nicely with type checkers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Range:
    min: float
    max: float
    step: float | None = None
