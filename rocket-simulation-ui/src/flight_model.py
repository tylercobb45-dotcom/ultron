"""Two-degree-of-freedom flight model: wind, full atmosphere, Mach-5 aero,
staged recovery, and a centre of gravity that moves as propellant burns.

Why this exists alongside simulation.run_simulation: the original model is a
purely vertical integration with one drag coefficient, one parachute, and an
atmosphere that stops being right above about 20 km. Adding wind drift,
supersonic drag, a recovery train, and shifting mass properties to it in place
would have meant rewriting its loop and risking every flight the app has ever
produced. This is a separate path that the app uses when a vehicle has an
airframe defined; run_simulation stays exactly as it was for older rocket
profiles.

It emits every field the original produced, so the plots, spreadsheet,
telemetry and failure analysis all keep working, plus the new ones (downrange
drift, angle of attack, CP/CG, stability margin, drag breakdown, local
atmosphere).

Axes: x is downrange, positive in the direction the wind blows toward; z is
altitude above ground level.
"""
from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import dataclass

import aero as aero_mod
import atmosphere as atmosphere_mod
import recovery as recovery_mod

G0 = 9.80665
SIM_VERSION = 3


@dataclass
class MassProperties:
    """Mass and balance. Positions are measured from the nose tip, in metres."""
    dry_mass_kg: float = 20.0
    propellant_mass_kg: float = 6.0
    dry_cg_m: float = 1.60          # CG of the empty vehicle
    propellant_cg_m: float = 2.40   # CG of the propellant column (aft)

    def cg(self, propellant_remaining_kg: float) -> float:
        """Instantaneous CG. Moves as propellant burns off - forward for the
        usual aft-mounted motor, which is why stability changes during boost."""
        m_p = max(0.0, propellant_remaining_kg)
        total = self.dry_mass_kg + m_p
        if total <= 0:
            return self.dry_cg_m
        return (self.dry_mass_kg * self.dry_cg_m + m_p * self.propellant_cg_m) / total


# ---------------------------------------------------------------------------
# thrust curve loading
# ---------------------------------------------------------------------------

def load_thrust_curve(path: str):
    """Read a thrust curve. Returns (points, propellant_mass_kg_or_None).

    Understands the CSV layout the Engine Lab writes and the sample files
    ship in, plus RASP .eng files.
    """
    if not path or not os.path.isfile(path):
        return [], None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".eng", ".rasp"):
        return _load_rasp(path)
    return _load_csv(path)


def _load_csv(path):
    points, prop_mass = [], None
    with open(path, newline="") as f:
        rows = [r for r in csv.reader(f) if any((c or "").strip() for c in r)]
    header_idx = None
    for i, row in enumerate(rows):
        low = [(c or "").strip().lower() for c in row]
        if any("thrust" in c for c in low):
            header_idx = i
            break
    if header_idx is None:
        return [], None
    header = [(c or "").strip().lower() for c in rows[header_idx]]
    t_col = next((j for j, c in enumerate(header)
                  if "time" in c or c.startswith("t (") or c == "t"), 0)
    f_col = next((j for j, c in enumerate(header) if "thrust" in c), 1)
    # Propellant mass is often recorded in the metadata above the header.
    for row in rows[:header_idx]:
        line = ",".join(row).lower()
        if "propellant" in line or "prop mass" in line:
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", line)
            if nums:
                try:
                    value = float(nums[-1])
                    if " g" in line and "kg" not in line:
                        value /= 1000.0
                    if value > 0:
                        prop_mass = value
                except ValueError:
                    pass
    for row in rows[header_idx + 1:]:
        if max(t_col, f_col) >= len(row):
            continue
        try:
            points.append((float(row[t_col]), max(0.0, float(row[f_col]))))
        except ValueError:
            continue
    points.sort(key=lambda p: p[0])
    return points, prop_mass


def _load_rasp(path):
    points, prop_mass = [], None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            parts = line.split()
            if len(parts) >= 7 and not _is_number(parts[0]):
                # Header: name dia len delays prop_mass total_mass manufacturer
                try:
                    prop_mass = float(parts[4])
                except (ValueError, IndexError):
                    pass
                continue
            if len(parts) >= 2 and _is_number(parts[0]) and _is_number(parts[1]):
                points.append((float(parts[0]), max(0.0, float(parts[1]))))
    points.sort(key=lambda p: p[0])
    return points, prop_mass


def _is_number(token):
    try:
        float(token)
        return True
    except ValueError:
        return False


def _thrust_at(points, t):
    if not points:
        return 0.0
    if t <= points[0][0]:
        return points[0][1] if t >= 0 else 0.0
    if t >= points[-1][0]:
        return 0.0
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    t0, f0 = points[lo]
    t1, f1 = points[hi]
    if t1 == t0:
        return f1
    return f0 + (f1 - f0) * (t - t0) / (t1 - t0)


def total_impulse(points):
    return sum((points[i][1] + points[i - 1][1]) / 2.0 *
               (points[i][0] - points[i - 1][0]) for i in range(1, len(points)))


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

def run_flight(thrust_points,
               airframe: aero_mod.Airframe,
               site: atmosphere_mod.LaunchSite,
               recovery_system: recovery_mod.RecoverySystem,
               mass_props: MassProperties,
               dt: float = 0.01,
               output_dt: float = 0.05,
               max_time: float = 900.0,
               cd_override: float | None = None):
    """Integrate a flight. Returns a list of per-sample dicts.

    Semi-implicit Euler at a small fixed step: recovery deployment and rail
    departure are state-dependent events, which a fixed small step handles
    cleanly and an adaptive solver does not without event machinery.

    cd_override forces a single constant drag coefficient instead of the
    component buildup. Use it when you have a measured or CFD-derived Cd you
    trust more than the estimate, or to compare against another tool that
    assumed a fixed number.
    """
    site = site or atmosphere_mod.LaunchSite()
    airframe = airframe or aero_mod.Airframe()
    recovery_system = recovery_system or recovery_mod.RecoverySystem.dual_deploy()
    mass_props = mass_props or MassProperties()
    recovery_system.reset()

    burn_end = thrust_points[-1][0] if thrust_points else 0.0
    impulse = total_impulse(thrust_points)
    prop_mass = max(0.0, mass_props.propellant_mass_kg)
    # Effective Isp implied by the curve and the propellant it consumes.
    isp = impulse / (prop_mass * G0) if prop_mass > 0 and impulse > 0 else 0.0

    rail_angle = math.radians(site.rail_angle_deg)
    rail_dir = (math.sin(rail_angle), math.cos(rail_angle))
    rail_len = max(0.0, site.rail_length_m)

    a_ref = airframe.reference_area
    diameter = max(1e-6, airframe.body_diameter_m)

    # state
    t = 0.0
    x, z = 0.0, 0.0
    vx, vz = 0.0, 0.0
    prop_left = prop_mass
    on_rail = True
    theta = rail_angle          # vehicle axis angle from vertical [rad]
    rail_travel = 0.0
    past_apogee = False
    launched = False
    apogee_z = 0.0
    rail_exit_speed = None
    max_q_seen = 0.0

    results = []
    next_output = 0.0
    steps = 0
    max_steps = int(max_time / dt) + 1

    while steps < max_steps:
        steps += 1
        mass = mass_props.dry_mass_kg + prop_left
        thrust = _thrust_at(thrust_points, t)
        thrusting = thrust > 0.01

        T_air, P_air, rho, a_sound, mu = site.properties(z)
        # This model is planar, so x is defined as the downwind direction and
        # the wind is its full magnitude - taking a single compass component
        # would silently drop most of it.
        wind_e = site.wind_speed_at(z)

        # Air-relative velocity: this, not ground speed, drives every
        # aerodynamic force and is what makes the vehicle weathercock.
        rvx, rvz = vx - wind_e, vz
        speed_rel = math.hypot(rvx, rvz)
        speed_ground = math.hypot(vx, vz)
        mach = speed_rel / a_sound if a_sound > 0 else 0.0

        # Thrust points along the vehicle's axis. The axis chases the
        # air-relative wind (that is weathercocking) but it cannot snap to it:
        # the vehicle has pitch inertia and the fins supply a restoring moment,
        # so it rotates with a finite time constant. Aligning thrust straight
        # to the relative wind instead makes a light crosswind flip the rocket
        # over, which is the classic way this gets modelled wrong.
        q_now = 0.5 * rho * speed_rel * speed_rel
        if on_rail:
            theta = rail_angle
        elif speed_rel > 1e-6:
            theta_rel = math.atan2(rvx, max(1e-9, rvz))
            cg_now = mass_props.cg(prop_left)
            cp_now = airframe.center_of_pressure(mach)
            margin_cal = (cp_now - cg_now) / diameter
            # Pitch natural frequency from the aerodynamic restoring moment,
            # against a slender-body inertia estimate.
            inertia = mass * (airframe.total_length / 3.5) ** 2
            restoring = (q_now * a_ref * diameter
                         * airframe.normal_force_slope() * max(0.0, margin_cal))
            if restoring > 0 and inertia > 0:
                omega = math.sqrt(restoring / inertia)
                tau = min(20.0, max(0.05, 1.0 / omega))
            else:
                tau = 20.0     # no restoring moment: it barely turns at all
            theta += (theta_rel - theta) * min(1.0, dt / tau)
        dir_x, dir_z = math.sin(theta), math.cos(theta)

        # Angle of attack: between where the vehicle points and where the air
        # is actually coming from.
        if speed_rel > 1e-6:
            theta_rel_now = math.atan2(rvx, max(1e-9, rvz))
            alpha = abs(math.degrees(theta - theta_rel_now))
        else:
            alpha = 0.0
        tilt_deg = math.degrees(theta)

        deployed_before = recovery_system.any_deployed()
        cd_body, breakdown = aero_mod.drag_coefficient(
            mach, z, speed_rel, airframe, site, thrusting=thrusting)
        if cd_override is not None:
            cd_body = cd_override
        cda_body = cd_body * a_ref
        cda_recovery = recovery_system.drag_area(t)
        # Once a canopy is out the airframe is no longer flying nose-first;
        # its own drag is small next to the canopy and broadly attitude
        # independent, so take the reference area rather than the streamlined
        # coefficient.
        if cda_recovery > 0:
            cda_body = 0.8 * a_ref
        cda_total = cda_body + cda_recovery

        q = 0.5 * rho * speed_rel * speed_rel
        drag_mag = q * cda_total
        max_q_seen = max(max_q_seen, q)

        if speed_rel > 1e-9:
            drag_x = -drag_mag * rvx / speed_rel
            drag_z = -drag_mag * rvz / speed_rel
        else:
            drag_x = drag_z = 0.0

        g = site.gravity(z)
        fx = thrust * dir_x + drag_x
        fz = thrust * dir_z + drag_z - mass * g

        # On the rail the vehicle cannot move sideways, and it cannot move at
        # all until thrust exceeds weight.
        if on_rail:
            along = fx * rail_dir[0] + fz * rail_dir[1]
            if along < 0 and rail_travel <= 0.0:
                along = 0.0
            fx, fz = along * rail_dir[0], along * rail_dir[1]

        ax, az = fx / mass, fz / mass
        accel_mag = math.hypot(ax, az)
        # Signed vertical acceleration is what the legacy output reported.
        accel_signed = az

        # --- record a sample --------------------------------------------
        # Under canopy the flight is a slow steady drift; sampling it as
        # finely as the boost produces tens of thousands of identical rows.
        sample_dt = output_dt * (10.0 if recovery_system.any_deployed() else 1.0)
        if t >= next_output - 1e-12:
            cg = mass_props.cg(prop_left)
            cp = airframe.center_of_pressure(mach)
            stability = (cp - cg) / diameter
            chute_cda = cda_recovery
            results.append({
                # --- legacy contract ---
                'time': t,
                'altitude': z,
                'velocity': vz,
                'acceleration': accel_signed,
                'thrust': thrust,
                'drag': drag_mag,
                'drag_signed': -drag_mag if vz >= 0 else drag_mag,
                'drag_raw_signed': -drag_mag if vz >= 0 else drag_mag,
                'drag_signed_uncapped': -drag_mag if vz >= 0 else drag_mag,
                'drag_cap_applied': False,
                'drag_cap_method': 'none',
                'Cd_eff': (cda_total / a_ref) if a_ref > 0 else 0.0,
                'A_eff': a_ref,
                'chute_deployed': cda_recovery > 0,
                'mass': mass,
                'mdot': (thrust / (isp * G0)) if (isp > 0 and thrusting) else 0.0,
                'sim_version': SIM_VERSION,
                'rho_local': rho,
                'q': q,
                'Mach': mach,
                'Cd_body_eff': cd_body,
                'terminal_v_body': _terminal_velocity(mass, g, rho, cda_body),
                'terminal_v_current': _terminal_velocity(mass, g, rho, cda_total),
                'rocket_drag_signed_raw': -(q * cda_body),
                'chute_drag_signed_raw': -(q * chute_cda),
                'chute_fill': (chute_cda / _full_recovery_cda(recovery_system)
                               if _full_recovery_cda(recovery_system) > 0 else 0.0),
                'rocket_drag_signed_smoothed': -(q * cda_body),
                'chute_drag_signed_smoothed_uncapped': -(q * chute_cda),
                'chute_drag_signed_smoothed': -(q * chute_cda),
                'ballistic_coeff_body': (mass / cda_body) if cda_body > 0 else 0.0,
                'ballistic_coeff_current': (mass / cda_total) if cda_total > 0 else 0.0,
                'initial_mass': mass_props.dry_mass_kg + prop_mass,
                'propellant_mass': prop_mass,
                'dry_mass': mass_props.dry_mass_kg,
                # --- new in the 2-DOF model ---
                'downrange': x,
                'horizontal_velocity': vx,
                'ground_speed': speed_ground,
                'airspeed': speed_rel,
                'wind_speed': wind_e,
                'angle_from_vertical_deg': tilt_deg,
                'angle_of_attack_deg': alpha,
                'temperature_k': T_air,
                'pressure_pa': P_air,
                'cg_m': cg,
                'cp_m': cp,
                'stability_cal': stability,
                'propellant_remaining': prop_left,
                'cd_friction': breakdown['friction'],
                'cd_base': breakdown['base'],
                'cd_wave': breakdown['wave'],
                'cd_fins': breakdown['fins'],
                'reynolds': breakdown['reynolds'],
                'cda_recovery': cda_recovery,
                'recovery_deployed': ', '.join(recovery_system.deployed_names()),
                'on_rail': on_rail,
                'accel_total': accel_mag,
            })
            next_output += sample_dt

        # --- integrate ---------------------------------------------------
        vx += ax * dt
        vz += az * dt
        x += vx * dt
        z += vz * dt
        t += dt

        if prop_left > 0 and isp > 0 and thrusting:
            prop_left = max(0.0, prop_left - thrust / (isp * G0) * dt)

        if on_rail:
            rail_travel = math.hypot(x, z)
            if rail_travel >= rail_len:
                on_rail = False
                rail_exit_speed = math.hypot(vx, vz)

        if z > 0.5:
            launched = True
        if z > apogee_z:
            apogee_z = z
        if launched and not past_apogee and vz < 0:
            past_apogee = True

        recovery_system.update(t, z, vz, past_apogee, launched=launched)

        if launched and z <= 0.0:
            break
        if z < 0.0:
            z = 0.0
        # Never left the pad: the motor is spent and it is still sitting there.
        if not launched and t > burn_end + 5.0:
            break

    summary = {
        'apogee_m': apogee_z,
        'apogee_ft': apogee_z * 3.28084,
        'rail_exit_speed': rail_exit_speed,
        'max_q': max_q_seen,
        'impulse': impulse,
        'isp': isp,
        'burn_time': burn_end,
        'drift_m': x,
        'landed': launched and z <= 0.0,
    }
    return results, summary


def _terminal_velocity(mass, g, rho, cda):
    if cda <= 0 or rho <= 0:
        return 0.0
    return math.sqrt(2.0 * mass * g / (rho * cda))


def _full_recovery_cda(system):
    return sum(s.full_drag_area for s in system.active_stages())
