"""RASP ``.eng`` motor files: impulse classification and writing.

JARVIS could already *read* .eng files but never write one, so a motor
designed in the Engine Lab could not leave the app. Every other tool a
SystemsGo team touches - OpenRocket, RASAero, ThrustCurve.org - speaks this
format, and it is what a flight-readiness package is expected to carry.

The format is a published de-facto standard (RASP/NAR), not anyone's
implementation:

    designation  diameter_mm  length_mm  delays  propMass_kg  totalMass_kg  mfg
    <time_s> <thrust_N>
    ...
    ;

Note the two mass fields are KILOGRAMS. That is the single most commonly
mis-read part of the format, and JARVIS's own reader used to label them
grams.
"""
from __future__ import annotations

import math

# The NAR/TRA impulse ladder: class A spans 1.25-2.5 N*s and every letter
# after it doubles. Below 1.25 N*s a motor is under class A ("1/4A" and
# friends), which this deliberately does not try to name.
_CLASS_A_MIN_NS = 1.25

# Stand-in for "thrust has just begun": small enough to be worth no
# measurable impulse, large enough that a reader treating 0 as the end
# of the curve does not stop on it.
_IGNITION_ANCHOR_N = 0.01


def impulse_class(total_impulse_ns: float) -> str:
    """Letter class for a total impulse, e.g. 1580 N*s -> 'K'.

    Past Z the class continues 'AA', 'AB', ... which is what the very large
    research motors use.
    """
    if total_impulse_ns < _CLASS_A_MIN_NS:
        return ""
    # How many doublings above the bottom of class A: 0 -> A, 1 -> B, ...
    order = int(math.log(total_impulse_ns / _CLASS_A_MIN_NS, 2))
    letters = ""
    while True:
        letters = chr(ord("A") + (order % 26)) + letters
        order = order // 26 - 1
        if order < 0:
            break
    return letters


def designation(total_impulse_ns: float, average_thrust_n: float) -> str:
    """Standard amateur designation: class letter + average thrust.

    A 1,580 N*s motor averaging 240 N is 'K240' - the same shape as the
    HyperTEK names the preset library already decodes, now generated for a
    motor of the user's own design.
    """
    letters = impulse_class(total_impulse_ns)
    if not letters:
        return "N/A"
    return f"{letters}{average_thrust_n:.0f}"


def class_fraction(total_impulse_ns: float) -> float:
    """How far up its impulse class a motor sits, 0.0 to 1.0.

    A motor at 0.95 is nearly the next letter up, so a small design change
    could reclassify it - which matters when a launch waiver or a contest
    category is written in terms of the letter.
    """
    if total_impulse_ns < _CLASS_A_MIN_NS:
        return 0.0
    floor_ns = _CLASS_A_MIN_NS * 2 ** int(
        math.log(total_impulse_ns / _CLASS_A_MIN_NS, 2))
    return (total_impulse_ns - floor_ns) / floor_ns


def _curve_points(times, thrusts):
    """Clean a simulated curve into points a RASP reader will accept.

    Readers are stricter than the simulator: time must increase strictly,
    the curve may not open with a zero-thrust sample at t=0, and it must
    close on exactly one zero.
    """
    pts = [(float(t), max(0.0, float(f))) for t, f in zip(times, thrusts)]
    pts.sort(key=lambda p: p[0])

    # Trim the leading dead time, but anchor the ignition instead of deleting
    # it. Readers dislike a literal "0.0 0.0" opening sample, yet a curve that
    # simply *starts* at full thrust is worse: JARVIS's own impulse routine
    # (and RASAero's) holds the first thrust value back to t=0, inventing a
    # rectangle of impulse that never burned - 4.4% on a curve with 0.4s of
    # ignition delay. One near-zero sample at the moment thrust begins keeps
    # the delay honest and costs ~0.003 N*s.
    first_live = next((i for i, (_, f) in enumerate(pts) if f > 0.0), None)
    if first_live is None:
        return []
    if first_live > 0:
        anchor_t = pts[first_live - 1][0]
        pts = [(anchor_t, _IGNITION_ANCHOR_N)] + pts[first_live:]
    elif pts[0][0] > 0.0:
        # Curve opens mid-thrust with no dead-time sample of its own.
        pts = [(0.0, _IGNITION_ANCHOR_N)] + pts

    # Strictly increasing time; keep the first sample at any timestamp.
    cleaned = []
    for t, f in pts:
        if cleaned and t <= cleaned[-1][0]:
            continue
        cleaned.append((t, f))

    # Trim the trailing zeros back to a single closing zero. Close it at the
    # time the curve itself went to zero, not an invented instant later: the
    # decay from the last thrusting sample down to zero is real impulse, and
    # collapsing it into 0.01s threw away 0.5% of the total.
    last_live = max(i for i, (_, f) in enumerate(cleaned) if f > 0.0)
    tail_t = (cleaned[last_live + 1][0] if last_live + 1 < len(cleaned)
              else cleaned[last_live][0] + 0.01)
    return cleaned[:last_live + 1] + [(tail_t, 0.0)]


def write_eng(path, times, thrusts, *, designation_str, diameter_m,
              length_m, propellant_mass_kg, total_mass_kg,
              manufacturer="JARVIS", append=False):
    """Write one motor block in RASP .eng format.

    Multiple motors may share a file, which is why append is offered: that is
    how a team keeps one .eng of every motor they have flown.
    """
    if propellant_mass_kg > total_mass_kg:
        raise ValueError(
            f"propellant mass ({propellant_mass_kg:.3f} kg) exceeds total mass "
            f"({total_mass_kg:.3f} kg) - the loaded motor cannot weigh less "
            f"than its propellant")

    points = _curve_points(times, thrusts)
    if len(points) < 2:
        raise ValueError("thrust curve has no positive thrust to export")

    # Spaces separate fields, so a designation carrying one would silently
    # shift every later field.
    safe_designation = "_".join(str(designation_str).split()) or "UNNAMED"
    safe_mfg = "_".join(str(manufacturer).split()) or "JARVIS"

    lines = [
        "; RASP .eng motor file written by JARVIS",
        "; Fields: designation  dia(mm)  len(mm)  delays  "
        "propellant(kg)  total(kg)  manufacturer",
        f"{safe_designation} {diameter_m * 1000.0:.6g} {length_m * 1000.0:.6g} "
        f"P {propellant_mass_kg:.6f} {total_mass_kg:.6f} {safe_mfg}",
    ]
    lines += [f"   {t:.4f} {f:.4f}" for t, f in points]
    lines.append(";")

    with open(path, "a" if append else "w", encoding="utf-8") as handle:
        if append:
            handle.write("\n")
        handle.write("\n".join(lines) + "\n")
    return path
