from pathlib import Path
import pandas as pd

TC_MAP = {"temp1_C":"U1_bottom_C","temp2_C":"U2_coilTop_C",
          "temp3_C":"U3_top_C","temp4_C":"U4_coilMid_C"}

def load_tc_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path).rename(columns=TC_MAP)
    df["t_min"] = df["time_s"]/60.0
    df["T_bulk_mean_C"] = df[["U1_bottom_C","U3_top_C"]].mean(axis=1)
    df["T_coil_mean_C"] = df[["U2_coilTop_C","U4_coilMid_C"]].mean(axis=1)
    df["DeltaT_C"] = df["T_bulk_mean_C"] - df["T_coil_mean_C"]
    df["Strat_top_minus_bottom_C"] = df["U3_top_C"] - df["U1_bottom_C"]
    return df
