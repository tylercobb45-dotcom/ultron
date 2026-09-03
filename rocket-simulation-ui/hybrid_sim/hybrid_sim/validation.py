"""Validation vs published HyperTEK certification data (thrustcurve.org)."""
from __future__ import annotations
from dataclasses import dataclass
from .config import Engine, SimConfig, FUELS
from .engine import EngineModel
from .metrics import metrics

@dataclass
class MotorRef:
    name: str; engine: Engine
    peak: float; avg: float; impulse: float; burn: float; isp: float; prop: float

MOTORS = {
 "I260": MotorRef('HyperTEK I260 (440cc, 0.172" orifice)',
    Engine(d_tank=0.050, L_tank=0.224, fill_frac=0.85, T_tank_0=293,
           n_holes=1, d_hole=0.00437, Cd_inj=0.7, fuel=FUELS["HTPB"],
           L_grain=0.20, d_grain_outer=0.050, d_port_0=0.044,
           d_throat=0.0092, eps_exp=3.5, eta_cstar=0.85, eta_nozzle=0.85),
    374, 260, 570, 2.2, 152, 0.383),
 "K240": MotorRef('HyperTEK K240 (835cc, 0.125" orifice)',
    Engine(d_tank=0.058, L_tank=0.425, fill_frac=0.80, T_tank_0=293,
           n_holes=1, d_hole=0.003175, Cd_inj=0.7, fuel=FUELS["HTPB"],
           L_grain=0.20, d_grain_outer=0.050, d_port_0=0.041,
           d_throat=0.008, eps_exp=3.5, eta_cstar=0.85, eta_nozzle=0.85),
    283, 240, 1292, 5.4, 165, 0.799),
}

def validate(verbose=True):
    rows = []
    for key, ref in MOTORS.items():
        m = metrics(EngineModel(ref.engine).run())
        sim = dict(peak=m["peak_thrust"], avg=m["avg_thrust"],
                   impulse=m["total_impulse"], burn=m["burn_time"], isp=m["isp"])
        pub = dict(peak=ref.peak, avg=ref.avg, impulse=ref.impulse,
                   burn=ref.burn, isp=ref.isp)
        rows.append((key, pub, sim))
        if verbose:
            print(f"\n{ref.name}")
            print(f"  {'metric':10}{'published':>11}{'sim':>10}{'error':>8}")
            for f in ("peak","avg","impulse","burn","isp"):
                e = (sim[f]-pub[f])/pub[f]*100
                print(f"  {f:10}{pub[f]:>11.0f}{sim[f]:>10.0f}{e:>7.0f}%")
    return rows

if __name__ == "__main__":
    validate()
