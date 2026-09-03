#!/usr/bin/env python3
"""Demo: python run.py [--validate] [--no-plots]. Edit BASELINE for your motor."""
import argparse
from hybrid_sim import Engine, Rocket, EngineModel, FlightModel, FUELS, print_metrics
from hybrid_sim.validation import validate
from hybrid_sim.plotting import plot_engine, plot_flight

BASELINE = Engine(
    d_tank=0.100, L_tank=1.019, fill_frac=0.85, T_tank_0=293,
    n_holes=4, d_hole=0.00252, Cd_inj=0.7,
    fuel=FUELS["HTPB"], L_grain=0.30, d_grain_outer=0.076, d_port_0=0.036,
    d_throat=0.018, eps_exp=5.0, eta_cstar=0.90, eta_nozzle=0.95, gamma=1.22, MW=26)
ROCKET = Rocket(m_dry=20.0, Cd_body=1.625, d_body=0.14)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--no-plots", action="store_true")
    a = ap.parse_args()
    if a.validate:
        validate(); return
    res = EngineModel(BASELINE).run()
    print_metrics(res, "Goddard baseline engine")
    fl = FlightModel(ROCKET, res).run()
    print("\n=== Flight ===")
    print(f"  Apogee        : {fl['apogee_ft']:8.0f} ft ({fl['apogee_m']:.0f} m)")
    print(f"  Time to apogee: {fl['t_apogee']:8.1f} s")
    print(f"  Max velocity  : {fl['v_max']:8.1f} m/s   Max Mach: {fl['mach_max']:.3f}")
    print(f"  Max G (ascent): {fl['g_max_ascent']:8.1f}")
    if not a.no_plots:
        print("\nPlots:", plot_engine(res, "engine.png", "Goddard baseline engine"),
              plot_flight(fl, "flight.png", "Goddard baseline trajectory"))

if __name__ == "__main__":
    main()
