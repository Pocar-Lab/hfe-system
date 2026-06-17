# HX Performance Processed Data

This folder separates reduced HX products by provenance.

## Layout
- `reference/`: HFE-7200 reference curves used for interpretation, not HX temperature logs.
- `hx_test_configuration_notes.csv`: test-level configuration notes, including the LN inlet/outlet orientation correction.
- `derived_from_available_raw/timeseries/`: processed timeseries derived from raw logs that are present in this repo.
- `derived_from_available_raw/summaries/`: summary and energy tables derived from the available raw logs.
- `reconstructed_from_history/tables/`: numerical values scavenged from the historical executed notebook.
- `reconstructed_from_history/original_plots/`: embedded historical notebook PNG plots recovered from `analysis/docs/HX_performance_analysis.ipynb`.
- `reconstructed_from_history/extracted_text_outputs/`: plain-text/table outputs extracted from the same historical notebook.

The Oct 23/24 reconstructed raw-like CSVs remain in `data/raw/HX_performance/` for notebook compatibility. They are reconstructed evidence, not recovered original logger exports.

The Oct 21 cooling tests used the center straight tube as the LN inlet and the top of the coil as the outlet. The Oct 23/24 cooling tests used the reversed connection, with LN entering at the top of the coil and leaving through the center straight tube connected to the bottom of the coil.
