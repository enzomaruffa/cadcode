"""Self-calibrating print-time coefficients, fit from real slicer runs.

The instant estimator (`print_time.py`) models minutes as a linear combination
of geometric feature-seconds:  minutes = Σ coeff[k] · feature_s[k] / 60.

Every time PrusaSlicer runs (`/print/slice`) we get ground truth (minutes) for
a concrete plate, plus that plate's estimator features. We keep those samples
and refit the coefficients by ridge-regularized least squares — regularized
TOWARD the hand-tuned defaults so early on (few samples) we barely move, and as
real data accumulates the fit takes over. Coefficients + samples persist to the
workspace volume, so calibration survives restarts and improves with use.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np

from app.kernel.print_time import FEATURES, default_coeffs

_LOCK = threading.Lock()
_MAX_SAMPLES = 500
_RIDGE = 6.0  # pull toward defaults; ~ how many "default samples" of prior weight


def _store_path() -> Path:
    base = os.environ.get("CAD_WORKSPACE") or str(Path(__file__).resolve().parents[2] / ".workspace")
    return Path(base) / "print_calibration.json"


def _load() -> dict:
    try:
        return json.loads(_store_path().read_text())
    except Exception:
        return {"coeffs": default_coeffs(), "samples": []}


_state = _load()


def coeffs() -> dict[str, float]:
    """Current calibrated coefficients (defaults until slicer runs teach us)."""
    c = _state.get("coeffs") or default_coeffs()
    return {k: float(c.get(k, default_coeffs()[k])) for k in FEATURES}


def _fit(samples: list[dict]) -> dict[str, float]:
    """Ridge least squares: minutes ≈ Σ c_k · (feature_s_k / 60), regularized
    toward the defaults. Non-negative. Falls back to defaults if degenerate."""
    d = default_coeffs()
    if not samples:
        return d
    keys = list(FEATURES)
    x = np.array([[s["features"].get(k, 0.0) / 60.0 for k in keys] for s in samples], dtype=float)
    y = np.array([s["minutes"] for s in samples], dtype=float)
    prior = np.array([d[k] for k in keys], dtype=float)
    # Augment with √λ·I rows targeting the prior (per-feature ridge).
    reg = np.sqrt(_RIDGE) * np.eye(len(keys))
    xa = np.vstack([x, reg])
    ya = np.concatenate([y, np.sqrt(_RIDGE) * prior])
    try:
        sol, *_ = np.linalg.lstsq(xa, ya, rcond=None)
    except Exception:
        return d
    sol = np.clip(sol, 0.0, None)
    if not np.all(np.isfinite(sol)):
        return d
    return {k: float(sol[i]) for i, k in enumerate(keys)}


def record(features: dict[str, float], slicer_minutes: float) -> dict[str, float]:
    """Add a (features, slicer minutes) sample, refit, persist. Returns new coeffs."""
    if slicer_minutes <= 0 or not features:
        return coeffs()
    feats = {k: float(features.get(k, 0.0)) for k in FEATURES}
    with _LOCK:
        samples = _state.setdefault("samples", [])
        # Dedup: re-slicing the same plate should UPDATE its sample, not pile on
        # near-duplicate rows that would drown out the rest of the calibration.
        sig = tuple(round(feats[k], 1) for k in FEATURES)
        for s in samples:
            if tuple(round(s["features"].get(k, 0.0), 1) for k in FEATURES) == sig:
                s["minutes"] = float(slicer_minutes)
                break
        else:
            samples.append({"features": feats, "minutes": float(slicer_minutes)})
        del samples[:-_MAX_SAMPLES]
        _state["coeffs"] = _fit(samples)
        try:
            p = _store_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_state))
        except Exception:
            pass  # calibration is best-effort; never break a slice on a write error
        return coeffs()


def status() -> dict:
    """{samples, coeffs} for surfacing calibration progress in the UI."""
    return {"samples": len(_state.get("samples") or []), "coeffs": coeffs()}
