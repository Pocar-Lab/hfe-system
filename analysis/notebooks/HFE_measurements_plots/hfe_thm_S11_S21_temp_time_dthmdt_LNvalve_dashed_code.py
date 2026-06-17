#!/usr/bin/env python3
"""
Selected HFE warmup/cooldown cycles: corrected THM temperature and dT/dt vs time.

This reproduces the selected-cycle plot with:
  * x-axis = elapsed time since the first -100 C corrected-THM crossing;
  * traces from each cycle's -100 C down-crossing through its cold minimum to its
    -100 C warmup crossing;
  * y-axis capped at -100 C;
  * solid line when the LN valve is closed;
  * dashed line when the LN valve is open;
  * THM 1-sigma absolute uncertainty bands in the temperature panel;
  * finite-difference readout-noise 1-sigma uncertainty bands in the dTHM/dt panel;
  * one slope-change marker per selected warmup, plus a horizontal mean-T line and
    total 1-sigma band.

Local paths are set to match the HFE repository notebooks.  If run outside that
repository, the script falls back to /mnt/data for this ChatGPT analysis session.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy.signal import savgol_filter, find_peaks

# -----------------------------------------------------------------------------
# Paths from the original notebooks / repository layout
# -----------------------------------------------------------------------------
LOCAL_REPO_ROOT = Path('/home/aamy/Documents/hfe-system')
LOCAL_DATA_PATH = LOCAL_REPO_ROOT / 'data' / 'raw' / 'recirculation' / 'log_20260424_153546.csv'
LOCAL_TC_CALIBRATION_PATH = LOCAL_REPO_ROOT / 'data' / 'processed' / 'calibration' / 'TC_calibration_20260420.csv'
LOCAL_FIGURE_DIR = LOCAL_REPO_ROOT / 'analysis' / 'notebooks' / 'HFE_measurements_plots'

# Fallbacks for the sandbox where this answer was generated.
SANDBOX_DIR = Path('/mnt/data')
DATA_PATH = LOCAL_DATA_PATH if LOCAL_DATA_PATH.exists() else SANDBOX_DIR / 'log_20260424_153546.csv'
TC_CALIBRATION_PATH = LOCAL_TC_CALIBRATION_PATH if LOCAL_TC_CALIBRATION_PATH.exists() else None
OUT_DIR = LOCAL_FIGURE_DIR if LOCAL_FIGURE_DIR.exists() else SANDBOX_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_PNG = OUT_DIR / 'hfe_thm_S11_S21_temp_time_dthmdt_LNvalve_dashed.png'

# -----------------------------------------------------------------------------
# Analysis settings
# -----------------------------------------------------------------------------
TIME_ORIGIN_MIN = 60.0
THRESHOLD_C = -100.0
PLOT_TEMPERATURE_YMAX_C = -100.0
# Original S11-S21 plot window, with the upper bound treated as exclusive.
SELECTED_S_NUMBERS = list(range(11, 21))
DISPLAY_S_NUMBERS = list(range(1, len(SELECTED_S_NUMBERS) + 1))

DISSERTATION_FIGSIZE = (12.8, 8.2)
DISSERTATION_LABEL_FONTSIZE = 20
DISSERTATION_TICK_FONTSIZE = 16
DISSERTATION_LEGEND_FONTSIZE = 18
DISSERTATION_LINEWIDTH = 2.0
DISSERTATION_FIT_LINEWIDTH = 3.0
DISSERTATION_SINGLE_ADJUST = dict(left=0.12, right=0.98, top=0.98, bottom=0.14)
DISSERTATION_STACKED_ADJUST = dict(left=0.16, right=0.98, top=0.98, bottom=0.14, hspace=0.12)
MEAN_PHASE_LINESTYLE = (0, (8.0, 2.2, 1.8, 2.2))

# Active THM calibration fallback from TC_calibration.ipynb / active calibration.
# If TC_CALIBRATION_PATH exists, it is used instead.
THM_GAIN_FALLBACK = 0.990066718500
THM_OFFSET_C_FALLBACK = -0.434417973250

# THM 1-sigma uncertainty model from TC_reading_uncertainty.ipynb.
ROOM_C = 20.2778
TYPE_K_LOW_C = -35.04
U_ROOM_C = 0.058
U_TYPE_K_LOW_C = 0.060
THM_RECOMMENDED_U_NOISE_C = 0.0440
THM_EXTRAPOLATION_MODEL_FRACTION = 0.01

MIN_BRANCH_POINTS = 3
MIN_BRANCH_RISE_C = 0.20
MIN_SMOOTH_TARGET = 11
SAVGOL_POLYORDER = 2

# Slope-change marker detection parameters. Light smoothing is used only for
# marker placement. The plotted dTHM/dt is raw corrected finite difference.
SLOPE_DETECT_SAVGOL_WINDOW = 7
SLOPE_PEAK_SEARCH_C = (-121.0, -113.0)
SLOPE_FALLBACK_SEARCH_C = (-119.0, -114.0)
MIN_PEAK_PROMINENCE_C_PER_MIN = 0.50
MIN_PEAK_DISTANCE_POINTS = 2


# -----------------------------------------------------------------------------
# Calibration and uncertainty helpers
# -----------------------------------------------------------------------------
def load_affine_calibration(tc: str = 'THM') -> tuple[float, float]:
    """Return gain, offset_C for a TC channel.

    Uses data/processed/calibration/TC_calibration_20260420.csv if available;
    otherwise falls back to the constants used in this analysis.
    """
    if TC_CALIBRATION_PATH is not None and Path(TC_CALIBRATION_PATH).exists():
        table = pd.read_csv(TC_CALIBRATION_PATH)
        row = table.loc[table['TC'].astype(str).str.upper().eq(tc.upper())]
        if row.empty:
            raise RuntimeError(f'No calibration row found for {tc!r} in {TC_CALIBRATION_PATH}')
        return float(row['gain'].iloc[0]), float(row['offset_C'].iloc[0])
    if tc.upper() == 'THM':
        return THM_GAIN_FALLBACK, THM_OFFSET_C_FALLBACK
    raise RuntimeError(f'No fallback calibration is defined for {tc!r}')


THM_GAIN, THM_OFFSET_C = load_affine_calibration('THM')


def thm_calibrated(raw_C):
    return THM_GAIN * np.asarray(raw_C, dtype=float) + THM_OFFSET_C


def thm_extrapolation_uncertainty_C(cal_C):
    cal = np.asarray(cal_C, dtype=float)
    return THM_EXTRAPOLATION_MODEL_FRACTION * np.maximum(0.0, TYPE_K_LOW_C - cal)


def thm_uncertainty_C(cal_C):
    """Absolute instantaneous THM 1-sigma uncertainty.

    This includes calibration anchors, readout noise, and the low-temperature
    extrapolation model term. It is appropriate for the temperature panel and
    for the slope-change temperature spread.
    """
    cal = np.asarray(cal_C, dtype=float)
    span = ROOM_C - TYPE_K_LOW_C
    low_anchor = ((ROOM_C - cal) / span * U_TYPE_K_LOW_C) ** 2
    room_anchor = ((cal - TYPE_K_LOW_C) / span * U_ROOM_C) ** 2
    noise = THM_RECOMMENDED_U_NOISE_C ** 2
    extrap = thm_extrapolation_uncertainty_C(cal) ** 2
    return np.sqrt(low_anchor + room_anchor + noise + extrap)


def gradient_with_noise_uncertainty(t_min, y_C, u_y_C):
    """np.gradient-like derivative with linear uncertainty propagation.

    For non-uniform time samples, np.gradient uses three-point finite-difference
    weights in the interior and first-order differences at the edges. This
    function uses the same derivative formulas and propagates independent
    point-to-point readout noise through the finite-difference weights.

    The absolute calibration/model uncertainty is intentionally not propagated
    into dT/dt because common offset/model terms do not create point-to-point
    rate noise. The calibration gain is already applied to both y and u_y.
    """
    x = np.asarray(t_min, dtype=float)
    y = np.asarray(y_C, dtype=float)
    u = np.asarray(u_y_C, dtype=float)
    n = len(x)
    dydx = np.full(n, np.nan, dtype=float)
    udydx = np.full(n, np.nan, dtype=float)
    if n < 2:
        return dydx, udydx

    # first edge
    dx = x[1] - x[0]
    if dx > 0:
        weights = np.array([-1.0 / dx, 1.0 / dx])
        dydx[0] = weights[0] * y[0] + weights[1] * y[1]
        udydx[0] = np.sqrt(np.sum((weights * u[:2]) ** 2))

    # interior: nonuniform 3-point finite-difference weights
    for i in range(1, n - 1):
        dx1 = x[i] - x[i - 1]
        dx2 = x[i + 1] - x[i]
        if dx1 <= 0 or dx2 <= 0:
            continue
        w0 = -dx2 / (dx1 * (dx1 + dx2))
        w1 = (dx2 - dx1) / (dx1 * dx2)
        w2 = dx1 / (dx2 * (dx1 + dx2))
        weights = np.array([w0, w1, w2])
        yy = y[i - 1:i + 2]
        uu = u[i - 1:i + 2]
        dydx[i] = float(np.dot(weights, yy))
        udydx[i] = float(np.sqrt(np.sum((weights * uu) ** 2)))

    # last edge
    dx = x[-1] - x[-2]
    if dx > 0:
        weights = np.array([-1.0 / dx, 1.0 / dx])
        dydx[-1] = weights[0] * y[-2] + weights[1] * y[-1]
        udydx[-1] = np.sqrt(np.sum((weights * u[-2:]) ** 2))

    return dydx, udydx


# -----------------------------------------------------------------------------
# Loading, S-run detection, and cycle extraction
# -----------------------------------------------------------------------------
def load_data(path=DATA_PATH):
    df = pd.read_csv(path, comment='#')
    for col in ['time_s', 'THM_C', 'valve']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = (
        df.dropna(subset=['time_s', 'THM_C'])
          .sort_values('time_s')
          .drop_duplicates('time_s')
          .reset_index(drop=True)
    )
    df['mode'] = df['mode'].astype(str).str.strip().str.upper()
    df['t_min'] = df['time_s'] / 60.0
    df['elapsed_min'] = df['t_min'] - TIME_ORIGIN_MIN
    df['THM_raw_C'] = df['THM_C']
    df['THM_corr_C'] = thm_calibrated(df['THM_raw_C'])
    df['THM_u_1sigma_C'] = thm_uncertainty_C(df['THM_corr_C'])

    # LN-valve state used for plotting and for S-run detection:
    # forced-close mode is closed; automatic mode is closed only when valve == 0.
    df['ln_valve_closed'] = (
        (df['mode'] == 'C') |
        ((df['mode'] == 'A') & (df['valve'] == 0))
    )
    df['ln_valve_open'] = ~df['ln_valve_closed']
    m = df['ln_valve_closed']
    df['closed_run_id'] = m.ne(m.shift(fill_value=bool(m.iloc[0]))).cumsum()
    return df


def odd_window_at_most(n, target):
    if n < 5:
        return None
    w = min(n, target)
    if w % 2 == 0:
        w -= 1
    return w if w >= 5 else None


def smooth_for_minimum(T, target=MIN_SMOOTH_TARGET):
    w = odd_window_at_most(len(T), target)
    if w is None:
        return np.asarray(T, dtype=float)
    return savgol_filter(T, window_length=w, polyorder=min(SAVGOL_POLYORDER, w - 1), mode='interp')


def detect_s_runs(df, max_s=max(SELECTED_S_NUMBERS)):
    """Detect closed-valve warmup branches and assign S01, S02, ... labels."""
    rows = []
    s_counter = 0
    selected = df[df['ln_valve_closed']].copy()
    for closed_run_id, run in selected.groupby('closed_run_id', sort=True):
        run = run.sort_values('time_s').copy()
        if len(run) < 2 or float(run['THM_corr_C'].min()) > THRESHOLD_C:
            continue

        T = run['THM_corr_C'].to_numpy(float)
        T_min_locator = smooth_for_minimum(T)
        min_pos = int(np.nanargmin(T_min_locator))
        min_idx = int(run.index[min_pos])
        branch = run.iloc[min_pos:].copy()
        branch_rise = float(branch['THM_corr_C'].max() - branch['THM_corr_C'].iloc[0]) if len(branch) else 0.0
        branch_kept = (
            len(branch) >= MIN_BRANCH_POINTS and
            branch_rise >= MIN_BRANCH_RISE_C and
            not (branch['THM_corr_C'].max() < -135.0 or branch['THM_corr_C'].min() > THRESHOLD_C)
        )
        if not branch_kept:
            continue

        s_counter += 1
        rows.append({
            'S_number': s_counter,
            'label': f'S{s_counter:02d}',
            'closed_run_id': int(closed_run_id),
            'elapsed_min_start_closed_run': float(run['elapsed_min'].iloc[0]),
            'elapsed_min_end_closed_run': float(run['elapsed_min'].iloc[-1]),
            'duration_closed_run_min': float(run['t_min'].iloc[-1] - run['t_min'].iloc[0]),
            'n_samples_closed_run': int(len(run)),
            'cold_min_elapsed_min': float(run.loc[min_idx, 'elapsed_min']),
            'cold_min_THM_corr_C': float(run.loc[min_idx, 'THM_corr_C']),
            'cold_min_sample_index_in_closed_run': int(min_pos),
            'start_closed_run_THM_corr_C': float(run['THM_corr_C'].iloc[0]),
            'end_closed_run_THM_corr_C': float(run['THM_corr_C'].iloc[-1]),
            'max_closed_run_THM_corr_C': float(run['THM_corr_C'].max()),
            'warmup_branch_rise_C': branch_rise,
        })
        if s_counter >= max_s:
            break
    return pd.DataFrame(rows)


def crossing_time_between(row0, row1, threshold=THRESHOLD_C):
    t0, T0 = float(row0['elapsed_min']), float(row0['THM_corr_C'])
    t1, T1 = float(row1['elapsed_min']), float(row1['THM_corr_C'])
    if T1 == T0:
        return t1
    f = (threshold - T0) / (T1 - T0)
    return t0 + f * (t1 - t0)


def find_down_crossing_before_min(df, min_t):
    before = df[df['elapsed_min'] <= min_t].copy()
    idxs = before.index.to_numpy()
    T = before['THM_corr_C'].to_numpy(float)
    for k in range(len(idxs) - 2, -1, -1):
        if T[k] >= THRESHOLD_C and T[k + 1] < THRESHOLD_C:
            t_cross = crossing_time_between(df.loc[idxs[k]], df.loc[idxs[k + 1]])
            return float(t_cross), int(idxs[k]), int(idxs[k + 1])
    below = before[before['THM_corr_C'] < THRESHOLD_C]
    if below.empty:
        raise RuntimeError('No down-crossing or below-threshold point before cold minimum.')
    i = int(below.index[0])
    return float(df.loc[i, 'elapsed_min']), i, i


def find_up_crossing_after_min(df, min_t):
    after = df[df['elapsed_min'] >= min_t].copy()
    idxs = after.index.to_numpy()
    T = after['THM_corr_C'].to_numpy(float)
    for k in range(len(idxs) - 1):
        if T[k] < THRESHOLD_C and T[k + 1] >= THRESHOLD_C:
            t_cross = crossing_time_between(df.loc[idxs[k]], df.loc[idxs[k + 1]])
            return float(t_cross), int(idxs[k]), int(idxs[k + 1])
    i = int(after.index[-1])
    return float(df.loc[i, 'elapsed_min']), i, i


def interpolated_crossing_row(df, elapsed_min, base_idx, S_number, label, crossing_type):
    raw_at_thresh = (THRESHOLD_C - THM_OFFSET_C) / THM_GAIN
    base = df.loc[base_idx].copy()
    base['time_s'] = (elapsed_min + TIME_ORIGIN_MIN) * 60.0
    base['t_min'] = elapsed_min + TIME_ORIGIN_MIN
    base['elapsed_min'] = elapsed_min
    base['THM_raw_C'] = raw_at_thresh
    base['THM_C'] = raw_at_thresh
    base['THM_corr_C'] = THRESHOLD_C
    base['THM_u_1sigma_C'] = float(thm_uncertainty_C(THRESHOLD_C))
    base['S_number'] = S_number
    base['label'] = label
    base['interpolated_minus100_crossing'] = True
    base['minus100_crossing_type'] = crossing_type
    return pd.DataFrame([base])


def build_full_cycle_trace(df, s_run, global_time_zero_elapsed_min):
    source_s = int(s_run['S_number'])
    s = int(s_run.get('display_S_number', source_s))
    label = str(s_run.get('display_label', f'S{s:02d}'))
    min_t = float(s_run['cold_min_elapsed_min'])
    closed_start_t = float(s_run['elapsed_min_start_closed_run'])
    closed_end_t = float(s_run['elapsed_min_end_closed_run'])

    down_t, down_before_idx, down_after_idx = find_down_crossing_before_min(df, min_t)
    up_t, up_before_idx, up_after_idx = find_up_crossing_after_min(df, min_t)

    tr = df[(df['elapsed_min'] > down_t + 1e-12) & (df['elapsed_min'] < up_t - 1e-12)].copy()
    start_row = interpolated_crossing_row(df, down_t, down_after_idx, s, label, 'down_to_below_minus100')
    end_row = interpolated_crossing_row(df, up_t, up_before_idx, s, label, 'up_to_minus100')
    tr['S_number'] = s
    tr['source_S_number'] = source_s
    tr['label'] = label
    tr['interpolated_minus100_crossing'] = False
    tr['minus100_crossing_type'] = ''
    tr = pd.concat([start_row, tr, end_row], ignore_index=True)
    tr['S_number'] = s
    tr['source_S_number'] = source_s
    tr['label'] = label
    tr = tr.sort_values('elapsed_min').drop_duplicates('elapsed_min', keep='first').reset_index(drop=True)

    # Cap to the requested maximum temperature. Exact crossing points are retained.
    tr = tr[tr['THM_corr_C'] <= THRESHOLD_C + 1e-9].copy().reset_index(drop=True)

    tr['time_since_first_cycle_minus100_min'] = tr['elapsed_min'] - global_time_zero_elapsed_min
    tr['time_since_cycle_down_minus100_min'] = tr['elapsed_min'] - down_t
    tr['time_rel_to_cold_min_min'] = tr['elapsed_min'] - min_t
    tr['cycle_phase'] = np.where(tr['elapsed_min'] <= min_t + 1e-12, 'cooling', 'warmup')
    tr['inside_anchor_closed_run'] = (
        (tr['elapsed_min'] >= closed_start_t - 1e-9) &
        (tr['elapsed_min'] <= closed_end_t + 1e-9) &
        tr['ln_valve_closed']
    )

    # Corrected derivative and propagated readout-noise uncertainty.
    t = tr['elapsed_min'].to_numpy(float)
    T = tr['THM_corr_C'].to_numpy(float)
    u_noise_corr = np.full(len(tr), abs(THM_GAIN) * THM_RECOMMENDED_U_NOISE_C, dtype=float)
    dTdt, u_dTdt = gradient_with_noise_uncertainty(t, T, u_noise_corr)
    tr['dTHMdt_raw_C_per_min'] = dTdt
    tr['dTHMdt_noise_u_1sigma_C_per_min'] = u_dTdt

    # Smooth derivative for warmup-marker placement only.
    warm = tr['elapsed_min'].to_numpy(float) >= min_t - 1e-12
    Tsm = np.full(len(tr), np.nan)
    dsm = np.full(len(tr), np.nan)
    if warm.sum() >= 2:
        Tw = T[warm]
        tw = t[warm]
        w = odd_window_at_most(len(Tw), SLOPE_DETECT_SAVGOL_WINDOW)
        Tsm_w = Tw.copy() if w is None else savgol_filter(Tw, window_length=w, polyorder=min(2, w - 1), mode='interp')
        dsm_w, _ = gradient_with_noise_uncertainty(tw, Tsm_w, np.full(len(Tw), abs(THM_GAIN) * THM_RECOMMENDED_U_NOISE_C))
        Tsm[warm] = Tsm_w
        dsm[warm] = dsm_w
    tr['THM_smooth_for_marker_C'] = Tsm
    tr['dTHMdt_smooth_for_marker_C_per_min'] = dsm

    meta = {
        'S_number': s,
        'source_S_number': source_s,
        'label': label,
        'elapsed_min_down_minus100': down_t,
        'elapsed_min_cold_min': min_t,
        'elapsed_min_up_minus100': up_t,
        'time_since_first_cycle_minus100_down_min': down_t - global_time_zero_elapsed_min,
        'time_since_first_cycle_cold_min_min': min_t - global_time_zero_elapsed_min,
        'time_since_first_cycle_up_minus100_min': up_t - global_time_zero_elapsed_min,
        'THM_cold_min_corr_C': float(s_run['cold_min_THM_corr_C']),
        'duration_from_down_to_up_min': up_t - down_t,
        'elapsed_min_start_closed_run': closed_start_t,
        'elapsed_min_end_closed_run': closed_end_t,
        'closed_run_id': int(s_run['closed_run_id']),
        'n_points_plotted': int(len(tr)),
        'n_points_ln_valve_closed': int(tr['ln_valve_closed'].sum()),
        'n_points_ln_valve_open': int((~tr['ln_valve_closed']).sum()),
    }
    return tr, meta


# -----------------------------------------------------------------------------
# Marker detection
# -----------------------------------------------------------------------------
def find_slope_change_marker(tr):
    warm = tr['cycle_phase'].eq('warmup')
    sub = tr[warm].copy().reset_index(drop=False).rename(columns={'index': 'orig_index'})
    T = sub['THM_corr_C'].to_numpy(float)
    dsm = sub['dTHMdt_smooth_for_marker_C_per_min'].to_numpy(float)

    def candidate_from_mask(mask):
        idx = np.flatnonzero(mask & np.isfinite(dsm))
        if len(idx) < 4:
            return None
        dsw = dsm[idx]
        peaks, props = find_peaks(dsw, prominence=MIN_PEAK_PROMINENCE_C_PER_MIN,
                                  distance=MIN_PEAK_DISTANCE_POINTS)
        candidates = []
        for ip, p in enumerate(peaks):
            local_i = int(idx[p])
            Tpeak = float(T[local_i])
            dpeak = float(dsm[local_i])
            after = idx[(T[idx] > Tpeak + 0.30) & (T[idx] < Tpeak + 3.00)]
            if len(after):
                dip = float(np.nanmin(dsm[after]))
                drop = dpeak - dip
            else:
                dip = np.nan
                drop = -np.inf
            prominence = float(props['prominences'][ip]) if 'prominences' in props else np.nan
            candidates.append((drop, prominence, dpeak, local_i, dip, Tpeak))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (np.nan_to_num(x[0], nan=-np.inf),
                                       np.nan_to_num(x[1], nan=-np.inf), x[2]), reverse=True)
        return candidates[0]

    mask_main = (T >= SLOPE_PEAK_SEARCH_C[0]) & (T <= SLOPE_PEAK_SEARCH_C[1])
    cand = candidate_from_mask(mask_main)
    if cand is not None:
        drop, prominence, dpeak, local_i, dip, Tpeak = cand
        method = 'local_max_followed_by_decrease'
    else:
        mask_fb = (T >= SLOPE_FALLBACK_SEARCH_C[0]) & (T <= SLOPE_FALLBACK_SEARCH_C[1]) & np.isfinite(dsm)
        idxs = np.flatnonzero(mask_fb)
        if len(idxs) == 0:
            idxs = np.flatnonzero(mask_main & np.isfinite(dsm))
        if len(idxs) == 0:
            idxs = np.flatnonzero(np.isfinite(dsm))
        local_i = int(idxs[np.nanargmax(dsm[idxs])])
        method = 'fallback_max_smooth_dTHMdt'
        drop = prominence = dip = np.nan

    row = sub.iloc[local_i]
    return {
        'S_number': int(row['S_number']),
        'label': str(row['label']),
        'elapsed_min': float(row['elapsed_min']),
        'time_since_first_cycle_minus100_min': float(row['time_since_first_cycle_minus100_min']),
        'time_since_cycle_down_minus100_min': float(row['time_since_cycle_down_minus100_min']),
        'time_rel_to_cold_min_min': float(row['time_rel_to_cold_min_min']),
        'THM_corr_C': float(row['THM_corr_C']),
        'THM_u_1sigma_C': float(thm_uncertainty_C(row['THM_corr_C'])),
        'dTHMdt_raw_C_per_min': float(row['dTHMdt_raw_C_per_min']),
        'dTHMdt_smooth_marker_C_per_min': float(row['dTHMdt_smooth_for_marker_C_per_min']),
        'method': method,
        'post_peak_drop_C_per_min': float(drop) if np.isfinite(drop) else np.nan,
        'peak_prominence_C_per_min': float(prominence) if np.isfinite(prominence) else np.nan,
        'post_peak_dip_C_per_min': float(dip) if np.isfinite(dip) else np.nan,
        'ln_valve_closed_at_marker': bool(row['ln_valve_closed']),
        'ln_valve_open_at_marker': bool(row['ln_valve_open']),
        'inside_anchor_closed_run': bool(row['inside_anchor_closed_run']),
    }


def slope_marker_summary(markers, time_zero_elapsed_min):
    vals = markers['THM_corr_C'].to_numpy(float)
    us = markers['THM_u_1sigma_C'].to_numpy(float)
    N = int(np.isfinite(vals).sum())
    mean_T = float(np.nanmean(vals)) if N else np.nan
    std_cycle = float(np.nanstd(vals, ddof=1)) if N >= 2 else np.nan
    mean_u = float(np.sqrt(np.nanmean(us ** 2))) if N else np.nan
    total_spread = float(np.sqrt(std_cycle ** 2 + mean_u ** 2)) if N >= 2 else mean_u
    return {
        'N_selected_cycles': len(SELECTED_S_NUMBERS),
        'selected_cycles': ','.join(f'C{s}' for s in DISPLAY_S_NUMBERS),
        'data_path': str(DATA_PATH),
        'tc_calibration_path_used': str(TC_CALIBRATION_PATH) if TC_CALIBRATION_PATH else 'fallback constants',
        'THM_gain': THM_GAIN,
        'THM_offset_C': THM_OFFSET_C,
        'time_zero_definition': f'corrected THM={THRESHOLD_C:.0f} C down-crossing of C1, first displayed cycle',
        'time_zero_elapsed_min_from_log_reference': float(time_zero_elapsed_min),
        'N_slope_markers': N,
        'mean_slope_change_THM_corr_C': mean_T,
        'cycle_to_cycle_std_slope_change_THM_C': std_cycle,
        'mean_THM_sensor_u_at_slope_points_C': mean_u,
        'total_1sigma_spread_slope_change_THM_C': total_spread,
        'N_markers_LN_valve_closed': int(markers['ln_valve_closed_at_marker'].sum()),
        'N_markers_LN_valve_open': int(markers['ln_valve_open_at_marker'].sum()),
        'N_markers_inside_anchor_closed_run': int(markers['inside_anchor_closed_run'].sum()),
        'N_markers_in_extension_to_minus100': int((~markers['inside_anchor_closed_run']).sum()),
    }


# -----------------------------------------------------------------------------
# Plotting helpers
# -----------------------------------------------------------------------------
def _apply_fine_grid(ax):
    ax.minorticks_on()
    ax.grid(True, which='major', alpha=0.34, linewidth=0.8)
    ax.grid(True, which='minor', alpha=0.16, linestyle=':', linewidth=0.6)


def state_segments_for_plot(part: pd.DataFrame, state_col='ln_valve_closed'):
    """Yield contiguous pieces with the same valve state, with shared boundary rows.

    The boundary row is included at the end of the preceding segment and the start
    of the next segment. This keeps the dashed/open and solid/closed pieces visually
    connected instead of leaving tiny gaps at LN-valve transitions.
    """
    p = part.sort_values('time_since_first_cycle_minus100_min').reset_index(drop=True)
    if p.empty:
        return
    states = p[state_col].astype(bool).to_numpy()
    start = 0
    current = states[0]
    n = len(p)
    for i in range(1, n):
        if states[i] != current:
            yield current, p.iloc[start:i + 1].copy()  # include transition row
            start = i
            current = states[i]
    yield current, p.iloc[start:n].copy()


def plot_with_uncertainty(ax_T, ax_d, piece, color, linestyle, alpha_line, alpha_band):
    xx = piece['time_since_first_cycle_minus100_min'].to_numpy(float)
    TT = piece['THM_corr_C'].to_numpy(float)
    TTu = piece['THM_u_1sigma_C'].to_numpy(float)
    dd = piece['dTHMdt_raw_C_per_min'].to_numpy(float)
    ddu = piece['dTHMdt_noise_u_1sigma_C_per_min'].to_numpy(float)

    ax_T.plot(xx, TT, lw=DISSERTATION_LINEWIDTH, ls=linestyle, alpha=alpha_line, color=color)
    ax_T.fill_between(xx, TT - TTu, TT + TTu, color=color, alpha=alpha_band, lw=0)
    ax_d.plot(xx, dd, lw=DISSERTATION_LINEWIDTH * 0.8, ls=linestyle,
              alpha=max(alpha_line - 0.18, 0.22), color=color)
    md = np.isfinite(dd) & np.isfinite(ddu)
    if md.any():
        ax_d.fill_between(xx[md], dd[md] - ddu[md], dd[md] + ddu[md], color=color, alpha=alpha_band, lw=0)


def plot_output(traces, markers, summary):
    fig, (ax_T, ax_d) = plt.subplots(
        2, 1, figsize=DISSERTATION_FIGSIZE, sharex=True,
        gridspec_kw={'height_ratios': [2.8, 1.25], 'hspace': 0.12}
    )

    default_colors = plt.rcParams['axes.prop_cycle'].by_key().get('color', [])

    for ii, (s, tr) in enumerate(traces.groupby('S_number', sort=True)):
        tr = tr.sort_values('time_since_first_cycle_minus100_min').copy()
        color = default_colors[ii % len(default_colors)] if default_colors else None

        # Solid = LN valve closed. Dashed = LN valve open.
        # The segmentation duplicates boundary rows so the styles connect cleanly.
        for valve_closed, piece in state_segments_for_plot(tr, 'ln_valve_closed'):
            linestyle = '-' if valve_closed else '--'
            alpha_line = 0.88 if valve_closed else 0.50
            alpha_band = 0.070 if valve_closed else 0.040
            plot_with_uncertainty(ax_T, ax_d, piece, color, linestyle, alpha_line, alpha_band)

        # Labels near the cold minimum.
        cold = tr.iloc[np.nanargmin(tr['THM_corr_C'].to_numpy(float))]
        ax_T.text(float(cold['time_since_first_cycle_minus100_min']) + 0.04,
                  float(cold['THM_corr_C']) - 0.55,
                  str(cold['label']), fontsize=12, ha='left', va='top')

    mean_marker_T = float(summary['mean_slope_change_THM_corr_C'])
    sigma_marker_T = float(summary['total_1sigma_spread_slope_change_THM_C'])
    ax_T.axhspan(mean_marker_T - sigma_marker_T, mean_marker_T + sigma_marker_T,
                 color='black', alpha=0.12, zorder=0)
    ax_T.axhline(mean_marker_T, lw=DISSERTATION_LINEWIDTH, ls=MEAN_PHASE_LINESTYLE,
                 color='black', alpha=0.85)
    ax_T.axhline(THRESHOLD_C, lw=1.0, ls=':', color='black', alpha=0.75)

    for _, row in markers.iterrows():
        x = row['time_since_first_cycle_minus100_min']
        ax_T.plot(x, row['THM_corr_C'], marker='o', ms=8.0, mec='black', mew=0.65, zorder=5)
        ax_d.plot(x, row['dTHMdt_raw_C_per_min'], marker='o', ms=6.5, mec='black', mew=0.55, zorder=5)

    # Axes and labels.
    xmin = 0.0
    xmax = float(traces['time_since_first_cycle_minus100_min'].max()) * 1.01
    ax_T.set_xlim(xmin, xmax)
    ax_T.set_ylim(-155.0, PLOT_TEMPERATURE_YMAX_C)
    finite_d = traces['dTHMdt_raw_C_per_min'].replace([np.inf, -np.inf], np.nan).dropna()
    d_low = float(np.nanmin(finite_d)) if len(finite_d) else -80.0
    d_high = float(np.nanmax(finite_d)) if len(finite_d) else 80.0
    d_margin = max(8.0, 0.05 * (d_high - d_low))
    ax_d.set_ylim(min(-100.0, d_low - d_margin), max(100.0, d_high + d_margin))

    ax_T.set_ylabel('HX temperature (middle, °C)', fontsize=DISSERTATION_LABEL_FONTSIZE)
    ax_d.set_ylabel('dT/dt (°C/min)', fontsize=DISSERTATION_LABEL_FONTSIZE)
    ax_d.set_xlabel('Elapsed time (min)', fontsize=DISSERTATION_LABEL_FONTSIZE)

    for ax in (ax_T, ax_d):
        ax.tick_params(axis='both', which='major', labelsize=DISSERTATION_TICK_FONTSIZE,
                       length=7, width=1.2)
        ax.tick_params(axis='both', which='minor', length=4, width=1.0)
        for spine in ax.spines.values():
            spine.set_linewidth(1.2)
        _apply_fine_grid(ax)

    style_handles = [
        Line2D([0], [0], lw=DISSERTATION_LINEWIDTH, ls='--', color='black',
               label='LN valve open'),
        Line2D([0], [0], lw=DISSERTATION_LINEWIDTH, ls='-', color='black',
               label='LN valve closed'),
        Line2D([0], [0], marker='o', markersize=8, lw=0, mec='black',
               color='black', label='Phase transition'),
        Line2D([0], [0], lw=DISSERTATION_LINEWIDTH, ls=MEAN_PHASE_LINESTYLE, color='black',
               label='Mean phase transition temperature'),
        Patch(facecolor='black', alpha=0.12, label=r'$\pm$ 1 sigma spread'),
    ]
    ax_T.legend(handles=style_handles, loc='lower left',
                fontsize=DISSERTATION_LEGEND_FONTSIZE, ncols=2, framealpha=0.92)
    fig.align_ylabels([ax_T, ax_d])
    fig.subplots_adjust(**DISSERTATION_STACKED_ADJUST)
    return fig


def main():
    df = load_data(DATA_PATH)
    s_runs = detect_s_runs(df, max_s=max(SELECTED_S_NUMBERS))
    selected_runs = s_runs[s_runs['S_number'].isin(SELECTED_S_NUMBERS)].copy()
    if len(selected_runs) != len(SELECTED_S_NUMBERS):
        raise RuntimeError(f'Expected {len(SELECTED_S_NUMBERS)} selected S-runs, found {len(selected_runs)}')
    selected_runs = selected_runs.sort_values('S_number').reset_index(drop=True)
    selected_runs['display_S_number'] = np.arange(1, len(selected_runs) + 1, dtype=int)
    selected_runs['display_label'] = selected_runs['display_S_number'].map(lambda s: f'C{s}')

    # t=0 is the first -100 C down-crossing of the first displayed cycle, C1.
    first = selected_runs[selected_runs['S_number'] == SELECTED_S_NUMBERS[0]].iloc[0]
    time_zero, _, _ = find_down_crossing_before_min(df, float(first['cold_min_elapsed_min']))

    traces_list = []
    for _, row in selected_runs.iterrows():
        tr, _ = build_full_cycle_trace(df, row, time_zero)
        traces_list.append(tr)
    traces = pd.concat(traces_list, ignore_index=True)

    markers = pd.DataFrame([find_slope_change_marker(g.copy()) for _, g in traces.groupby('S_number', sort=True)])
    summary = slope_marker_summary(markers, time_zero)

    fig = plot_output(traces, markers, summary)
    fig.savefig(OUT_PNG, dpi=220)
    plt.close(fig)

    print(f'Wrote {OUT_PNG}')


if __name__ == '__main__':
    main()
