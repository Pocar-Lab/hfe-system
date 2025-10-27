"""Filtering helpers for temperature telemetry."""

from __future__ import annotations

from typing import Iterable

import numpy as np


def rolling_slope(
    t_s: Iterable[float],
    values: Iterable[float],
    window_s: float = 45.0,
    min_pts: int = 5,
) -> np.ndarray:
    """
    Compute the local slope using a rolling least-squares fit.
    """
    t = np.asarray(t_s, dtype=float)
    y = np.asarray(values, dtype=float)
    n = len(t)
    out = np.full(n, np.nan)
    half = window_s / 2.0

    for i in range(n):
        t0, t1 = t[i] - half, t[i] + half
        mask = (t >= t0) & (t <= t1)
        if mask.sum() >= min_pts:
            A = np.vstack([t[mask], np.ones(mask.sum())]).T
            slope, _ = np.linalg.lstsq(A, y[mask], rcond=None)[0]
            out[i] = slope
    return out
