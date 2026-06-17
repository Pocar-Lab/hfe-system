# Reconstructed HX Raw-Like Logs

The four Oct 23/24 HX raw logger exports in this directory are reconstructed
from executed notebook outputs, embedded plots, and displayed summary tables in
`analysis/docs/HX_performance_analysis.ipynb`. They are not recovered original
measurements.

The temperature channels are digitized from the historical embedded PNG plots
using deterministic color masks and manual tick-axis calibration. Small affine
corrections are applied only to preserve the displayed historical fit power,
fit-window DeltaT, and warmup heat leak constraints.

Generated files:

- `log_20251023_140530-mixed.csv`
- `log_20251023_142833-mixed_auto.csv`
- `log_20251023_154857-warmup2.csv`
- `log_20251024_142037_mixed60psi.csv`

The reconstructed columns follow the raw logger layout expected by the HX
analysis notebook:

`time_s,temp0_C,temp1_C,temp2_C,temp3_C,temp4_C,temp5_C,temp6_C,temp7_C,THM_C,THI_C,valve,mode`

Channel mapping used for reconstruction:

- `temp1_C`: bath bottom
- `temp3_C`: bath top
- `temp2_C`: coil top
- `temp4_C`: coil mid
- unused channels: `nan`

Historical constraints used:

- `Cp = 16660 J/K`
- heat leak `= 14.895 W`
- HX area `= 0.12 m2`
- mixed: start `1525.6 s`, duration `21.958833 min`
- mixed auto: start `2906.5 s`, duration `12.377633 min`
- warmup: start `7722.399 s`, heat leak `14.89 W`
- mixed 60 psi: start `974.6 s`, duration `10.532717 min`

Configuration context:

- Oct 21 cooling tests used the center straight tube as the LN inlet and the top
  of the coil as the outlet.
- The reconstructed Oct 23/24 cooling tests use the switched orientation: LN
  inlet at the top of the coil and outlet through the center straight tube
  connected to the bottom of the coil.
- The switch was made to inspect changes in antifreeze freezing behavior, so the
  Oct 21 mixed and Oct 23 mixed runs should be treated as opposite
  flow-direction tests rather than strict repeats.

See `hx_reconstruction_manifest.csv` for the retained provenance and validation
metrics from the reconstruction work. The reconstruction and comparison helper
code is intentionally not kept in the repository; these CSVs are frozen
raw-like data products for exploratory/dissertation use only. The Oct 21 logs
remain the true raw HX measurements currently present in this repository.
