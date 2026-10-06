"""Preset engines and rockets that ship with JARVIS.

Provenance matters here, so it is stated per item, and the line between
"published by the manufacturer" and "fitted by us" is drawn explicitly.

ENGINES
    The four HyperTEK entries are Engine Lab configurations built around the
    manufacturer's own published hardware. Tank volume, tank internal
    diameter and length, injector orifice diameter, orifice count, case
    diameter and overall motor length are LOCKED to published values (see
    ENGINE_HARDWARE below for each number and where it comes from). Only the
    internals HyperTEK does not publish were fitted - injector discharge
    coefficient, tank cooling, fuel regression coefficient, throat, expansion
    ratio, grain port and efficiencies.

    Reference performance:
      I260  - certification figures carried in hybrid_sim/validation.py
      J317, K240, L550 - measured from the thrustcurve.org curves that ship
      in thrust_curves/csv (contributed by John Coker), whose headers also
      carry the propellant masses.

    The published numbers cross-check each other before any modelling
    happens: the J317 and K240 are the same 835 cc motor differing only in
    orifice (.172 in vs .125 in), and their measured burn times scale as the
    inverse injector-area ratio to within 1.5%. tools/validate_presets.py
    checks that, and the tank/pressure consistency, as section 0.

ROCKETS
    Airframes are representative of what each motor class is normally flown
    in. They are not claimed to be specific named vehicles - inventing
    dimensions for a real team's rocket would make the validation
    meaningless. Two presets are modelled rather than measured, and both say
    so: "hybrid_sim Reference Flight" is the configuration behind the
    spreadsheet reference, so its flight can be checked end to end, and
    "SystemsGo Goddard Baseline" is a motor sized here to the Goddard brief
    (50,000 ft with a payload) because no measured curve of that class ships
    with the app.

    Every other preset flies a real measured thrust curve rather than a
    modelled one, so a flight comparison tests the flight model against a
    motor that actually existed.

See docs/VALIDATION.md for the residuals and the disagreements.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Published HyperTEK hardware.
#
# These are NOT fitted. Each number below is either stated by the vendor or
# encoded in the motor designation itself, and the fitter treats them as fixed
# constraints - only the genuinely unpublished internals are free to move.
#
# How the designation works:  440CC172J-I260
#                             ^^^        tank volume in cc
#                                ^^^     injector orifice, thousandths of an inch
#                                   ^    fuel grain type
#                                     ^^^^ impulse class and average thrust
#
# The injector is a bell holding ONE field-interchangeable orifice insert (the
# kit ships five sizes; swapping the insert is what turns an 835 cc tank from a
# J317 into a K240). So n_holes = 1, and the orifice diameter is published to
# the thousandth of an inch.
#
# Tank internal diameter: the 835 cc 54 mm tank takes a 17.5 in vent tube that
# runs its length, so L = 0.4445 m; 835 cc at that length gives ID 48.9 mm,
# which is exactly right for a 54 mm tube with a ~2.5 mm wall. The same ID is
# used for the 440 cc tank (same 54 mm system), and the 75 mm L tank is scaled
# by the same OD/ID ratio.
#
# Sources: thrustcurve.org motor designations; HyperTEK product listings and
# vendor descriptions (Sunward Rockets, jcrocket.com HyperTEK system page);
# HyperTEK manual introduction. See docs/VALIDATION.md.
# ---------------------------------------------------------------------------
IN = 0.0254

ENGINE_HARDWARE = {
    "HyperTEK I260": dict(
        designation="440CC172J-I260", tank_cc=440.0, case_od=0.054,
        tank_id=0.04890, orifice_in=0.172, n_holes=1, motor_length=0.533,
        note="54 mm system, small tank. Vendor states ~21 in overall length."),
    "HyperTEK J317": dict(
        designation="835CC172J-J317", tank_cc=835.0, case_od=0.054,
        tank_id=0.04890, orifice_in=0.172, n_holes=1, motor_length=0.762,
        note="54 mm system, large tank. Vendor states 30 in overall length; "
             "same .172 orifice as the I260, bigger tank."),
    "HyperTEK K240": dict(
        designation="835CC125J-K240", tank_cc=835.0, case_od=0.054,
        tank_id=0.04890, orifice_in=0.125, n_holes=1, motor_length=0.762,
        note="Same 835 cc hardware as the J317 with the smaller .125 orifice: "
             "less flow, longer burn, K impulse instead of J."),
    "HyperTEK L550": dict(
        designation="1685CCRGL-L550", tank_cc=1685.0, case_od=0.075,
        tank_id=0.06792, orifice_in=None, n_holes=1, motor_length=None,
        note="75 mm system. 'RGL' is not a numbered orifice, so the injector "
             "area is fitted rather than published."),
}

# Vendor-stated operating pressures, used as fit constraints and as a check
# that the model is running the motor the way the motor actually runs.
#   "the nitrous oxide is self-pressurized to between 650 and 750 psi, which
#    allows the motor to operate at initial chamber pressures of up to about
#    550 psi"
N2O_TANK_PSI = (650.0, 750.0)      # 4.48 - 5.17 MPa
PC_MAX_PSI = 550.0                 # 3.79 MPa
PSI = 6894.757


def hardware_geometry(name):
    """Published tank geometry and injector for a motor, in SI.

    Returns the subset of Engine fields that are fixed by published data.
    """
    hw = ENGINE_HARDWARE.get(name)
    if not hw:
        return {}
    d = hw["tank_id"]
    out = dict(d_tank=d,
               L_tank=(hw["tank_cc"] * 1e-6) / (math.pi * (d / 2.0) ** 2),
               n_holes=hw["n_holes"])
    if hw.get("orifice_in"):
        out["d_hole"] = hw["orifice_in"] * IN
    return out


# ---------------------------------------------------------------------------
# Engine Lab configurations fitted to published motor performance.
# Produced by tools/fit_engines.py; see that script for the fit residuals.
# ---------------------------------------------------------------------------
ENGINE_FITS = {
    # Produced by tools/fit_engines.py. Everything HyperTEK publishes is LOCKED
    # here and was not adjusted to improve the match:
    #
    #   d_tank / L_tank   the tank volume in the designation (440/835/1685 cc),
    #                     with the internal diameter derived from the 17.5 in
    #                     vent tube that runs the length of the 835 cc tank
    #   d_hole, n_holes   the orifice in the designation (.172 / .125 inch),
    #                     one field-interchangeable insert
    #   d_grain_outer     bounded by the 54 mm / 75 mm case it ships in
    #   L_grain           bounded so tank + grain fits the vendor's stated
    #                     overall motor length
    #
    # What is fitted is the physics HyperTEK does NOT publish:
    #
    #   Cd_inj            how well the orifice actually flows. Not the textbook
    #                     0.7 of a water orifice: N2O flashes to vapour across
    #                     the hole and the two-phase choking that follows cuts
    #                     the effective discharge coefficient a long way.
    #   cooling_coeff     how much boil-off latent heat comes out of the liquid
    #                     rather than the tank walls, which sets how steeply
    #                     the blowdown decays
    #   fuel_a            regression coefficient of HyperTEK's proprietary
    #                     moulded thermoplastic grain, over an ABS base
    #   d_throat, eps_exp, grain port, eta_cstar, fill_frac
    #
    # Chamber pressure lands at 435-536 psi against the vendor's stated "up to
    # about 550 psi", and tank pressure at 730 psi against their stated
    # 650-750 psi - neither was targeted directly.
    "HyperTEK I260": dict(
        d_tank=0.04890, L_tank=0.23429, d_hole=0.00437, n_holes=1,
        d_throat=0.01023, eps_exp=5.555, L_grain=0.1963,
        d_grain_outer=0.0467, d_port_0=0.0257,
        eta_cstar=0.860, eta_nozzle=0.860, fill_frac=0.950,
        cooling_coeff=0.1290, Cd_inj=0.3207,
        fuel="ABS", fuel_a=1.343e-04),
    "HyperTEK J317": dict(
        d_tank=0.04890, L_tank=0.44461, d_hole=0.00437, n_holes=1,
        d_throat=0.01184, eps_exp=3.421, L_grain=0.2267,
        d_grain_outer=0.0500, d_port_0=0.0381,
        eta_cstar=0.817, eta_nozzle=0.817, fill_frac=0.950,
        cooling_coeff=0.1388, Cd_inj=0.4451,
        fuel="ABS", fuel_a=1.173e-04),
    "HyperTEK K240": dict(
        d_tank=0.04890, L_tank=0.44461, d_hole=0.00317, n_holes=1,
        d_throat=0.01013, eps_exp=4.226, L_grain=0.2574,
        d_grain_outer=0.0500, d_port_0=0.0392,
        eta_cstar=0.970, eta_nozzle=0.970, fill_frac=0.950,
        cooling_coeff=0.1485, Cd_inj=0.3600,
        fuel="ABS", fuel_a=8.987e-05),
    "HyperTEK L550": dict(
        d_tank=0.06792, L_tank=0.46507, d_hole=0.00366, n_holes=1,
        d_throat=0.01407, eps_exp=4.140, L_grain=0.4804,
        d_grain_outer=0.0554, d_port_0=0.0441,
        eta_cstar=0.970, eta_nozzle=0.970, fill_frac=0.950,
        cooling_coeff=0.1498, Cd_inj=0.8004,
        fuel="ABS", fuel_a=6.909e-05),
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
    #
    # This is a VALIDATION FIXTURE, not a competition vehicle. Its job is to
    # reproduce one specific independently-computed flight, which is why it
    # carries that author's measured Cd of 1.625 and a 9,292 ft goal. It was
    # called "SystemsGo Goddard Baseline" until the real Goddard brief - 50,000
    # ft with a payload - was modelled, which this configuration reaches
    # nothing like. Renaming it keeps the reference honest and frees the
    # Goddard name for the vehicle that actually flies the mission.
    dict(
        name="hybrid_sim Reference Flight",
        description=("The configuration hybrid_sim is validated against, with "
                     "an independent spreadsheet reference for the whole "
                     "flight: 9,292 ft, Mach 0.57. Transonic-adjacent. A "
                     "reference case, not a Goddard-level vehicle."),
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
    # -- 3b. the Goddard brief: 50,000 ft with a payload -----------------------
    #
    # SystemsGo's Goddard level asks for a scratch-built vehicle that carries a
    # scientific payload to 50,000 ft, flown at White Sands. Nothing in the
    # measured-curve library is that motor, so this one was sized here against
    # the app's own engine and trajectory models and then graded by the same
    # Flight Report the user sees. It reaches 53,459 ft with no CRITICAL check
    # outstanding - see docs/VALIDATION.md for the three CAUTIONs that remain
    # and why they are inherent to a nitrous blowdown flying this profile.
    #
    # Sizing decisions worth knowing before editing any number here:
    #   * The 90 mm initial port and the 0.60 m grain are set by two checks
    #     pulling against each other - opening the port drops oxidiser flux
    #     (P-07) but thins the web that P-06 wants left at burnout. 90 mm in a
    #     170.8 mm bore lands at 472 kg/m^2s peak flux with 29% of the web
    #     still there.
    #   * The 42 mm throat and 2.6 mm orifices are the other opposed pair:
    #     opening the injector lifts thrust-to-weight (P-01) and pushes flux
    #     back over its limit. Flux is a physical limit on where the
    #     regression data is valid; the 5:1 thrust-to-weight is a convention
    #     for rail stability that R-01 already confirms directly.
    #   * The airframe is carbon rather than glass because 5.7 m of 184 mm
    #     tube in G10 costs 5.4 kg, which is worth about 5,600 ft here.
    dict(
        name="SystemsGo Goddard Baseline",
        description=("SystemsGo Goddard level: a scientific payload to 50,000 "
                     "ft. 184 mm carbon airframe, 78.6 kN.s nitrous/HTPB "
                     "hybrid, Mach 1.68. Reaches 53,459 ft."),
        reference=("Motor and airframe sized in this repository against the "
                   "Goddard brief; no measured curve of this class exists "
                   "here. Modelled, not flown."),
        thrust_curve=f"{_CURVES}/SystemsGo_Goddard_50k.csv",
        cd=0.55, main_alt=250, main_area=18.1,
        engine=dict(d_tank=0.1708, L_tank=2.60, fill_frac=0.85, T_tank_0=298.0,
                    n_holes=12, d_hole=0.0026, Cd_inj=0.75,
                    L_grain=0.60, d_grain_outer=0.1708, d_port_0=0.090,
                    d_throat=0.042, eps_exp=6.5, alpha_deg=15.0,
                    eta_cstar=0.90, eta_nozzle=0.95, gamma=1.22, MW=26.0),
        airframe=dict(
            nose_shape="Von Karman (LV-Haack)", nose_length_m=1.00,
            body_diameter_m=0.184, body_length_m=4.7614,
            surface_roughness_um=20.0, boattail_length_m=0.0,
            boattail_exit_diameter_m=0.0,
            fin_count=4, fin_root_chord_m=0.44, fin_tip_chord_m=0.18,
            fin_span_m=0.13, fin_sweep_m=0.22, fin_thickness_m=0.009,
            dry_mass_kg=56.808, propellant_mass_kg=41.889,
            dry_cg_m=3.496, propellant_cg_m=3.739,
            elevation_m=1216.0, temperature_c=25.0, wind_speed_ms=4.0,
            rail_length_m=10.0, rail_angle_deg=0.0,
            # No measured-Cd override: this vehicle does not exist, so there
            # is no measured number to honour and the model's own drag
            # buildup is the only defensible source.
            cd_override=0.0,
            # Every dry mass above is the sum of these, placed where they sit.
            # The stability margin the report grades is the margin of this
            # parts list, not of a number somebody typed.
            mass_components=[
                dict(name="Nose cone", mass_kg=2.508, position_m=0.550,
                     length_m=1.000, kind="Structure"),
                dict(name="Payload", mass_kg=2.000, position_m=1.200,
                     length_m=0.300, kind="Payload"),
                dict(name="Avionics bay", mass_kg=1.800, position_m=1.500,
                     length_m=0.350, kind="Avionics"),
                dict(name="Recovery", mass_kg=4.600, position_m=1.700,
                     length_m=0.500, kind="Recovery"),
                dict(name="Fwd closure / fill", mass_kg=1.400,
                     position_m=2.050, length_m=0.100, kind="Structure"),
                dict(name="Oxidiser tank", mass_kg=12.148, position_m=3.400,
                     length_m=2.600, kind="Structure"),
                dict(name="Plumbing / main valve", mass_kg=2.600,
                     position_m=4.550, length_m=0.400, kind="Other"),
                dict(name="Injector bulkhead", mass_kg=1.600,
                     position_m=4.730, length_m=0.060, kind="Structure"),
                dict(name="Chamber case", mass_kg=3.044, position_m=5.170,
                     length_m=0.820, kind="Structure"),
                dict(name="Chamber liner", mass_kg=2.750, position_m=5.170,
                     length_m=0.820, kind="Structure"),
                dict(name="Nozzle", mass_kg=3.507, position_m=5.671,
                     length_m=0.181, kind="Structure"),
                dict(name="Body tube", mass_kg=11.288, position_m=3.381,
                     length_m=4.761, kind="Structure"),
                dict(name="Fins", mass_kg=3.355, position_m=5.519,
                     length_m=0.440, kind="Structure"),
                dict(name="Fasteners / bonding", mass_kg=4.208,
                     position_m=3.381, length_m=4.761, kind="Other"),
            ]),
        recovery=None,
        recovery_spec=dict(kind="dual", drogue_d=1.2, main_d=4.8, main_alt=250),
        # Carbon airframe and nose, but GLASS fins: G10's shear modulus is
        # 7.0 GPa against carbon's 5.0, and fin flutter is a shear-stiffness
        # problem, so glass is the better fin at Mach 1.68 despite being
        # heavier. Tank is 7075 rather than 6061 - at 7.02 MPa hot-pad
        # saturation the wall drops from 3.5 mm to 2.4 mm, worth 5 kg.
        vehicle=dict(_GLASS, airframe_material="Carbon Fiber / Epoxy",
                     nose_material="Carbon Fiber / Epoxy",
                     tank_material="Aluminum 7075-T6",
                     body_od_m=0.184, body_wall_m=0.0026,
                     body_length_m=5.7614, fin_count=4,
                     fin_root_chord_m=0.44, fin_tip_chord_m=0.18,
                     fin_span_m=0.13, fin_thickness_m=0.009,
                     chamber_wall_m=0.00223, tank_wall_m=0.00238,
                     target_altitude_ft=50000, rail_length_m=10.0,
                     harness_rating_n=13000),
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
