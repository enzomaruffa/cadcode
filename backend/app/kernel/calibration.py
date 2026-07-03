"""Self-calibrating print-time multipliers, fit from real slicer runs.

The instant estimator (`print_time.py`) models a plate's minutes as

    minutes = (base_scale · base_seconds + support_scale · support_seconds) / 60

where base/support seconds are the geometric feature estimate at default weights.
Two multipliers — an overall speed bias and a separate SUPPORT bias (the one that
varies wildly and drove the old error) — are robust to fit from a handful of
correlated samples, unlike five per-feature coefficients (which overfit: walls
would collapse to zero).

Every real PrusaSlicer run (`/print/slice`) contributes a (base_s, support_s) →
true-minutes sample. We refit both multipliers by ridge-regularized least
squares pulled toward 1.0, so few samples barely move and real data takes over
as it accumulates. Persisted to the workspace volume — survives restarts.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np

from app.kernel.print_time import base_support_seconds, default_mult

_LOCK = threading.Lock()
_MAX_SAMPLES = 500
_RIDGE = 3.0  # pull both multipliers toward 1.0 (≈ this many "prior samples")


def _store_path() -> Path:
    base = os.environ.get("CAD_WORKSPACE") or str(Path(__file__).resolve().parents[2] / ".workspace")
    return Path(base) / "print_calibration.json"


def _load() -> dict:
    try:
        d = json.loads(_store_path().read_text())
        if "mult" in d:
            return d
    except Exception:
        pass
    return {"mult": default_mult(), "samples": []}


_state = _load()


def mult() -> dict[str, float]:
    """Current calibration multipliers {base, support} (1.0 until slices teach us)."""
    m = _state.get("mult") or default_mult()
    return {"base": float(m.get("base", 1.0)), "support": float(m.get("support", 1.0))}


def _fit(samples: list[dict]) -> dict[str, float]:
    """Ridge least squares for [base, support] scales: minutes ≈ (b·base_s +
    s·support_s)/60, regularized toward 1.0. Non-negative; defaults if degenerate."""
    if not samples:
        return default_mult()
    rows, y = [], []
    for smp in samples:
        rows.append([smp["base_s"] / 60.0, smp["support_s"] / 60.0])
        y.append(smp["minutes"])
    x = np.array(rows, dtype=float)
    yv = np.array(y, dtype=float)
    reg = np.sqrt(_RIDGE) * np.eye(2)
    xa = np.vstack([x, reg])
    ya = np.concatenate([yv, np.sqrt(_RIDGE) * np.array([1.0, 1.0])])
    try:
        sol, *_ = np.linalg.lstsq(xa, ya, rcond=None)
    except Exception:
        return default_mult()
    sol = np.clip(sol, 0.0, None)
    if not np.all(np.isfinite(sol)):
        return default_mult()
    return {"base": float(sol[0]), "support": float(sol[1])}


def record(features: dict[str, float], slicer_minutes: float) -> dict[str, float]:
    """Add a (features → true minutes) sample, refit multipliers, persist."""
    if slicer_minutes <= 0 or not features:
        return mult()
    base_s, support_s = base_support_seconds(features)
    with _LOCK:
        samples = _state.setdefault("samples", [])
        # Dedup: re-slicing the same plate updates its sample, doesn't pile on.
        sig = (round(base_s, 1), round(support_s, 1))
        for s in samples:
            if (round(s["base_s"], 1), round(s["support_s"], 1)) == sig:
                s["minutes"] = float(slicer_minutes)
                break
        else:
            samples.append({"base_s": base_s, "support_s": support_s, "minutes": float(slicer_minutes)})
        del samples[:-_MAX_SAMPLES]
        _state["mult"] = _fit(samples)
        try:
            p = _store_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_state))
        except Exception:
            pass  # calibration is best-effort; never break a slice on a write error
        return mult()


def status() -> dict:
    """{samples, mult} for surfacing calibration progress in the UI."""
    return {"samples": len(_state.get("samples") or []), "mult": mult()}
