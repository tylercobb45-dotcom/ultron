"""1-DOF trajectory: ISA atmosphere, ascent -> apogee -> drogue -> main -> ground."""
from __future__ import annotations
import math
import numpy as np
from scipy.integrate import solve_ivp
from .config import Rocket, G0, R_AIR, GAMMA_AIR, P_SL, T_SL, LAPSE, T_TROP, BARO_EXP

def isa(h):
    h = max(0.0, h)
    T = max(T_TROP, T_SL - LAPSE*h)
    P = P_SL*(T/T_SL)**BARO_EXP
    return P/(R_AIR*T), P, math.sqrt(GAMMA_AIR*R_AIR*T)

class FlightModel:
    def __init__(self, rocket: Rocket, res: dict):
        self.r = rocket
        t, F = res["t"], res["thrust"]
        m_prop = (res["m_ox"]-res["m_ox"][-1]) + (res["m_fuel"]-res["m_fuel"][-1])
        self._t, self._F, self._mp, self.t_burn = t, F, m_prop, t[-1]

    def thrust(self, tt):
        return float(np.interp(tt, self._t, self._F, left=0.0, right=0.0))
    def prop_mass(self, tt):
        return 0.0 if tt >= self.t_burn else float(np.interp(tt, self._t, self._mp))

    def _rhs(self, t, y, phase):
        h, v = y
        rho, _, _ = isa(h)
        m = self.r.m_dry + self.prop_mass(t)
        F = self.thrust(t) if phase == "ascent" else 0.0
        if phase == "ascent":
            CdA = self.r.CdA_body
        else:
            CdA = self.r.CdA_main if h < self.r.h_main_ft/3.28084 else self.r.CdA_drogue
        drag = 0.5*rho*v*abs(v)*CdA
        return [v, (F - m*G0 - drag)/m]

    def run(self, n_out=1000):
        def apogee(t, y): return y[1] if y[0] > 10.0 else 1.0
        apogee.terminal, apogee.direction = True, -1
        asc = solve_ivp(lambda t,y: self._rhs(t,y,"ascent"), (0, self.t_burn+120),
                        [0.0, 1e-6], events=apogee, rtol=1e-7, atol=1e-9,
                        max_step=0.1, dense_output=True)
        t_ap, h_ap = asc.t[-1], asc.y[0,-1]

        def ground(t, y): return y[0]
        ground.terminal, ground.direction = True, -1
        dsc = solve_ivp(lambda t,y: self._rhs(t,y,"descent"), (t_ap, t_ap+600),
                        [h_ap, 0.0], events=ground, rtol=1e-6, atol=1e-8,
                        max_step=0.2, dense_output=True)

        tu = np.linspace(0, t_ap, n_out//2); td = np.linspace(t_ap, dsc.t[-1], n_out//2)
        H = np.concatenate([asc.sol(tu)[0], dsc.sol(td)[0]])
        V = np.concatenate([asc.sol(tu)[1], dsc.sol(td)[1]])
        t = np.concatenate([tu, td])
        snd = np.array([isa(h)[2] for h in H])
        accel = np.array([self._rhs(tt, [hh, vv], "ascent" if tt <= t_ap else "descent")[1]
                          for tt, hh, vv in zip(t, H, V)])
        F = np.array([self.thrust(tt) for tt in tu] + [0.0]*len(td))
        return {"t": t, "altitude": H, "velocity": V, "mach": np.abs(V)/snd,
                "accel": accel, "thrust": F,
                "apogee_m": float(h_ap), "apogee_ft": float(h_ap*3.28084),
                "t_apogee": float(t_ap), "v_max": float(V.max()),
                "mach_max": float((np.abs(V)/snd).max()),
                "g_max_ascent": float(accel[:len(tu)].max()/G0),
                "t_ground": float(dsc.t[-1])}
