import numpy as np

def rolling_slope(t_s, y, window_s=45.0, min_pts=5):
    t = np.asarray(t_s); y = np.asarray(y)
    n=len(t); out=np.full(n, np.nan); half=window_s/2
    for i in range(n):
        t0,t1=t[i]-half, t[i]+half
        m=(t>=t0)&(t<=t1)
        if m.sum()>=min_pts:
            A = np.vstack([t[m], np.ones(m.sum())]).T
            slope,_ = np.linalg.lstsq(A, y[m], rcond=None)[0]
            out[i]=slope
    return out
