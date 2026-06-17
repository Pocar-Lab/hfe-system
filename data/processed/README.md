Processed analysis outputs live here, organized by analysis domain. Keep the
root of this directory limited to this README and put generated artifacts in
the matching subfolder.

## Subfolders
- `calibration/`: thermocouple calibration tables consumed by the supervisor,
  analysis helpers, and notebooks.
- `HX_performance/`: heat-exchanger reference data, reduced timeseries,
  summary tables, reconstructed-run validation targets, and recovered
  historical notebook plots.
- `leak_test/`: processed leak-analysis series used to recreate leak plots.
- `pump_performance/`: digitized vendor pump-performance reference data.

Raw logger files live in `data/raw/<type>/`.

## Thermocouple Tables
- `calibration/TC_calibration_20260420.csv` is the active table.
- `calibration/TC_calibration_20260410.csv` is retained as the flawed first
  pass from before the firmware thermocouple type fix.
- The active table is channel-first: U8 maps to `THM_C`, U9 maps to `THI_C`.
- Pre-fix logs are corrected in memory by `orca.logbook.apply_legacy_tc_correction`
  rather than by directly applying the April 20 affine coefficients to the
  wrong-type logged temperatures.
- The pre-fix HX channels are the exception: `THM_C` and `THI_C` were already
  Type K channels, so the active room + warmup-transfer HX rows are applied to
  those channels in the legacy path.
- Post-fix logs are treated as raw thermocouple data unless explicitly marked
  `tc_calibrated=true`; notebooks apply this table in memory and preserve
  `*_raw_C` columns.
