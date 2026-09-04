"""Recovery system modelling: single deploy, dual deploy, reefing, streamers.

The simulator previously understood exactly one parachute, opening at a set
altitude with a fixed inflation ramp. Real flights - and anything going to
50,000 ft - use a drogue at apogee and a main down low, often with the main
reefed so it opens in two stages to keep the snatch load survivable.

This module models the recovery train as an ordered set of stages. Each stage
knows when it fires, how fast it inflates, and how much drag area it presents,
including a reefed phase at a fraction of full area before it disreefs.

Drag area (Cd*A) is the quantity that actually matters, so that is what this
returns; the flight integrator just adds it to the airframe's own drag.

Qt-free and importable on its own.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Canopy type -> (default Cd, description)
CANOPY_TYPES = {
    "Round parachute":      (1.50, "Classic hemispherical canopy. High drag, "
                                   "high snatch load, drifts a long way."),
    "Elliptical parachute": (1.60, "Slightly more efficient than round for the "
                                   "same fabric."),
    "Toroidal / annular":   (2.20, "Highest drag per square metre. Common for "
                                   "high-power mains."),
    "Cruciform (X-form)":   (0.85, "Very stable, low drag, easy to sew."),
    "Streamer":             (0.35, "A ribbon, not a canopy - drag comes from "
                                   "flutter. Sized by area but far less "
                                   "effective. Drogue duty only."),
    "Drogue slider":        (1.10, "Small stabilising drogue."),
}

# How a stage is triggered
TRIGGER_APOGEE = "apogee"
TRIGGER_ALTITUDE = "altitude"      # descending through a set altitude AGL
TRIGGER_TIME = "time"              # a set time after launch
TRIGGER_DELAY = "delay"            # a set time after the previous stage


@dataclass
class RecoveryStage:
    """One deployable device in the recovery train."""
    name: str = "Main"
    canopy_type: str = "Round parachute"
    diameter_m: float = 1.5
    cd: float = 1.5
    trigger: str = TRIGGER_ALTITUDE
    trigger_altitude_m: float = 300.0
    trigger_time_s: float = 0.0
    inflation_time_s: float = 1.5      # full-open ramp
    # Reefing: the canopy first opens to a fraction of full area, then
    # disreefs after a delay. This is how you survive a high-speed deployment.
    reefed: bool = False
    reef_ratio: float = 0.25           # area fraction while reefed
    reef_duration_s: float = 3.0       # time spent reefed before disreef
    disreef_time_s: float = 1.0        # ramp from reefed to full
    enabled: bool = True
    # A parachute is not weightless, and where it sits changes the CG. Canopy,
    # harness, deployment bag and charges all live at one station, so they get
    # folded into the mass buildup along with everything else.
    mass_kg: float = 0.0
    position_m: float = 0.0            # from the nose tip

    @property
    def area(self) -> float:
        """Full-open canopy area [m^2] from the nominal diameter."""
        return math.pi * (self.diameter_m / 2.0) ** 2

    @property
    def full_drag_area(self) -> float:
        """Cd*A at full open [m^2]."""
        return self.cd * self.area

    def drag_area_at(self, t_since_deploy: float) -> float:
        """Cd*A this stage presents, t seconds after it fired."""
        if t_since_deploy < 0:
            return 0.0
        full = self.full_drag_area
        if not self.reefed:
            if self.inflation_time_s <= 0:
                return full
            frac = min(1.0, t_since_deploy / self.inflation_time_s)
            return full * _inflation_curve(frac)

        # Reefed: inflate to the reefed area, hold, then disreef to full.
        reefed_area = full * self.reef_ratio
        if t_since_deploy < self.inflation_time_s:
            frac = (t_since_deploy / self.inflation_time_s
                    if self.inflation_time_s > 0 else 1.0)
            return reefed_area * _inflation_curve(frac)
        t_hold_end = self.inflation_time_s + self.reef_duration_s
        if t_since_deploy < t_hold_end:
            return reefed_area
        t_dis = t_since_deploy - t_hold_end
        if self.disreef_time_s <= 0:
            return full
        frac = min(1.0, t_dis / self.disreef_time_s)
        return reefed_area + (full - reefed_area) * _inflation_curve(frac)

    def fully_open_after(self) -> float:
        """Seconds from firing until this stage is at full drag area."""
        if not self.reefed:
            return max(0.0, self.inflation_time_s)
        return (max(0.0, self.inflation_time_s) + max(0.0, self.reef_duration_s)
                + max(0.0, self.disreef_time_s))


def _inflation_curve(frac: float) -> float:
    """Canopy area growth during inflation.

    Real canopies open slowly then snap open; a smooth S-curve is a better
    match than a straight ramp and keeps the load from spiking on step one.
    """
    f = max(0.0, min(1.0, frac))
    return f * f * (3.0 - 2.0 * f)


@dataclass
class RecoverySystem:
    """The whole recovery train, in firing order."""
    mode: str = "Dual deploy"
    stages: list = field(default_factory=list)

    @staticmethod
    def single_deploy(diameter_m=2.0, cd=1.5, altitude_m=0.0,
                      canopy="Round parachute"):
        """One canopy, out at apogee. Simple, and it drifts for miles."""
        return RecoverySystem("Single deploy (main at apogee)", [
            RecoveryStage(name="Main", canopy_type=canopy, diameter_m=diameter_m,
                          cd=cd, trigger=TRIGGER_APOGEE, inflation_time_s=2.0),
        ])

    @staticmethod
    def dual_deploy(drogue_d=0.9, drogue_cd=1.5, main_d=2.6, main_cd=1.5,
                    main_altitude_m=300.0, drogue_canopy="Drogue slider",
                    main_canopy="Round parachute"):
        """Drogue at apogee, main down low. The standard for anything high."""
        return RecoverySystem("Dual deploy (drogue + main)", [
            RecoveryStage(name="Drogue", canopy_type=drogue_canopy,
                          diameter_m=drogue_d, cd=drogue_cd,
                          trigger=TRIGGER_APOGEE, inflation_time_s=1.0),
            RecoveryStage(name="Main", canopy_type=main_canopy,
                          diameter_m=main_d, cd=main_cd,
                          trigger=TRIGGER_ALTITUDE,
                          trigger_altitude_m=main_altitude_m,
                          inflation_time_s=2.0),
        ])

    @staticmethod
    def streamer_drogue(streamer_d=0.5, main_d=2.6, main_altitude_m=300.0):
        """Streamer instead of a drogue canopy - less drag, far less drift."""
        return RecoverySystem("Streamer drogue + main", [
            RecoveryStage(name="Streamer", canopy_type="Streamer",
                          diameter_m=streamer_d, cd=0.35,
                          trigger=TRIGGER_APOGEE, inflation_time_s=0.5),
            RecoveryStage(name="Main", canopy_type="Round parachute",
                          diameter_m=main_d, cd=1.5,
                          trigger=TRIGGER_ALTITUDE,
                          trigger_altitude_m=main_altitude_m,
                          inflation_time_s=2.0),
        ])

    @staticmethod
    def reefed_main(main_d=3.0, main_cd=1.5, altitude_m=450.0,
                    reef_ratio=0.2, reef_duration=4.0):
        """A single main that opens reefed, then disreefs. Halves the snatch
        load of a straight high-speed main deployment."""
        return RecoverySystem("Reefed single deploy", [
            RecoveryStage(name="Main (reefed)", canopy_type="Round parachute",
                          diameter_m=main_d, cd=main_cd,
                          trigger=TRIGGER_ALTITUDE,
                          trigger_altitude_m=altitude_m,
                          inflation_time_s=1.0, reefed=True,
                          reef_ratio=reef_ratio, reef_duration_s=reef_duration,
                          disreef_time_s=1.5),
        ])

    # --- runtime -----------------------------------------------------------
    def active_stages(self):
        return [s for s in self.stages if s.enabled]

    def reset(self):
        """Clear per-flight deployment state."""
        for s in self.stages:
            s._t_fired = None

    def update(self, t: float, altitude_m: float, velocity_ms: float,
               past_apogee: bool, launched: bool = True):
        """Fire any stage whose trigger condition has been met.

        `launched` gates the time-based triggers. trigger_time_s defaults to
        0.0, so a stage switched to "at a set time" without editing the time
        would otherwise fire on the first step, with the vehicle still on the
        rail - which reads as a deployment failure that never happened.

        Returns the list of stages that fired on this call.
        """
        fired = []
        previous_fire_time = None
        for stage in self.active_stages():
            already = getattr(stage, "_t_fired", None)
            if already is not None:
                previous_fire_time = already
                continue
            trigger = stage.trigger
            go = False
            if trigger == TRIGGER_APOGEE:
                go = past_apogee
            elif trigger == TRIGGER_ALTITUDE:
                go = past_apogee and altitude_m <= stage.trigger_altitude_m
            elif trigger == TRIGGER_TIME:
                go = launched and t > 0.0 and t >= stage.trigger_time_s
            elif trigger == TRIGGER_DELAY:
                go = (launched and previous_fire_time is not None
                      and t >= previous_fire_time + stage.trigger_time_s)
            if go:
                stage._t_fired = t
                fired.append(stage)
                previous_fire_time = t
        return fired

    def drag_area(self, t: float) -> float:
        """Total Cd*A from every deployed stage [m^2]."""
        total = 0.0
        for stage in self.active_stages():
            fired = getattr(stage, "_t_fired", None)
            if fired is not None:
                total += stage.drag_area_at(t - fired)
        return total

    def mass_components(self):
        """The recovery train as mass components, for the mass buildup."""
        import mass_model
        out = []
        for s in self.stages:
            if s.mass_kg > 0:
                out.append(mass_model.MassComponent(
                    name=f"{s.name} (recovery)", mass_kg=s.mass_kg,
                    position_m=s.position_m, kind="Recovery",
                    enabled=s.enabled))
        return out

    def total_mass(self) -> float:
        return sum(s.mass_kg for s in self.stages if s.enabled)

    def deployed_names(self):
        return [s.name for s in self.active_stages()
                if getattr(s, "_t_fired", None) is not None]

    def any_deployed(self) -> bool:
        return any(getattr(s, "_t_fired", None) is not None
                   for s in self.active_stages())

    def descent_rate(self, mass_kg: float, rho: float = 1.225,
                     g: float = 9.80665) -> float:
        """Steady-state descent rate under everything fully open [m/s]."""
        cda = sum(s.full_drag_area for s in self.active_stages())
        if cda <= 0:
            return float("inf")
        return math.sqrt(2.0 * mass_kg * g / (rho * cda))

    def descent_rate_stage(self, stage_index: int, mass_kg: float,
                           rho: float = 1.225, g: float = 9.80665) -> float:
        """Descent rate with only the first N stages open, e.g. drogue only."""
        stages = self.active_stages()[:stage_index + 1]
        cda = sum(s.full_drag_area for s in stages)
        if cda <= 0:
            return float("inf")
        return math.sqrt(2.0 * mass_kg * g / (rho * cda))

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "stages": [{
                "name": s.name, "canopy_type": s.canopy_type,
                "diameter_m": s.diameter_m, "cd": s.cd, "trigger": s.trigger,
                "trigger_altitude_m": s.trigger_altitude_m,
                "trigger_time_s": s.trigger_time_s,
                "inflation_time_s": s.inflation_time_s,
                "reefed": s.reefed, "reef_ratio": s.reef_ratio,
                "reef_duration_s": s.reef_duration_s,
                "disreef_time_s": s.disreef_time_s, "enabled": s.enabled,
                "mass_kg": s.mass_kg, "position_m": s.position_m,
            } for s in self.stages],
        }

    @staticmethod
    def from_dict(data: dict) -> "RecoverySystem":
        if not data:
            return RecoverySystem.dual_deploy()
        stages = []
        for raw in data.get("stages", []):
            stage = RecoveryStage()
            for key, value in raw.items():
                if hasattr(stage, key):
                    setattr(stage, key, value)
            stages.append(stage)
        return RecoverySystem(data.get("mode", "Custom"), stages)
