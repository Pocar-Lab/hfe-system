#!/usr/bin/env python3
"""Retroactively normalize legacy pump/VFD fields in historical CSV logs.

The early flow logs used legacy VFD telemetry:
- `pump_input_power_w` was derived from Fuji M10 with a 400 W scale.
- `pump_output_current_a` was derived from Fuji M11 with a 2.8 A scale.
- `pump_rotation_speed_rpm` and `pump_input_power_kw` were not logged yet.

This script rewrites selected CSV files in place, with one-time backups, so they
line up with the newer pump/VFD conventions used by the firmware, supervisor,
and Pump tab:
- power rescaled to a 1 HP motor basis (746 W),
- current rescaled to the 1 HP FRENIC-Mini inverter-rated-current basis (5.5 A),
- estimated rotation speed backfilled as `pump_freq_hz * 30`,
- input power in kW backfilled from the corrected watt value,
- legacy percent fields, when present, refreshed to the current pump-limit basis.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from statistics import median


DEFAULT_LOGS = [
    "data/raw/recirculation/log_20260417_094053.csv",
    "data/raw/recirculation/log_20260403_115916.csv",
    "data/raw/recirculation/log_20260402_150754.csv",
    "data/raw/recirculation/log_20260330_161922.csv",
]

BACKUP_DIR_NAME = "_pump_log_backups"

LEGACY_POWER_BASE_W = 400.0
CORRECTED_POWER_BASE_W = 746.0
LEGACY_CURRENT_BASE_A = 2.8
CORRECTED_CURRENT_BASE_A = 5.5
PUMP_NAMEPLATE_CURRENT_A = 3.4
PUMP_BASE_VOLTAGE_V = 230.0
PUMP_RATED_OUTPUT_KW = 0.746
PUMP_NAMEPLATE_EFFICIENCY = 0.855
PUMP_EST_RATED_INPUT_KW = PUMP_RATED_OUTPUT_KW / PUMP_NAMEPLATE_EFFICIENCY
PUMP_EST_RPM_PER_HZ = 30.0
DEFAULT_MAX_FREQ_HZ = 71.7

POWER_SCALE = CORRECTED_POWER_BASE_W / LEGACY_POWER_BASE_W
CURRENT_SCALE = CORRECTED_CURRENT_BASE_A / LEGACY_CURRENT_BASE_A


@dataclass(frozen=True)
class FileSummary:
    path: Path
    rows: int
    scaled_legacy_fields: bool
    median_power_before_w: float | None
    median_power_after_w: float | None
    median_current_before_a: float | None
    median_current_after_a: float | None
    median_freq_hz: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill corrected pump/VFD fields in legacy CSV logs.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=DEFAULT_LOGS,
        help="CSV log paths to fix in place. Defaults to the known legacy pump runs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the detected corrections without rewriting any files.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Rewrite files without creating a backup copy first.",
    )
    return parser.parse_args()


def parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def format_float(value: float | None, digits: int) -> str:
    if value is None or not math.isfinite(value):
        return "nan"
    return f"{value:.{digits}f}"


def insert_after(fieldnames: list[str], anchor: str, field: str) -> None:
    if field in fieldnames:
        return
    if anchor in fieldnames:
        fieldnames.insert(fieldnames.index(anchor) + 1, field)
    else:
        fieldnames.append(field)


def ensure_field_order(original: list[str]) -> list[str]:
    fieldnames = list(original)
    insert_after(fieldnames, "pump_freq_hz", "pump_rotation_speed_rpm")
    insert_after(fieldnames, "pump_rotation_speed_rpm", "pump_input_power_kw")
    return fieldnames


def maybe_default_max_freq_hz(row: dict[str, str]) -> float:
    value = parse_float(row.get("pump_max_freq_hz"))
    return value if value is not None and value > 0.0 else DEFAULT_MAX_FREQ_HZ


def already_normalized(fieldnames: list[str]) -> bool:
    return "pump_rotation_speed_rpm" in fieldnames and "pump_input_power_kw" in fieldnames


def fix_rows(rows: list[dict[str, str]], fieldnames: list[str]) -> tuple[list[dict[str, str]], FileSummary]:
    normalized = already_normalized(fieldnames)
    output_rows: list[dict[str, str]] = []

    power_before: list[float] = []
    power_after: list[float] = []
    current_before: list[float] = []
    current_after: list[float] = []
    freqs: list[float] = []

    for row in rows:
        updated = dict(row)

        freq_hz = parse_float(row.get("pump_freq_hz"))
        power_w = parse_float(row.get("pump_input_power_w"))
        current_a = parse_float(row.get("pump_output_current_a"))
        voltage_v = parse_float(row.get("pump_output_voltage_v"))
        max_freq_hz = maybe_default_max_freq_hz(row)

        pump_active = freq_hz is not None and freq_hz > 0.5

        if pump_active and freq_hz is not None:
            freqs.append(freq_hz)
        if pump_active and power_w is not None:
            power_before.append(power_w)
        if pump_active and current_a is not None:
            current_before.append(current_a)

        if not normalized:
            if power_w is not None:
                power_w *= POWER_SCALE
            if current_a is not None:
                current_a *= CURRENT_SCALE

        if pump_active and power_w is not None:
            power_after.append(power_w)
        if pump_active and current_a is not None:
            current_after.append(current_a)

        power_kw = power_w / 1000.0 if power_w is not None else None
        rotation_speed_rpm = freq_hz * PUMP_EST_RPM_PER_HZ if freq_hz is not None else None

        if "pump_freq_hz" in updated:
            updated["pump_freq_hz"] = format_float(freq_hz, 2)
        updated["pump_rotation_speed_rpm"] = format_float(rotation_speed_rpm, 0)
        updated["pump_input_power_kw"] = format_float(power_kw, 2)
        if "pump_input_power_w" in updated:
            updated["pump_input_power_w"] = format_float(power_w, 0)
        if "pump_output_current_a" in updated:
            updated["pump_output_current_a"] = format_float(current_a, 2)
        if "pump_output_voltage_v" in updated:
            updated["pump_output_voltage_v"] = format_float(voltage_v, 1)
        if "pump_max_freq_hz" in updated:
            updated["pump_max_freq_hz"] = format_float(max_freq_hz, 1)

        if "pump_freq_pct" in updated:
            freq_pct = (freq_hz / max_freq_hz) * 100.0 if freq_hz is not None and max_freq_hz > 0.0 else None
            updated["pump_freq_pct"] = format_float(freq_pct, 1)
        if "pump_input_power_pct" in updated:
            input_power_pct = (
                (power_kw / PUMP_EST_RATED_INPUT_KW) * 100.0
                if power_kw is not None and PUMP_EST_RATED_INPUT_KW > 0.0
                else None
            )
            updated["pump_input_power_pct"] = format_float(input_power_pct, 1)
        if "pump_output_current_pct" in updated:
            current_pct = (
                (current_a / PUMP_NAMEPLATE_CURRENT_A) * 100.0
                if current_a is not None and PUMP_NAMEPLATE_CURRENT_A > 0.0
                else None
            )
            updated["pump_output_current_pct"] = format_float(current_pct, 1)
        if "pump_output_voltage_pct" in updated:
            voltage_pct = (
                (voltage_v / PUMP_BASE_VOLTAGE_V) * 100.0
                if voltage_v is not None and PUMP_BASE_VOLTAGE_V > 0.0
                else None
            )
            updated["pump_output_voltage_pct"] = format_float(voltage_pct, 1)

        output_rows.append(updated)

    summary = FileSummary(
        path=Path(),
        rows=len(rows),
        scaled_legacy_fields=not normalized,
        median_power_before_w=median(power_before) if power_before else None,
        median_power_after_w=median(power_after) if power_after else None,
        median_current_before_a=median(current_before) if current_before else None,
        median_current_after_a=median(current_after) if current_after else None,
        median_freq_hz=median(freqs) if freqs else None,
    )
    return output_rows, summary


def ensure_backup(path: Path) -> Path:
    backup_dir = path.parent / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / path.name
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
    return backup_path


def rewrite_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def process_file(path: Path, *, dry_run: bool, backup: bool) -> FileSummary:
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        original_fieldnames = list(reader.fieldnames or [])
        original_rows = list(reader)

    output_fieldnames = ensure_field_order(original_fieldnames)
    output_rows, summary = fix_rows(original_rows, original_fieldnames)
    summary = FileSummary(
        path=path,
        rows=summary.rows,
        scaled_legacy_fields=summary.scaled_legacy_fields,
        median_power_before_w=summary.median_power_before_w,
        median_power_after_w=summary.median_power_after_w,
        median_current_before_a=summary.median_current_before_a,
        median_current_after_a=summary.median_current_after_a,
        median_freq_hz=summary.median_freq_hz,
    )

    if not dry_run:
        if backup:
            ensure_backup(path)
        rewrite_csv(path, output_fieldnames, output_rows)

    return summary


def print_summary(summary: FileSummary) -> None:
    mode = "legacy rescale + backfill" if summary.scaled_legacy_fields else "backfill only"
    print(f"{summary.path}: {mode}")
    print(f"  rows: {summary.rows}")
    if summary.median_freq_hz is not None:
        print(f"  median pump_freq_hz: {summary.median_freq_hz:.2f}")
    if summary.median_power_before_w is not None and summary.median_power_after_w is not None:
        print(
            "  median pump_input_power_w: "
            f"{summary.median_power_before_w:.2f} -> {summary.median_power_after_w:.2f}"
        )
    if summary.median_current_before_a is not None and summary.median_current_after_a is not None:
        print(
            "  median pump_output_current_a: "
            f"{summary.median_current_before_a:.3f} -> {summary.median_current_after_a:.3f}"
        )


def main() -> int:
    args = parse_args()
    summaries: list[FileSummary] = []

    print("Legacy pump/VFD log normalization")
    print(f"  power scale:   {LEGACY_POWER_BASE_W:.1f} W -> {CORRECTED_POWER_BASE_W:.1f} W")
    print(f"  current scale: {LEGACY_CURRENT_BASE_A:.1f} A -> {CORRECTED_CURRENT_BASE_A:.1f} A")
    print(f"  rpm estimate:  {PUMP_EST_RPM_PER_HZ:.1f} rpm/Hz")
    print()

    for raw_path in args.paths:
        path = Path(raw_path)
        summary = process_file(path, dry_run=args.dry_run, backup=not args.no_backup)
        summaries.append(summary)
        print_summary(summary)
        print()

    if args.dry_run:
        print("Dry run only; no files were rewritten.")
    else:
        if args.no_backup:
            print("Files rewritten in place without backups.")
        else:
            print(f"Files rewritten in place. Backups live under `{BACKUP_DIR_NAME}/` next to each log.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
