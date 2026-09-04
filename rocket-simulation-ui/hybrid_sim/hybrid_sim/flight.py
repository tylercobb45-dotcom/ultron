"""1-DOF trajectory: ISA atmosphere, ascent -> apogee -> drogue -> main -> ground."""
from __future__ import annotations
import math
import numpy as np
from scipy.integrate import solve_ivp
from .config import Rocket, G0, R_AIR, GAMMA_AIR, P_SL, T_SL, LAPSE, T_TROP, BARO_EXP

# U.S. Standard Atmosphere 1976 layers: (base geopotential altitude [m],
# base temperature [K], lapse rate dT/dh [K/m]).
_ISA_LAYERS = (
    (0.0,     288.15, -0.0065),
    (11000.0, 216.65,  0.0),
    (20000.0, 216.65,  0.001),
    (32000.0, 228.65,  0.0028),
    (47000.0, 270.65,  0.0),
    (51000.0, 270.65, -0.0028),
    (71000.0, 214.65, -0.002),
)
_R_EARTH = 6356766.0


def _isa_base_pressures():
    """Pressure at the bottom of each layer, integrated up from sea level."""
    out = [P_SL]
    for i in range(len(_ISA_LAYERS) - 1):
        h0, T0, a = _ISA_LAYERS[i]
        h1 = _ISA_LAYERS[i + 1][0]
        P0 = out[-1]
        if a == 0.0:
            out.append(P0*math.exp(-G0*(h1 - h0)/(R_AIR*T0)))
        else:
            out.append(P0*(1.0 + a*(h1 - h0)/T0)**(-G0/(a*R_AIR)))
    return tuple(out)


_ISA_BASE_P = _isa_base_pressures()


def isa(h):
    """Density, pressure and speed of sound at geometric altitude h [m].

    The previous version clamped temperature at the tropopause and then
    derived pressure from that temperature, which froze pressure and density
    above 11 km - by 20 km the density was 4x too high, and 20x at 30 km. Any
    high-altitude preview came back badly pessimistic. This walks the real
    layers instead.
    """
    z = max(0.0, h)
    # Geopotential altitude: what the ISA layer table is defined against.
    hg = _R_EARTH*z/(_R_EARTH + z)
    idx = 0
    for i, layer in enumerate(_ISA_LAYERS):
        if hg >= layer[0]:
            idx = i
        else:
            break
    h0, T0, a = _ISA_LAYERS[idx]
    P0 = _ISA_BASE_P[idx]
    dh = hg - h0
    if a == 0.0:
        T = T0
        P = P0*math.exp(-G0*dh/(R_AIR*T0))
    else:
        T = T0 + a*dh
        P = P0*(T/T0)**(-G0/(a*R_AIR))
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
