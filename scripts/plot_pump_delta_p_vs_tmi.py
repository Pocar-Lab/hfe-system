#!/usr/bin/env python3
"""Plot pump pressure rise against TMI for bypass-closed 40% pump samples."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import orca


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIRCULATION_DIR = REPO_ROOT / "data" / "raw" / "recirculation"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "processed" / "recirculation"
RAW_LOG_PATTERN = re.compile(r"^log_(\d{8})_(\d{6})(?:.*)?\.csv$")

PUMP_CMD_TARGET_PCT = 40.0
PUMP_CMD_TOL_PCT = 0.25
TEMP_BIN_WIDTH_C = 2.5
MIN_BIN_SAMPLES = 5

# Notebook-derived manual bypass marker for the Apr 22 run.
BYPASS_CLOSED_MIN_BY_LOG = {
    "log_20260422_143345.csv": 301.95,
}

# The Apr 24 cold run exceeds the default HFE liquid-density gate.
DENSITY_BOUNDS_BY_LOG = {
    "log_20260424_153546.csv": (1200.0, 1800.0),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a pump delta-P vs TMI plot for bypass-closed, 40% pump data "
            "from the latest recirculation logs."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_RECIRCULATION_DIR,
        help="Directory containing recirculation log_*.csv files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated plot and binned summary CSV.",
    )
    parser.add_argument(
        "--latest",
        type=int,
        default=2,
        help="Number of latest recirculation logs to include.",
    )
    return parser.parse_args()


def log_sort_key(path: Path) -> tuple[int, str | int]:
    match = RAW_LOG_PATTERN.match(path.name)
    if match:
        return (1, f"{match.group(1)}{match.group(2)}")
    return (0, path.stat().st_mtime_ns)


def latest_logs(data_dir: Path, count: int) -> list[Path]:
    candidates = sorted(data_dir.glob("log_*.csv"), key=log_sort_key, reverse=True)
    if len(candidates) < count:
        raise FileNotFoundError(f"Found only {len(candidates)} log CSV files in {data_dir}")
    return candidates[:count]


def bypass_closed_time_s(log_path: Path) -> tuple[float, str]:
    if log_path.name in BYPASS_CLOSED_MIN_BY_LOG:
        marker_min = BYPASS_CLOSED_MIN_BY_LOG[log_path.name]
        return marker_min * 60.0, f"notebook marker at {marker_min:.2f} min"

    review_kwargs = {}
    if log_path.name in DENSITY_BOUNDS_BY_LOG:
        review_kwargs["density_bounds"] = DENSITY_BOUNDS_BY_LOG[log_path.name]

    review = orca.prepare_flow_log_review(log_path, **review_kwargs)
    run = orca.segment_slice(review.data, review.segment_summary.loc[review.run_segment_id])
    _, step_windows, _, _ = orca.command_step_summary(run)
    transition = step_windows[
        step_windows["cmd_pct"].shift(1).round().eq(PUMP_CMD_TARGET_PCT)
        & step_windows["cmd_pct"].round().eq(30.0)
    ]
    if transition.empty:
        raise ValueError(f"Could not infer bypass-closed marker for {log_path.name}")
    marker_s = float(transition.iloc[0]["start_s"])
    return marker_s, f"40% to 30% command transition at {marker_s / 60.0:.2f} min"


def numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def type_t_temperature_error_c(temperature_c: pd.Series | np.ndarray) -> np.ndarray:
    values = np.asarray(temperature_c, dtype=float)
    return np.maximum(1.0, 0.0075 * np.abs(values))


def load_selected_samples(log_path: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = pd.read_csv(log_path, comment="#")
    required = [
        "time_s",
        "TMI_C",
        "pump_cmd_pct",
        "pump_freq_hz",
        "pump_pressure_before_bar_abs",
        "pump_pressure_after_bar_abs",
        "pump_pressure_error_bar",
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{log_path} is missing required columns: {', '.join(missing)}")

    for column in required:
        frame[column] = numeric_column(frame, column)

    marker_s, marker_note = bypass_closed_time_s(log_path)
    command_mask = frame["pump_cmd_pct"].sub(PUMP_CMD_TARGET_PCT).abs().le(PUMP_CMD_TOL_PCT)
    mask = (
        frame["time_s"].ge(marker_s)
        & command_mask
        & frame["pump_freq_hz"].gt(0.5)
        & frame[required].notna().all(axis=1)
    )

    selected = frame.loc[mask, required].copy()
    selected["source_log"] = log_path.name
    selected["t_min"] = selected["time_s"] / 60.0
    selected["delta_p_bar"] = (
        selected["pump_pressure_after_bar_abs"] - selected["pump_pressure_before_bar_abs"]
    )
    # Full worst-case error of a difference of two pressure readings.
    selected["delta_p_full_error_bar"] = 2.0 * selected["pump_pressure_error_bar"]
    selected["tmi_error_C"] = type_t_temperature_error_c(selected["TMI_C"])

    info = {
        "log": log_path.name,
        "bypass_marker_s": marker_s,
        "bypass_marker_note": marker_note,
        "selected_samples": int(len(selected)),
        "tmi_min_C": float(selected["TMI_C"].min()) if not selected.empty else np.nan,
        "tmi_max_C": float(selected["TMI_C"].max()) if not selected.empty else np.nan,
    }
    return selected, info


def binned_summary(samples: pd.DataFrame) -> pd.DataFrame:
    t_min = float(samples["TMI_C"].min())
    t_max = float(samples["TMI_C"].max())
    start = np.floor(t_min / TEMP_BIN_WIDTH_C) * TEMP_BIN_WIDTH_C
    stop = np.ceil(t_max / TEMP_BIN_WIDTH_C) * TEMP_BIN_WIDTH_C + TEMP_BIN_WIDTH_C
    edges = np.arange(start, stop + 0.5 * TEMP_BIN_WIDTH_C, TEMP_BIN_WIDTH_C)

    work = samples.copy()
    work["temp_bin"] = pd.cut(work["TMI_C"], bins=edges, include_lowest=True)
    summary = (
        work.groupby("temp_bin", observed=True)
        .agg(
            samples=("delta_p_bar", "size"),
            tmi_C=("TMI_C", "median"),
            tmi_p10_C=("TMI_C", lambda values: float(np.nanpercentile(values, 10))),
            tmi_p90_C=("TMI_C", lambda values: float(np.nanpercentile(values, 90))),
            delta_p_bar=("delta_p_bar", "median"),
            delta_p_p10_bar=("delta_p_bar", lambda values: float(np.nanpercentile(values, 10))),
            delta_p_p90_bar=("delta_p_bar", lambda values: float(np.nanpercentile(values, 90))),
            delta_p_full_error_bar=("delta_p_full_error_bar", "median"),
        )
        .reset_index(drop=True)
    )
    summary = summary[summary["samples"].ge(MIN_BIN_SAMPLES)].copy()
    summary["tmi_error_C"] = type_t_temperature_error_c(summary["tmi_C"])
    return summary.sort_values("tmi_C").reset_index(drop=True)


def make_plot(samples: pd.DataFrame, summary: pd.DataFrame, output_path: Path) -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 130,
            "savefig.dpi": 180,
            "axes.grid": True,
            "grid.alpha": 0.28,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )

    fig, ax = plt.subplots(figsize=(9.0, 6.0), constrained_layout=True)
    colors = {
        "log_20260424_153546.csv": "#2563eb",
        "log_20260422_143345.csv": "#dc2626",
    }
    for log_name, group in samples.groupby("source_log", sort=False):
        ax.scatter(
            group["TMI_C"],
            group["delta_p_bar"],
            s=10,
            alpha=0.22,
            color=colors.get(log_name, "#4b5563"),
            linewidths=0,
            label=f"{log_name} raw samples",
        )

    if not summary.empty:
        x = summary["tmi_C"].to_numpy(float)
        y = summary["delta_p_bar"].to_numpy(float)
        yerr = summary["delta_p_full_error_bar"].to_numpy(float)
        xerr = summary["tmi_error_C"].to_numpy(float)
        ax.fill_between(
            x,
            y - yerr,
            y + yerr,
            color="#111827",
            alpha=0.14,
            linewidth=0,
            label="full pressure-error band",
        )
        ax.errorbar(
            x,
            y,
            xerr=xerr,
            fmt="none",
            ecolor="#111827",
            elinewidth=0.9,
            alpha=0.38,
            capsize=0,
            label="TMI Type-T error",
        )
        ax.plot(
            x,
            y,
            color="#111827",
            linewidth=2.0,
            marker="o",
            markersize=3.5,
            label=f"{TEMP_BIN_WIDTH_C:g} C-bin median",
        )

    pressure_error = float(np.nanmedian(samples["delta_p_full_error_bar"]))
    temp_error = float(np.nanmedian(samples["tmi_error_C"]))
    ax.set_title("Pump delta P vs TMI, bypass closed, pump at 40%")
    ax.set_xlabel("TMI_C [deg C]")
    ax.set_ylabel("Pump delta P: after - before [bar]")
    ax.text(
        0.02,
        0.02,
        (
            f"Selection: bypass closed, pump_cmd_pct = {PUMP_CMD_TARGET_PCT:.0f} +/- "
            f"{PUMP_CMD_TOL_PCT:.2f}%\n"
            f"Pressure band: +/-{pressure_error:.3f} bar full worst-case delta-P error; "
            f"TMI error median: +/-{temp_error:.1f} deg C"
        ),
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "0.8", "alpha": 0.9},
    )
    ax.legend(loc="best", fontsize=8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    logs = latest_logs(args.data_dir, args.latest)
    selected_frames = []
    info_rows = []
    for log_path in logs:
        selected, info = load_selected_samples(log_path)
        selected_frames.append(selected)
        info_rows.append(info)

    samples = pd.concat(selected_frames, ignore_index=True)
    if samples.empty:
        raise RuntimeError("No samples matched the bypass-closed, 40% pump selection.")

    summary = binned_summary(samples)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_path = output_dir / "pump_delta_p_vs_tmi_bypass_closed_40pct.png"
    samples_path = output_dir / "pump_delta_p_vs_tmi_bypass_closed_40pct_samples.csv"
    summary_path = output_dir / "pump_delta_p_vs_tmi_bypass_closed_40pct_binned.csv"
    info_path = output_dir / "pump_delta_p_vs_tmi_bypass_closed_40pct_selection.csv"

    make_plot(samples, summary, plot_path)
    samples.to_csv(samples_path, index=False)
    summary.to_csv(summary_path, index=False)
    pd.DataFrame(info_rows).to_csv(info_path, index=False)

    print(f"Plot: {plot_path}")
    print(f"Selected samples: {samples_path} ({len(samples)} rows)")
    print(f"Binned summary: {summary_path} ({len(summary)} rows)")
    print(f"Selection notes: {info_path}")
    for row in info_rows:
        print(
            f"- {row['log']}: {row['selected_samples']} samples, "
            f"TMI {row['tmi_min_C']:.2f} to {row['tmi_max_C']:.2f} C, "
            f"{row['bypass_marker_note']}"
        )


if __name__ == "__main__":
    main()
