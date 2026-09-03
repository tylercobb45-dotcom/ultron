"""Burn metrics. burn_time uses the 5%-of-peak certification convention."""
from __future__ import annotations
import numpy as np
from .config import G0

def metrics(res: dict) -> dict:
    t, F = res["t"], res["thrust"]
    if len(t) < 2 or F.max() <= 0:
        return {k: 0.0 for k in ("total_impulse","peak_thrust","avg_thrust",
                                 "burn_time","peak_Pc","isp","avg_OF","prop_mass")}
    peak = F.max(); use = F > 0.05*peak
    tu = t[use]
    burn = float(tu[-1]-tu[0]) if tu.size else 0.0
    imp = float(np.trapezoid(F, t))
    prop = (res["m_l0"]+res["m_v0"]+res["m_f0"]-res["m_ox"][-1]-res["m_fuel"][-1])
    of = res["OF"]; ofv = of[(of>0)&use]
    return {"total_impulse": imp, "peak_thrust": float(peak),
            "avg_thrust": (float(np.trapezoid(F[use], t[use]))/burn if burn else 0.0),
            "burn_time": burn, "peak_Pc": float(res["Pc"].max()),
            "isp": imp/(prop*G0) if prop > 0 else 0.0,
            "avg_OF": float(ofv.mean()) if ofv.size else 0.0,
            "prop_mass": float(prop)}

def print_metrics(res: dict, name="Engine"):
    m = metrics(res)
    print(f"=== {name} ===")
    print(f"  Peak thrust   : {m['peak_thrust']:8.1f} N")
    print(f"  Avg thrust    : {m['avg_thrust']:8.1f} N")
    print(f"  Total impulse : {m['total_impulse']:8.1f} N*s")
    print(f"  Burn time     : {m['burn_time']:8.2f} s")
    print(f"  Peak Pc       : {m['peak_Pc']/1e6:8.3f} MPa ({m['peak_Pc']*0.000145038:.0f} psi)")
    print(f"  Isp           : {m['isp']:8.1f} s")
    print(f"  Avg O/F       : {m['avg_OF']:8.2f}")
    print(f"  Prop mass     : {m['prop_mass']:8.3f} kg")
