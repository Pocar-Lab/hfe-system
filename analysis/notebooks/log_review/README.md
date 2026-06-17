# Log Review Calibration Notes

These notebooks review recirculation logs around the thermocouple firmware type
fix.

## Type-Fix Boundary

The firmware thermocouple type fix is treated as occurring at:

```text
2026-04-20 11:15:45
```

Logs before this time were recorded while installed Type T loop probes were
decoded as Type K. Logs after this time are treated as post-fix raw
thermocouple data.

## Affected Pre-Fix Notebooks

These notebooks use data affected by the legacy Type T decoded-as-Type K issue:

- `log_20260330_161922_review.ipynb`
- `log_20260402_150754_review.ipynb`
- `log_20260403_115916_review.ipynb`
- `log_20260417_094053_review.ipynb`

For these logs, `orca.prepare_flow_log_review(...)` applies:

- legacy K-to-T reconstruction for the Type T loop probes
- the active April 20 room + warmup-transfer calibration for the Type K HX
  channels `THM_C` and `THI_C`

The HX channels are handled separately because `THM` and `THI` are Type K
probes and were not part of the Type T decoded-as-Type K issue.

## Post-Fix Notebooks

These notebooks use post-fix data:

- `log_20260422_143345_review.ipynb`
- `log_20260424_153546_review.ipynb`

For post-fix raw logs, `orca.prepare_flow_log_review(...)` applies
`data/processed/calibration/TC_calibration_20260420.csv` in memory when a
calibration path or log metadata calibration file is available. Raw CSV files
remain unmodified.
