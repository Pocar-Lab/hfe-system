#!/usr/bin/env python3
"""
Dissertation-style single-cycle THM warmup figure.

The plotted cycle is selected internally by the closed-valve segment index used in
previous analyses, but the figure itself does not label the segment/cycle number.

Local default inputs
--------------------
Raw data:
    /home/aamy/Documents/hfe-system/data/raw/recirculation/log_20260424_153546.csv
Calibration table, if available:
    /home/aamy/Documents/hfe-system/data/processed/calibration/TC_calibration_20260420.csv

Outputs are written to the local HFE measurements plot directory if it exists;
otherwise they are written to /mnt/data.
"""
from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy.signal import savgol_filter

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
LOCAL_REPO_ROOT = Path('/home/aamy/Documents/hfe-system')
LOCAL_DATA_PATH = LOCAL_REPO_ROOT / 'data' / 'raw' / 'recirculation' / 'log_20260424_153546.csv'
LOCAL_TC_CALIBRATION_PATH = LOCAL_REPO_ROOT / 'data' / 'processed' / 'calibration' / 'TC_calibration_20260420.csv'
LOCAL_FIGURE_DIR = LOCAL_REPO_ROOT / 'analysis' / 'notebooks' / 'HFE_measurements_plots'

SANDBOX_DIR = Path('/mnt/data')
DATA_PATH = LOCAL_DATA_PATH if LOCAL_DATA_PATH.exists() else SANDBOX_DIR / 'log_20260424_153546.csv'
TC_CALIBRATION_PATH = LOCAL_TC_CALIBRATION_PATH if LOCAL_TC_CALIBRATION_PATH.exists() else None
OUT_DIR = LOCAL_FIGURE_DIR if LOCAL_FIGURE_DIR.exists() else SANDBOX_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_PREFIX = 'hfe_thm_single_cycle_dissertation'
PNG_COLD_CAP = OUT_DIR / f'{OUT_PREFIX}_coldrange_ymax_minus100.png'

# -----------------------------------------------------------------------------
# Analysis settings
# -----------------------------------------------------------------------------
TIME_REFERENCE_OFFSET_MIN = 60.0
SELECTED_S_NUMBER = 20  # internal only; not used in figure labels
THRESHOLD_C = -100.0
SG_WINDOW_FOR_MARKER = 9
MARKER_METHOD = f'SG{SG_WINDOW_FOR_MARKER}_marker_basis'
FEATURE_SEARCH_C = (-121.0, -112.0)

# Fallback constants from the calibration notebooks/previous analysis.
TC_CAL_FALLBACK = {
    'THM': {'gain': 0.990066718500, 'offset_C': -0.434417973250},
    'TTO': {'gain': 1.006182900013, 'offset_C': -0.187982408485},
}

# THM uncertainty model from the dissertation calibration and sensor-spec tables.
ROOM_C = 20.2778
TYPE_K_LOW_C = -35.04
U_ROOM_C = 0.058
U_TYPE_K_LOW_C = 0.060
THM_RECOMMENDED_U_NOISE_C = 0.0440
TYPE_K_SPEC_BASE_C = 2.2
TYPE_K_SPEC_REL_POSITIVE = 0.0075
TYPE_K_SPEC_REL_NEGATIVE = 0.020


def load_affine_calibration(tc: str) -> tuple[float, float]:
    """Return affine calibration T_corr = gain*T_raw + offset_C."""
    if TC_CALIBRATION_PATH is not None and Path(TC_CALIBRATION_PATH).exists():
        table = pd.read_csv(TC_CALIBRATION_PATH)
        row = table.loc[table['TC'].astype(str).str.upper().eq(tc.upper())]
        if not row.empty:
            return float(row['gain'].iloc[0]), float(row['offset_C'].iloc[0])
    cal = TC_CAL_FALLBACK[tc.upper()]
    return float(cal['gain']), float(cal['offset_C'])


THM_GAIN, THM_OFFSET_C = load_affine_calibration('THM')
TTO_GAIN, TTO_OFFSET_C = load_affine_calibration('TTO')


def thm_calibration_uncertainty_C(cal_C):
    """Type-K HX calibration uncertainty from Eq. (5.77) of the dissertation."""
    cal = np.asarray(cal_C, dtype=float)
    span = ROOM_C - TYPE_K_LOW_C
    low_anchor = ((ROOM_C - cal) / span * U_TYPE_K_LOW_C) ** 2
    room_anchor = ((cal - TYPE_K_LOW_C) / span * U_ROOM_C) ** 2
    return np.sqrt(low_anchor + room_anchor)


def thm_type_k_spec_uncertainty_C(cal_C):
    """Standard uncertainty from the bounded Type-K HX specification in Table 5.12."""
    cal = np.asarray(cal_C, dtype=float)
    rel = np.where(cal < 0.0, TYPE_K_SPEC_REL_NEGATIVE, TYPE_K_SPEC_REL_POSITIVE)
    bound = np.maximum(TYPE_K_SPEC_BASE_C, rel * np.abs(cal))
    return bound / np.sqrt(3.0)


def thm_uncertainty_C(cal_C):
    """Complete instantaneous absolute THM reading uncertainty used for the plot band."""
    cal = np.asarray(cal_C, dtype=float)
    readout = THM_RECOMMENDED_U_NOISE_C ** 2
    spec = thm_type_k_spec_uncertainty_C(cal) ** 2
    return np.sqrt(thm_calibration_uncertainty_C(cal) ** 2 + readout + spec)


def gradient_with_noise_uncertainty(t_min, y_C, u_y_C):
    """Nonuniform finite-difference derivative and propagated independent noise."""
    x = np.asarray(t_min, dtype=float)
    y = np.asarray(y_C, dtype=float)
    u = np.asarray(u_y_C, dtype=float)
    n = len(x)
    d = np.full(n, np.nan)
    ud = np.full(n, np.nan)
    if n < 2:
        return d, ud

    dx = x[1] - x[0]
    if dx > 0:
        w = np.array([-1.0 / dx, 1.0 / dx])
        d[0] = np.dot(w, y[:2])
        ud[0] = np.sqrt(np.sum((w * u[:2]) ** 2))

    for i in range(1, n - 1):
        dx1 = x[i] - x[i - 1]
        dx2 = x[i + 1] - x[i]
        if dx1 <= 0 or dx2 <= 0:
            continue
        w0 = -dx2 / (dx1 * (dx1 + dx2))
        w1 = (dx2 - dx1) / (dx1 * dx2)
        w2 = dx1 / (dx2 * (dx1 + dx2))
        w = np.array([w0, w1, w2])
        d[i] = np.dot(w, y[i - 1:i + 2])
        ud[i] = np.sqrt(np.sum((w * u[i - 1:i + 2]) ** 2))

    dx = x[-1] - x[-2]
    if dx > 0:
        w = np.array([-1.0 / dx, 1.0 / dx])
        d[-1] = np.dot(w, y[-2:])
        ud[-1] = np.sqrt(np.sum((w * u[-2:]) ** 2))
    return d, ud


def odd_window_at_most(n: int, target: int) -> int | None:
    if n < 5:
        return None
    w = min(int(target), int(n))
    if w % 2 == 0:
        w -= 1
    return w if w >= 5 else None


def smooth_temperature(T, window: int):
    T = np.asarray(T, dtype=float)
    w = odd_window_at_most(len(T), window)
    if w is None:
        return T.copy()
    return savgol_filter(T, window_length=w, polyorder=min(2, w - 1), mode='interp')


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, comment='#')
    required = ['time_s', 'THM_C', 'TTO_C', 'valve', 'mode']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'Missing required columns: {missing}')

    for col in ['time_s', 'THM_C', 'TTO_C', 'valve']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = (df.dropna(subset=['time_s', 'THM_C', 'TTO_C'])
            .sort_values('time_s')
            .drop_duplicates('time_s')
            .reset_index(drop=True))
    df['mode'] = df['mode'].astype(str).str.strip().str.upper()
    df['t_min'] = df['time_s'] / 60.0
    df['elapsed_min'] = df['t_min'] - TIME_REFERENCE_OFFSET_MIN
    df['THM_raw_C'] = df['THM_C']
    df['TTO_raw_C'] = df['TTO_C']
    df['THM_corr_C'] = THM_GAIN * df['THM_raw_C'] + THM_OFFSET_C
    df['TTO_corr_C'] = TTO_GAIN * df['TTO_raw_C'] + TTO_OFFSET_C
    df['DeltaT_TTO_minus_THM_C'] = df['TTO_corr_C'] - df['THM_corr_C']
    df['THM_u_1sigma_C'] = thm_uncertainty_C(df['THM_corr_C'])
    df['ln_valve_closed'] = ((df['mode'] == 'C') | ((df['mode'] == 'A') & (df['valve'] == 0)))
    df['ln_valve_open'] = ~df['ln_valve_closed']
    m = df['ln_valve_closed']
    df['closed_run_id'] = m.ne(m.shift(fill_value=bool(m.iloc[0]))).cumsum()
    return df


def detect_s_runs(df: pd.DataFrame, max_s: int) -> pd.DataFrame:
    """Closed-valve warmup branches below -100 C, labeled S01, S02, ... internally."""
    rows = []
    s_counter = 0
    selected = df[df['ln_valve_closed']].copy()
    for closed_run_id, run in selected.groupby('closed_run_id', sort=True):
        run = run.sort_values('time_s').copy()
        if len(run) < 3 or float(run['THM_corr_C'].min()) > THRESHOLD_C:
            continue
        T_s = smooth_temperature(run['THM_corr_C'].to_numpy(float), 11)
        min_pos = int(np.nanargmin(T_s))
        min_idx = int(run.index[min_pos])
        branch = run.iloc[min_pos:].copy()
        if len(branch) < 3:
            continue
        branch_rise = float(branch['THM_corr_C'].max() - branch['THM_corr_C'].iloc[0])
        if branch_rise < 0.20:
            continue
        s_counter += 1
        rows.append({
            'S_number': s_counter,
            'closed_run_id': int(closed_run_id),
            'elapsed_min_start_closed_run': float(run['elapsed_min'].iloc[0]),
            'elapsed_min_end_closed_run': float(run['elapsed_min'].iloc[-1]),
            'cold_min_elapsed_min': float(run.loc[min_idx, 'elapsed_min']),
            'cold_min_THM_corr_C': float(run.loc[min_idx, 'THM_corr_C']),
            'end_closed_run_THM_corr_C': float(run['THM_corr_C'].iloc[-1]),
            'n_samples_closed_run': int(len(run)),
        })
        if s_counter >= max_s:
            break
    return pd.DataFrame(rows)


def crossing_time(row0, row1, threshold=THRESHOLD_C):
    t0, T0 = float(row0['elapsed_min']), float(row0['THM_corr_C'])
    t1, T1 = float(row1['elapsed_min']), float(row1['THM_corr_C'])
    if T1 == T0:
        return t1
    f = (threshold - T0) / (T1 - T0)
    return t0 + f * (t1 - t0)


def find_down_crossing_before_min(df: pd.DataFrame, min_t: float):
    before = df[df['elapsed_min'] <= min_t]
    idxs = before.index.to_numpy()
    T = before['THM_corr_C'].to_numpy(float)
    for k in range(len(idxs) - 2, -1, -1):
        if T[k] >= THRESHOLD_C and T[k + 1] < THRESHOLD_C:
            return crossing_time(df.loc[idxs[k]], df.loc[idxs[k + 1]]), int(idxs[k]), int(idxs[k + 1])
    raise RuntimeError('No -100 C down-crossing found before the selected cold minimum.')


def interpolated_minus100_row(df: pd.DataFrame, elapsed_min: float, base_idx: int, crossing_type: str):
    raw_at_thresh = (THRESHOLD_C - THM_OFFSET_C) / THM_GAIN
    base = df.loc[base_idx].copy()
    base['time_s'] = (elapsed_min + TIME_REFERENCE_OFFSET_MIN) * 60.0
    base['t_min'] = elapsed_min + TIME_REFERENCE_OFFSET_MIN
    base['elapsed_min'] = elapsed_min
    base['THM_C'] = raw_at_thresh
    base['THM_raw_C'] = raw_at_thresh
    base['THM_corr_C'] = THRESHOLD_C
    base['THM_u_1sigma_C'] = float(thm_uncertainty_C(THRESHOLD_C))
    base['interpolated_minus100_crossing'] = True
    base['minus100_crossing_type'] = crossing_type
    return pd.DataFrame([base])


def build_selected_trace(df: pd.DataFrame, selected: pd.Series) -> tuple[pd.DataFrame, dict]:
    """Trace from -100 C down-crossing through the first LN-valve-open sample."""
    min_t = float(selected['cold_min_elapsed_min'])
    closed_start_t = float(selected['elapsed_min_start_closed_run'])
    closed_end_t = float(selected['elapsed_min_end_closed_run'])
    down_t, down_before, down_after = find_down_crossing_before_min(df, min_t)

    after_closed = df[(df['elapsed_min'] > closed_end_t) & (~df['ln_valve_closed'])]
    if after_closed.empty:
        first_open_idx = int(df[df['elapsed_min'] >= closed_end_t].index[-1])
    else:
        first_open_idx = int(after_closed.index[0])
    first_open_t = float(df.loc[first_open_idx, 'elapsed_min'])

    tr = df[(df['elapsed_min'] > down_t + 1e-12) &
            (df['elapsed_min'] <= first_open_t + 1e-12)].copy()
    start_row = interpolated_minus100_row(df, down_t, down_after, 'down_to_below_minus100')
    tr['interpolated_minus100_crossing'] = False
    tr['minus100_crossing_type'] = ''
    tr = pd.concat([start_row, tr], ignore_index=True)
    tr = tr.sort_values('elapsed_min').drop_duplicates('elapsed_min', keep='first').reset_index(drop=True)

    tr['time_since_minus100_min'] = tr['elapsed_min'] - down_t
    tr['time_rel_to_cold_min_min'] = tr['elapsed_min'] - min_t
    tr['cycle_phase'] = np.where(tr['elapsed_min'] <= min_t + 1e-12, 'cooling', 'warmup')
    tr['inside_anchor_closed_run'] = ((tr['elapsed_min'] >= closed_start_t - 1e-9) &
                                      (tr['elapsed_min'] <= closed_end_t + 1e-9) &
                                      tr['ln_valve_closed'])

    t = tr['elapsed_min'].to_numpy(float)
    T = tr['THM_corr_C'].to_numpy(float)
    # The derivative band uses the short-term readout-noise scale. The absolute
    # temperature band is handled separately with the complete THM reading model.
    u_noise = np.full(len(tr), THM_RECOMMENDED_U_NOISE_C)
    dTdt, u_dTdt = gradient_with_noise_uncertainty(t, T, u_noise)
    tr['dTHMdt_raw_C_per_min'] = dTdt
    tr['dTHMdt_noise_u_1sigma_C_per_min'] = u_dTdt

    for w in [5, 7, 9, 11]:
        T_s = smooth_temperature(T, w)
        d_s, u_d_s = gradient_with_noise_uncertainty(t, T_s, u_noise)
        tr[f'THM_sg{w}_C'] = T_s
        tr[f'dTHMdt_sg{w}_C_per_min'] = d_s
        tr[f'dTHMdt_sg{w}_noise_u_1sigma_C_per_min'] = u_d_s

    meta = {
        'internal_selected_S_number': SELECTED_S_NUMBER,
        'elapsed_min_down_minus100': down_t,
        'elapsed_min_cold_min': min_t,
        'elapsed_min_closed_run_start': closed_start_t,
        'elapsed_min_closed_run_end': closed_end_t,
        'elapsed_min_first_open_sample': first_open_t,
        'time_since_minus100_cold_min_min': min_t - down_t,
        'time_since_minus100_closed_start_min': closed_start_t - down_t,
        'time_since_minus100_closed_end_min': closed_end_t - down_t,
        'time_since_minus100_first_open_sample_min': first_open_t - down_t,
        'THM_corr_C_cold_min': float(selected['cold_min_THM_corr_C']),
        'THM_corr_C_closed_run_end': float(selected['end_closed_run_THM_corr_C']),
        'THM_corr_C_first_open_sample': float(df.loc[first_open_idx, 'THM_corr_C']),
    }
    return tr, meta


def feature_tests(tr: pd.DataFrame) -> pd.DataFrame:
    warm_closed = tr[(tr['cycle_phase'] == 'warmup') & tr['ln_valve_closed']].copy()
    rows = []
    derivative_methods = [('raw', 'dTHMdt_raw_C_per_min', 'dTHMdt_noise_u_1sigma_C_per_min')]
    for w in [5, 7, 9, 11]:
        name = MARKER_METHOD if w == SG_WINDOW_FOR_MARKER else f'SG{w}'
        derivative_methods.append((
            name,
            f'dTHMdt_sg{w}_C_per_min',
            f'dTHMdt_sg{w}_noise_u_1sigma_C_per_min',
        ))

    for name, ycol, ucol in derivative_methods:
        d = warm_closed[ycol].to_numpy(float)
        T = warm_closed['THM_corr_C'].to_numpy(float)
        t = warm_closed['time_since_minus100_min'].to_numpy(float)
        u = warm_closed[ucol].to_numpy(float)
        m = (T >= FEATURE_SEARCH_C[0]) & (T <= FEATURE_SEARCH_C[1]) & np.isfinite(d)
        idx = np.flatnonzero(m)
        if len(idx) == 0:
            continue
        ip = int(idx[np.nanargmax(d[idx])])
        after = idx[(T[idx] > T[ip] + 0.30)]
        if len(after):
            idip = int(after[np.nanargmin(d[after])])
        else:
            idip = ip
        drop = float(d[ip] - d[idip])
        drop_u = float(np.sqrt(u[ip]**2 + u[idip]**2)) if np.isfinite(u[ip]) and np.isfinite(u[idip]) else np.nan
        rows.append({
            'method': name,
            'peak_time_since_minus100_min': float(t[ip]),
            'peak_THM_corr_C': float(T[ip]),
            'peak_dTHMdt_C_per_min': float(d[ip]),
            'dip_time_since_minus100_min': float(t[idip]),
            'dip_THM_corr_C': float(T[idip]),
            'dip_dTHMdt_C_per_min': float(d[idip]),
            'drop_peak_minus_dip_C_per_min': drop,
            'drop_readout_u_C_per_min': drop_u,
            'drop_over_readout_u': drop / drop_u if np.isfinite(drop_u) and drop_u > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def line_segments_by_valve_state(part: pd.DataFrame):
    """Adjacent two-point segments, styled by the valve state at the endpoint.

    This keeps dashed/solid pieces connected at valve-state transitions without
    introducing artificial gaps.
    """
    p = part.sort_values('time_since_minus100_min').reset_index(drop=True)
    for i in range(1, len(p)):
        seg = p.iloc[i-1:i+1].copy()
        state_endpoint_closed = bool(p.loc[i, 'ln_valve_closed'])
        yield state_endpoint_closed, seg


def make_dissertation_plot(tr: pd.DataFrame,
                            features: pd.DataFrame,
                            meta: dict,
                            output_png: Path,
                            output_pdf: Path | None = None,
                            output_svg: Path | None = None,
                            cold_range_cap: bool = False,
                            warmup_rate_focus: bool = False):
    marker = features.loc[features['method'].eq(MARKER_METHOD)].iloc[0]
    method_temperatures = features['peak_THM_corr_C'].to_numpy(float)
    candidate_T_mean = float(np.nanmean(method_temperatures))
    candidate_T_method_sigma = float(np.nanstd(method_temperatures, ddof=1))

    marker_t = float(marker['peak_time_since_minus100_min'])
    marker_T = float(marker['peak_THM_corr_C'])
    marker_rate = float(marker['peak_dTHMdt_C_per_min'])
    transition_T = marker_T
    transition_u_thm = float(thm_uncertainty_C(transition_T))
    transition_u_thm_cal = float(thm_calibration_uncertainty_C(transition_T))
    transition_u_readout = THM_RECOMMENDED_U_NOISE_C
    transition_u_spec = float(thm_type_k_spec_uncertainty_C(transition_T))
    transition_total_sigma = float(np.sqrt(candidate_T_method_sigma**2 + transition_u_thm**2))
    transition_color = 'tab:red'
    first_open_t = float(meta['time_since_minus100_first_open_sample_min'])
    closed_start_t = float(meta['time_since_minus100_closed_start_min'])

    fig, (axT, axD) = plt.subplots(
        2, 1, figsize=(7.0, 5.2), sharex=True,
        gridspec_kw={'height_ratios': [2.6, 0.78], 'hspace': 0.05},
        constrained_layout=False,
    )

    # Top panel: calibrated THM temperature vs time.
    # Plot two-point segments so line style follows LN valve state cleanly.
    for closed, seg in line_segments_by_valve_state(tr):
        ls = '-' if closed else (0, (4, 2.2))
        lw = 1.65 if closed else 1.35
        alpha = 0.96 if closed else 0.70
        axT.plot(seg['time_since_minus100_min'], seg['THM_corr_C'], linestyle=ls, linewidth=lw,
                 color='0.12', alpha=alpha)

    # THM one-sigma uncertainty envelope.
    axT.fill_between(
        tr['time_since_minus100_min'],
        tr['THM_corr_C'] - tr['THM_u_1sigma_C'],
        tr['THM_corr_C'] + tr['THM_u_1sigma_C'],
        color='0.4', alpha=0.16, linewidth=0,
        label=r'THM 1$\sigma$ absolute reading band',
    )

    # Candidate temperature band from derivative-method scatter + THM uncertainty.
    axT.axhspan(transition_T - transition_total_sigma,
                transition_T + transition_total_sigma,
                color=transition_color, alpha=0.12, linewidth=0)
    axT.axhline(transition_T, color=transition_color, linewidth=1.0, linestyle='--')

    # Event/marker lines.
    axT.axvline(closed_start_t, color='0.35', linestyle=':', linewidth=1.0)
    axT.axvline(first_open_t, color='0.35', linestyle=':', linewidth=1.0)
    axT.axvline(marker_t, color=transition_color, linestyle='--', linewidth=1.2)
    axT.plot(marker_t, transition_T, marker='o', markersize=5.5, color=transition_color, zorder=5)

    # Bottom panel: derivative vs time. Raw finite-difference points,
    # and the smoothed derivative used to stabilize the marker placement. For
    # the dissertation-friendly version, the lower panel is restricted to the
    # warmup branch so that the candidate max/decrease is visible instead of
    # being compressed by the large negative cooling rate.
    dplot = tr[tr['cycle_phase'].eq('warmup')].copy() if warmup_rate_focus else tr.copy()
    d_raw = dplot['dTHMdt_raw_C_per_min'].to_numpy(float)
    t = dplot['time_since_minus100_min'].to_numpy(float)
    axD.plot(t, d_raw, '.', markersize=3.2, color='0.45', alpha=0.55, label='raw finite difference')

    # Smoothed derivative by two-point state segments.
    sg_col = f'dTHMdt_sg{SG_WINDOW_FOR_MARKER}_C_per_min'
    sg_u_col = f'dTHMdt_sg{SG_WINDOW_FOR_MARKER}_noise_u_1sigma_C_per_min'
    d_sg = dplot[sg_col].to_numpy(float)
    u_sg = dplot[sg_u_col].to_numpy(float)
    m = np.isfinite(d_sg) & np.isfinite(u_sg)
    axD.fill_between(t[m], d_sg[m] - u_sg[m], d_sg[m] + u_sg[m],
                     color='black', alpha=0.12, linewidth=0)
    for closed, seg in line_segments_by_valve_state(dplot):
        ls = '-' if closed else (0, (4, 2.2))
        lw = 1.45 if closed else 1.25
        alpha = 0.96 if closed else 0.70
        axD.plot(seg['time_since_minus100_min'], seg[sg_col], linestyle=ls, linewidth=lw,
                 color='black', alpha=alpha)

    axD.axvline(closed_start_t, color='0.35', linestyle=':', linewidth=1.0)
    axD.axvline(first_open_t, color='0.35', linestyle=':', linewidth=1.0)
    axD.axvline(marker_t, color=transition_color, linestyle='--', linewidth=1.2)
    axD.plot(marker_t, marker_rate, marker='o', markersize=5.0, color=transition_color, zorder=5)
    axD.axhline(0, color='0.35', linewidth=0.8, alpha=0.65)

    # Compact valve-state labels inside the temperature panel.
    trans = axT.get_xaxis_transform()
    axT.text(closed_start_t, 0.985, 'LN valve closes', transform=trans, rotation=90,
             ha='right', va='top', fontsize=8.0, color='0.25')
    axT.text(first_open_t - 0.02, 0.02, 'LN valve opens', transform=trans, rotation=90,
             ha='right', va='bottom', fontsize=8.0, color='0.25')

    # Axes formatting.
    axT.set_ylabel('HX temperature (middle, °C)')
    axD.set_ylabel(r'dT/dt (°C min$^{-1}$)')
    axD.set_xlabel('Elapsed time (min)')

    axT.set_xlim(-0.03, tr['time_since_minus100_min'].max() + 0.08)
    if cold_range_cap:
        axT.set_ylim(-138.5, -95.0)
    else:
        axT.set_ylim(-138.5, -95.8)
    if warmup_rate_focus:
        axD.set_ylim(-8, 45)
    else:
        axD.set_ylim(-155, 50)

    for ax in (axT, axD):
        ax.grid(True, which='major', alpha=0.26, linewidth=0.6)
        ax.grid(True, which='minor', alpha=0.10, linewidth=0.35)
        ax.minorticks_on()
        ax.tick_params(direction='in', top=True, right=True)

    # Legend: no cycle/segment label.
    legend_handles = [
        Line2D([0], [0], marker='o', color=transition_color, lw=1.2, linestyle='--',
               label=f'Transition point ({transition_T:.1f} °C)'),
        Patch(facecolor=transition_color, alpha=0.12, edgecolor='none',
              label=r'1$\sigma$ band (for the transition value)'),
    ]
    axT.legend(handles=legend_handles, loc='best', fontsize=8.2, frameon=True, framealpha=0.92)

    fig.subplots_adjust(left=0.115, right=0.985, top=0.975, bottom=0.12)
    fig.savefig(output_png, dpi=400)
    if output_pdf is not None:
        fig.savefig(output_pdf)
    if output_svg is not None:
        fig.savefig(output_svg)
    plt.close(fig)

    return {
        'candidate_T_mean_C': candidate_T_mean,
        'candidate_T_method_sigma_C': candidate_T_method_sigma,
        'transition_T_C': transition_T,
        'transition_T_THM_calibration_uncertainty_C': transition_u_thm_cal,
        'transition_T_THM_readout_uncertainty_C': transition_u_readout,
        'transition_T_THM_type_k_spec_uncertainty_C': transition_u_spec,
        'transition_T_THM_total_uncertainty_C': transition_u_thm,
        'transition_T_total_sigma_C': transition_total_sigma,
        'marker_time_since_minus100_min': marker_t,
        'marker_THM_corr_C': marker_T,
        'marker_dTHMdt_C_per_min': marker_rate,
        'minutes_marker_before_first_open': first_open_t - marker_t,
        'temperature_marker_to_first_open_C': meta['THM_corr_C_first_open_sample'] - marker_T,
    }


def main():
    df = load_data(DATA_PATH)
    s_runs = detect_s_runs(df, max_s=SELECTED_S_NUMBER)
    selected_row = s_runs[s_runs['S_number'].eq(SELECTED_S_NUMBER)]
    if selected_row.empty:
        raise RuntimeError(f'Could not find selected closed-valve segment S{SELECTED_S_NUMBER:02d}.')

    tr, meta = build_selected_trace(df, selected_row.iloc[0])
    features = feature_tests(tr)

    cold_cap_summary = make_dissertation_plot(
        tr, features, meta,
        output_png=PNG_COLD_CAP,
        cold_range_cap=True,
        warmup_rate_focus=True,
    )

    meta.update(cold_cap_summary)
    meta['calibration_THM_gain'] = THM_GAIN
    meta['calibration_THM_offset_C'] = THM_OFFSET_C
    meta['calibration_TTO_gain'] = TTO_GAIN
    meta['calibration_TTO_offset_C'] = TTO_OFFSET_C
    meta['data_path_used'] = str(DATA_PATH)
    meta['tc_calibration_path_used'] = str(TC_CALIBRATION_PATH) if TC_CALIBRATION_PATH else 'fallback constants'

    print(json.dumps({
        'cold_cap_png': str(PNG_COLD_CAP),
        'summary': meta,
    }, indent=2))


if __name__ == '__main__':
    main()
