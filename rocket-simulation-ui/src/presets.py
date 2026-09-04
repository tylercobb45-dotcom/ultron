"""Preset engines and rockets that ship with JARVIS.

Provenance matters here, so it is stated per item.

ENGINES
    The four HyperTEK entries are Engine Lab configurations *fitted* to
    reproduce published performance of real motors - total impulse, burn
    time, peak thrust and propellant mass. HyperTEK does not publish the
    internal geometry of its motors, so these are not the manufacturer's
    dimensions; they are a set of parameters that makes this model behave
    like the real motor did on a test stand. Reference data:
      I260  - certification figures carried in hybrid_sim/validation.py
      J317, K240, L550 - measured from the thrustcurve.org curves that ship
      in thrust_curves/csv (contributed by John Coker), whose headers also
      carry the propellant masses.
    The Goddard baseline is the SystemsGo configuration the vendored
    hybrid_sim package is validated against, with an independent spreadsheet
    reference for the whole flight.

ROCKETS
    Airframes are representative of what each motor class is normally flown
    in. They are not claimed to be specific named vehicles - inventing
    dimensions for a real team's rocket would make the validation
    meaningless. The Goddard preset is the exception: it is the configuration
    behind the spreadsheet reference, so its flight can be checked end to end.

    Every preset except Goddard flies a real measured thrust curve rather
    than a modelled one, so a flight comparison tests the flight model
    against a motor that actually existed.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Engine Lab configurations fitted to published motor performance.
# Produced by tools/fit_engines.py; see that script for the fit residuals.
# ---------------------------------------------------------------------------
ENGINE_FITS = {
    # Fitted by tools/fit_engines.py against the published performance below.
    # Peak chamber pressure is constrained to the 2.5-4.5 MPa band real hybrids
    # run at: a thrust curve alone does not pin down internal geometry, and an
    # unconstrained fit reaches the right thrust with a 20 mm throat at 1 MPa,
    # which would make the report's chamber-pressure safety check meaningless.
    "HyperTEK I260": dict(
        d_tank=0.0500, L_tank=0.2382, d_hole=0.00276, d_throat=0.01298,
        L_grain=0.1862, d_grain_outer=0.0500, d_port_0=0.0440,
        eps_exp=3.50, eta_cstar=0.825, eta_nozzle=0.825, fill_frac=0.950),
    "HyperTEK J317": dict(
        d_tank=0.0580, L_tank=0.5800, d_hole=0.00276, d_throat=0.01336,
        L_grain=0.4971, d_grain_outer=0.0500, d_port_0=0.0440,
        eps_exp=3.50, eta_cstar=0.860, eta_nozzle=0.860, fill_frac=0.600),
    "HyperTEK K240": dict(
        d_tank=0.0580, L_tank=0.3406, d_hole=0.00222, d_throat=0.01097,
        L_grain=0.3050, d_grain_outer=0.0500, d_port_0=0.0410,
        eps_exp=3.50, eta_cstar=0.935, eta_nozzle=0.935, fill_frac=0.950),
    "HyperTEK L550": dict(
        d_tank=0.0700, L_tank=0.4558, d_hole=0.00335, d_throat=0.01767,
        L_grain=0.3599, d_grain_outer=0.0620, d_port_0=0.0480,
        eps_exp=3.50, eta_cstar=0.959, eta_nozzle=0.959, fill_frac=0.950),
}

# Published reference performance, for the validation script.
ENGINE_REFERENCE = {
    "HyperTEK I260": dict(impulse=570,  burn=2.20, peak=374, prop=0.383,
                          source="HyperTEK certification data (hybrid_sim/validation.py)"),
    "HyperTEK J317": dict(impulse=997,  burn=3.17, peak=471, prop=0.712,
                          source="thrustcurve.org Hypertek_835CC172J-J317.csv"),
    "HyperTEK K240": dict(impulse=1423, burn=6.09, peak=338, prop=0.789,
                          source="thrustcurve.org Hypertek_835CC125J-K240.csv"),
    "HyperTEK L550": dict(impulse=2999, burn=5.53, peak=817, prop=1.552,
                          source="thrustcurve.org Hypertek_1685CCRGL-L550.csv"),
}

_CURVES = "thrust_curves/csv"

# Materials used across the presets
_GLASS = dict(airframe_material="Fiberglass G10/FR4",
              nose_material="Fiberglass G10/FR4",
              fin_material="Fiberglass G10/FR4",
              nozzle_material="Graphite",
              chamber_material="Aluminum 6061-T6",
              tank_material="Aluminum 6061-T6")
_CARBON = dict(_GLASS, airframe_material="Carbon Fiber / Epoxy",
               nose_material="Carbon Fiber / Epoxy",
               fin_material="Carbon Fiber / Epoxy")


PRESET_ROCKETS = [
    # -- 1. small subsonic --------------------------------------------------
    dict(
        name="HyperTEK J317 Sport",
        description=("98 mm sport airframe on a real J317 hybrid. Subsonic, "
                     "low altitude, high thrust-to-weight - the easy case."),
        reference="Motor: thrustcurve.org Hypertek_835CC172J-J317 (measured)",
        thrust_curve=f"{_CURVES}/Hypertek_835CC172J-J317.csv",
        cd=0.55, main_alt=250, main_area=1.8,
        engine=ENGINE_FITS["HyperTEK J317"],
        airframe=dict(
            nose_shape="Tangent Ogive", nose_length_m=0.40,
            body_diameter_m=0.098, body_length_m=1.40,
            surface_roughness_um=20.0, boattail_length_m=0.0,
            boattail_exit_diameter_m=0.0,
            fin_count=3, fin_root_chord_m=0.22, fin_tip_chord_m=0.10,
            fin_span_m=0.11, fin_sweep_m=0.11, fin_thickness_m=0.004,
            dry_mass_kg=5.50, propellant_mass_kg=0.712,
            dry_cg_m=1.05, propellant_cg_m=1.55,
            elevation_m=200.0, temperature_c=18.0, wind_speed_ms=3.0,
            rail_length_m=3.05, rail_angle_deg=0.0),
        recovery=None,      # filled by the builder
        recovery_spec=dict(kind="single", main_d=1.5),
        vehicle=dict(_GLASS, body_od_m=0.098, body_wall_m=0.0025,
                     body_length_m=1.80, fin_count=3,
                     fin_root_chord_m=0.22, fin_tip_chord_m=0.10,
                     fin_span_m=0.11, fin_thickness_m=0.004,
                     target_altitude_ft=3000, rail_length_m=3.05,
                     harness_rating_n=2500),
    ),
    # -- 2. subsonic, longer burn, higher --------------------------------------
    dict(
        name="HyperTEK K240 Altitude",
        description=("98 mm airframe on a real K240. Long 6 s burn, modest "
                     "thrust-to-weight, still comfortably subsonic."),
        reference="Motor: thrustcurve.org Hypertek_835CC125J-K240 (measured)",
        thrust_curve=f"{_CURVES}/Hypertek_835CC125J-K240.csv",
        cd=0.50, main_alt=300, main_area=2.0,
        engine=ENGINE_FITS["HyperTEK K240"],
        airframe=dict(
            nose_shape="Von Karman (LV-Haack)", nose_length_m=0.45,
            body_diameter_m=0.098, body_length_m=1.60,
            surface_roughness_um=15.0, boattail_length_m=0.0,
            boattail_exit_diameter_m=0.0,
            fin_count=4, fin_root_chord_m=0.20, fin_tip_chord_m=0.09,
            fin_span_m=0.10, fin_sweep_m=0.10, fin_thickness_m=0.004,
            dry_mass_kg=5.00, propellant_mass_kg=0.789,
            dry_cg_m=1.15, propellant_cg_m=1.72,
            elevation_m=200.0, temperature_c=18.0, wind_speed_ms=4.0,
            rail_length_m=3.05, rail_angle_deg=0.0),
        recovery=None,
        recovery_spec=dict(kind="dual", drogue_d=0.45, main_d=1.8, main_alt=300),
        vehicle=dict(_GLASS, body_od_m=0.098, body_wall_m=0.0025,
                     body_length_m=2.05, fin_count=4,
                     fin_root_chord_m=0.20, fin_tip_chord_m=0.09,
                     fin_span_m=0.10, fin_thickness_m=0.004,
                     target_altitude_ft=5000, rail_length_m=3.05,
                     harness_rating_n=2500),
    ),
    # -- 3. the validated reference case ---------------------------------------
    dict(
        name="SystemsGo Goddard Baseline",
        description=("The configuration hybrid_sim is validated against, with "
                     "an independent spreadsheet reference for the whole "
                     "flight: 9,292 ft, Mach 0.57. Transonic-adjacent."),
        reference=("Engine and flight: hybrid_sim excel_ref.json "
                   "(independent spreadsheet model)"),
        thrust_curve=f"{_CURVES}/SystemsGo_Goddard_baseline.csv",
        cd=0.60, main_alt=300, main_area=2.5,
        engine=dict(d_tank=0.100, L_tank=1.019, d_hole=0.00252, d_throat=0.018,
                    L_grain=0.30, d_grain_outer=0.076, d_port_0=0.036,
                    eps_exp=5.0, eta_cstar=0.90, eta_nozzle=0.95, n_holes=4),
        airframe=dict(
            nose_shape="Tangent Ogive", nose_length_m=0.60,
            body_diameter_m=0.140, body_length_m=2.50,
            surface_roughness_um=25.0, boattail_length_m=0.0,
            boattail_exit_diameter_m=0.0,
            fin_count=4, fin_root_chord_m=0.30, fin_tip_chord_m=0.15,
            fin_span_m=0.15, fin_sweep_m=0.10, fin_thickness_m=0.005,
            dry_mass_kg=20.00, propellant_mass_kg=6.049,
            dry_cg_m=1.85, propellant_cg_m=2.55,
            elevation_m=0.0, temperature_c=15.0, wind_speed_ms=0.0,
            rail_length_m=5.18, rail_angle_deg=0.0,
            # The reference flight was computed with a measured Cd of 1.625,
            # far above what this airframe's shape would suggest (~0.52). The
            # real vehicle's drag is what it is, so the preset uses the
            # reference's own number and reproduces its documented apogee.
            cd_override=1.625),
        recovery=None,
        recovery_spec=dict(kind="dual", drogue_d=0.892, main_d=2.61, main_alt=305),
        vehicle=dict(_GLASS, body_od_m=0.140, body_wall_m=0.003,
                     body_length_m=3.10, fin_count=4,
                     fin_root_chord_m=0.30, fin_tip_chord_m=0.15,
                     fin_span_m=0.15, fin_thickness_m=0.005,
                     target_altitude_ft=9292, rail_length_m=5.18,
                     harness_rating_n=4000),
    ),
    # -- 4. supersonic ----------------------------------------------------------
    dict(
        name="HyperTEK L550 Supersonic",
        description=("76 mm minimum-diameter carbon airframe on a real L550. "
                     "Thrust-to-weight near 14, goes supersonic on the way up."),
        reference="Motor: thrustcurve.org Hypertek_1685CCRGL-L550 (measured)",
        thrust_curve=f"{_CURVES}/Hypertek_1685CCRGL-L550.csv",
        cd=0.45, main_alt=250, main_area=1.6,
        engine=ENGINE_FITS["HyperTEK L550"],
        airframe=dict(
            nose_shape="Von Karman (LV-Haack)", nose_length_m=0.42,
            body_diameter_m=0.076, body_length_m=1.45,
            surface_roughness_um=10.0, boattail_length_m=0.0,
            boattail_exit_diameter_m=0.0,
            fin_count=3, fin_root_chord_m=0.18, fin_tip_chord_m=0.07,
            fin_span_m=0.075, fin_sweep_m=0.12, fin_thickness_m=0.004,
            dry_mass_kg=4.30, propellant_mass_kg=1.552,
            dry_cg_m=1.05, propellant_cg_m=1.50,
            elevation_m=600.0, temperature_c=22.0, wind_speed_ms=4.0,
            rail_length_m=3.66, rail_angle_deg=0.0),
        recovery=None,
        recovery_spec=dict(kind="dual", drogue_d=0.40, main_d=1.5, main_alt=250),
        vehicle=dict(_CARBON, body_od_m=0.076, body_wall_m=0.002,
                     body_length_m=1.87, fin_count=3,
                     fin_root_chord_m=0.18, fin_tip_chord_m=0.07,
                     fin_span_m=0.075, fin_thickness_m=0.004,
                     target_altitude_ft=12000, rail_length_m=3.66,
                     harness_rating_n=3000),
    ),
]
