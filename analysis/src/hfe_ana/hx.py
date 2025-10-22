import numpy as np
from sklearn.linear_model import LinearRegression

def bath_capacity_j_per_k(volume_L, rho_kgL=1.07, cp_kJkgK=3.5):
    return volume_L*rho_kgL*cp_kJkgK*1000.0

def apparent_power(df, Cp_JK, window_s=45.0, slope_func=None):
    d = df.copy()
    s = slope_func(d["time_s"].to_numpy(), d["T_bulk_mean_C"].to_numpy(), window_s)
    d["dTbulk_dt_C_per_s"] = s
    d["P_bath_W"] = -Cp_JK * s
    return d

def fit_heat_leak_and_UA(df, tmin=(1.0,5.0), dT_range=(1.0,12.0)):
    d = df.copy()
    m = (d["t_min"]>tmin[0])&(d["t_min"]<tmin[1])&(d["DeltaT_C"]>dT_range[0])&(d["DeltaT_C"]<dT_range[1])&(~d["P_bath_W"].isna())
    X = d.loc[m,["DeltaT_C"]].to_numpy()
    y = d.loc[m,"P_bath_W"].to_numpy()
    reg = LinearRegression().fit(X, y)
    UA = float(reg.coef_[0])
    H  = float(-reg.intercept_)
    R2 = float(reg.score(X,y))
    return UA, H, R2

def apply_corrections(df, H_W):
    d = df.copy()
    d["P_HX_W"] = d["P_bath_W"] + H_W
    d["UA_corr_W_per_K"] = d["P_HX_W"]/d["DeltaT_C"]
    return d

def integrate_energy(t_s, power_W):
    return float(np.trapz(power_W, t_s))
