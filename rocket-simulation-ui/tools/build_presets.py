#!/usr/bin/env python3
"""Generate the preset rockets that ship with JARVIS.

Each preset flies a real, measured thrust curve from thrustcurve.org (the
files in thrust_curves/csv, contributed by John Coker) rather than a modelled
motor, so a flight can be checked against something that was actually
measured on a test stand.

The airframes are representative of what each motor class is normally flown
in - they are not claimed to be any specific named vehicle - except for the
SystemsGo Goddard baseline, which is the configuration the vendored
hybrid_sim package is validated against and which has an independent
spreadsheet reference to compare with.

Run:  python tools/build_presets.py
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

PROFILES = os.path.join(SRC, "profiles")

# --- display factors, mirroring the tabs the values are typed into ---------
ENGINE_FACTORS = {
    "d_tank": 1000.0, "L_tank": 1000.0, "fill_frac": 100.0, "T_tank_0": 1.0,
    "n_holes": 1.0, "d_hole": 1000.0, "Cd_inj": 1.0, "L_grain": 1000.0,
    "d_grain_outer": 1000.0, "d_port_0": 1000.0, "d_throat": 1000.0,
    "eps_exp": 1.0, "alpha_deg": 1.0, "eta_cstar": 1.0, "eta_nozzle": 1.0,
    "gamma": 1.0, "MW": 1.0, "m_dry": 1.0, "Cd_body": 1.0, "d_body": 1000.0,
    # component fields added with the expanded engine model
    "d_vent": 1000.0, "Cd_vent": 1.0, "cooling_coeff": 1.0, "n_ports": 1.0,
    "L_pre": 1000.0, "L_post": 1000.0, "beta_conv_deg": 1.0,
    "erosion_rate": 1000.0, "fuel_a": 1.0, "fuel_n": 1.0, "Cd_inj": 1.0,
}
ENGINE_DECIMALS = {"d_hole": 3, "d_throat": 3, "fill_frac": 1, "n_holes": 0,
                   "n_ports": 0, "cooling_coeff": 4, "erosion_rate": 4,
                   "eta_cstar": 3, "eta_nozzle": 3, "Cd_inj": 3,
                   "d_tank": 2, "L_tank": 2, "fuel_a": 8, "fuel_n": 4}

AIRFRAME_FACTORS = {
    "nose_length_m": 1000.0, "body_diameter_m": 1000.0, "body_length_m": 1000.0,
    "surface_roughness_um": 1.0, "cd_override": 1.0,
    "boattail_length_m": 1000.0,
    "boattail_exit_diameter_m": 1000.0, "fin_count": 1.0,
    "fin_root_chord_m": 1000.0, "fin_tip_chord_m": 1000.0, "fin_span_m": 1000.0,
    "fin_sweep_m": 1000.0, "fin_thickness_m": 1000.0,
    "dry_mass_kg": 1.0, "propellant_mass_kg": 1.0,
    "dry_cg_m": 1000.0, "propellant_cg_m": 1000.0,
    "elevation_m": 1.0, "latitude_deg": 1.0, "temperature_c": 1.0,
    "pressure_pa": 0.01, "humidity_pct": 1.0, "wind_speed_ms": 1.0,
    "wind_ref_height_m": 1.0, "wind_shear_exp": 1.0,
    "rail_length_m": 1.0, "rail_angle_deg": 1.0,
}
VEHICLE_FACTORS = {
    "body_od_m": 1000.0, "body_wall_m": 1000.0, "body_length_m": 1.0,
    "fin_count": 1.0, "fin_root_chord_m": 1000.0, "fin_tip_chord_m": 1000.0,
    "fin_span_m": 1000.0, "fin_thickness_m": 1000.0,
    "chamber_wall_m": 1000.0, "tank_wall_m": 1000.0,
    "target_altitude_ft": 1.0, "rail_length_m": 1.0, "harness_rating_n": 1.0,
    "min_pressure_sf": 1.0, "min_structure_sf": 1.0,
}


def _fmt(value, factor, decimals=None):
    shown = value * factor
    if decimals is None:
        decimals = 0 if abs(shown) >= 100 else (2 if abs(shown) < 10 else 1)
    return f"{shown:.{decimals}f}"


def engine_section(spec):
    from engine_lab import _PRESETS
    base = dict(_PRESETS["Goddard baseline"])
    base.pop("rocket", None)
    base.update(spec)
    fuel = base.pop("fuel", "HTPB")
    out = {}
    for key, factor in ENGINE_FACTORS.items():
        if key in base:
            out[key] = _fmt(base[key], factor, ENGINE_DECIMALS.get(key))
    out["fuel"] = fuel
    return out


def airframe_section(spec, recovery):
    import aero
    import flight_model
    import atmosphere
    defaults = {}
    defaults.update({k: getattr(aero.Airframe(), k, None) for k in AIRFRAME_FACTORS})
    for k in AIRFRAME_FACTORS:
        if defaults.get(k) is None:
            defaults[k] = getattr(flight_model.MassProperties(), k, None)
        if defaults.get(k) is None:
            defaults[k] = getattr(atmosphere.LaunchSite(), k, 0.0)
    defaults.update(spec)
    fields = {}
    for key, factor in AIRFRAME_FACTORS.items():
        decimals = {"fin_count": 0, "cd_override": 3, "wind_shear_exp": 3}.get(key)
        fields[key] = _fmt(float(defaults[key]), factor, decimals)
    return {
        "fields": fields,
        "nose_shape": spec.get("nose_shape", "Tangent Ogive"),
        "fin_profile": spec.get("fin_profile", "Rounded leading"),
        "recovery": recovery,
    }


def vehicle_section(spec):
    import failure_analysis as fa
    cfg = fa.VehicleConfig()
    out = {}
    for key, factor in VEHICLE_FACTORS.items():
        value = spec.get(key, getattr(cfg, key))
        out[key] = _fmt(float(value), factor, 0 if key in ("fin_count", "target_altitude_ft",
                                                           "harness_rating_n") else None)
    for key in ("airframe_material", "nose_material", "fin_material",
                "nozzle_material", "chamber_material", "tank_material"):
        out[key] = spec.get(key, getattr(cfg, key))
    return out


def dual_deploy(drogue_d, main_d, main_alt, main_cd=1.5, drogue_cd=1.5):
    import recovery as rec
    system = rec.RecoverySystem.dual_deploy(
        drogue_d=drogue_d, drogue_cd=drogue_cd, main_d=main_d,
        main_cd=main_cd, main_altitude_m=main_alt)
    return system.to_dict()


def single_deploy(main_d, main_cd=1.5):
    import recovery as rec
    return rec.RecoverySystem.single_deploy(diameter_m=main_d, cd=main_cd).to_dict()


def resolve_recovery(preset):
    """Turn the compact recovery_spec into a full recovery-system dict."""
    spec = preset.get("recovery_spec") or {}
    kind = spec.get("kind", "dual")
    if kind == "single":
        return single_deploy(spec.get("main_d", 2.0), spec.get("main_cd", 1.5))
    return dual_deploy(spec.get("drogue_d", 0.9), spec.get("main_d", 2.6),
                       spec.get("main_alt", 300), spec.get("main_cd", 1.5),
                       spec.get("drogue_cd", 1.5))


def build(preset):
    dry = preset["airframe"]["dry_mass_kg"]
    prop = preset["airframe"]["propellant_mass_kg"]
    diameter = preset["airframe"]["body_diameter_m"]
    area = 3.141592653589793 * (diameter / 2.0) ** 2
    return {
        "version": "2.0",
        "name": preset["name"],
        "description": preset["description"],
        "created": time.strftime("%a %b %d %H:%M:%S %Y"),
        "reference": preset.get("reference", ""),
        "rocket_parameters": {
            "mass": f"{dry + prop:.3f}", "mass_unit": 0,
            "prop_mass": f"{prop:.3f}", "prop_mass_unit": 0,
            "cd": f"{preset.get('cd', 0.55):.2f}",
            "area": f"{area:.6f}", "area_unit": 0,
            "rho": "1.225", "rho_unit": 0,
            "timestep": "0.05", "timestep_unit": 0,
            "fin_count": str(int(preset["airframe"].get("fin_count", 4))),
            "fin_thickness": f"{preset['airframe'].get('fin_thickness_m', 0.005) * 1000:.1f}",
            "fin_thickness_unit": 1,
            "fin_length": f"{preset['airframe'].get('fin_root_chord_m', 0.3) * 1000:.0f}",
            "fin_length_unit": 1,
            "body_diameter": f"{diameter:.4f}", "body_diameter_unit": 0,
            "chute_height": f"{preset.get('main_alt', 300):.0f}", "chute_height_unit": 0,
            "chute_size": f"{preset.get('main_area', 2.0):.2f}", "chute_size_unit": 0,
            "chute_cd": "1.50",
        },
        "launch_conditions": {
            "start_altitude": f"{preset['airframe'].get('elevation_m', 0):.0f}",
            "temperature": f"{preset['airframe'].get('temperature_c', 15):.1f}",
            "humidity": f"{preset['airframe'].get('humidity_pct', 0):.0f}",
        },
        "stability_settings": {"rocket_length": 1.0, "center_of_mass": 0.5,
                               "center_of_pressure": 0.7, "launch_angle": 0.0},
        "wind_settings": {"wind_speed": preset["airframe"].get("wind_speed_ms", 0.0),
                          "wind_direction": 0},
        "thrust_curve_path": preset["thrust_curve"],
        "engine": engine_section(preset["engine"]),
        "airframe": airframe_section(preset["airframe"], resolve_recovery(preset)),
        "vehicle": vehicle_section(preset.get("vehicle", {})),
    }


def main():
    from presets import PRESET_ROCKETS
    os.makedirs(PROFILES, exist_ok=True)
    for preset in PRESET_ROCKETS:
        profile = build(preset)
        path = os.path.join(PROFILES, f"{preset['name']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
        print(f"wrote {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
