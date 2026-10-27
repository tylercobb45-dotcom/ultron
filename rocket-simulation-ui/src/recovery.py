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
        """One canopy. At apogee by default, or at a set altitude if given.

        altitude_m > 0 holds the canopy until the vehicle has descended
        through that height, which is the whole point of a single deploy on a
        high flight: falling ballistic to low altitude before opening is what
        keeps the drift down. It used to be accepted and ignored.
        """
        if altitude_m and altitude_m > 0:
            return RecoverySystem("Single deploy (main at altitude)", [
                RecoveryStage(name="Main", canopy_type=canopy,
                              diameter_m=diameter_m, cd=cd,
                              trigger=TRIGGER_ALTITUDE,
                              trigger_altitude_m=altitude_m,
                              inflation_time_s=2.0),
            ])
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


# ---------------------------------------------------------------------------
# Ejection charges: how much black powder, or how much CO2, to shear the pins
# ---------------------------------------------------------------------------
#
# A deployment charge has one job: raise the pressure inside a bay until the
# force on the bulkhead exceeds whatever is holding the airframe together,
# which on anything that flies fast is a set of shear pins. The sum is small
# and every term in it is known, so there is no reason to guess it:
#
#     force to break out   F = n * tau * A_pin      (+ friction)
#     pressure that needs  P = F * safety / A_bay
#     powder for that      m = P * V / (R * T)
#
# NOTHING HERE REPLACES A GROUND TEST. The numbers below size the charge; the
# ground test is what tells you the bay actually opens, that the shear pins
# were in the holes you think they were, and that the charge did not simply
# blow past a badly fitting bulkhead. Every reference on this says the same
# thing and so does this module: calculate, then test, then fly.

#: Ultimate SHEAR strength, Pa. Shear, not tensile - a pin in a shear pin hole
#: fails by being cut through, and quoting tensile here would undersize the
#: charge by roughly a third.
SHEAR_PIN_MATERIALS = {
    "Nylon 6/6":      65.5e6,   # 9,500 psi. The usual shear pin.
    "Nylon 6":        60.0e6,   # 8,700 psi.
    "Polycarbonate":  69.0e6,   # 10,000 psi. Tougher, less predictable.
    "Delrin / POM":   67.0e6,   # 9,700 psi.
    "Brass":         200.0e6,   # Not a shear pin. Here so a wrong choice
    "Steel":         340.0e6,   # shows up as an absurd charge, not silently.
}

#: Thread sizes, as (major diameter m, minor diameter m). The MINOR diameter
#: is what carries the shear: a screw shears at the thread root, not across
#: the crests. Using the major diameter overstates a 2-56 pin by 80%, and the
#: minor-diameter figures below land on the 30-35 lbf that vendors publish
#: for exactly that pin, which is the check that the choice is right.
SHEAR_PINS = {
    "2-56 nylon":  (0.086 * 0.0254, 0.0642 * 0.0254),
    "4-40 nylon":  (0.112 * 0.0254, 0.0813 * 0.0254),
    "6-32 nylon":  (0.138 * 0.0254, 0.0997 * 0.0254),
    "8-32 nylon":  (0.164 * 0.0254, 0.1257 * 0.0254),
    "10-24 nylon": (0.190 * 0.0254, 0.1389 * 0.0254),
    "M3 nylon":    (0.0030, 0.00239),
    "M4 nylon":    (0.0040, 0.00314),
}

#: Black powder combustion gas, as the standard sizing figures give it:
#: R = 266 in.lbf/(lbm.R) and T = 3307 R. Converted once, here, so the
#: arithmetic below is SI and still checkable against any published table.
#: 266 in.lbf/(lbm.R) -> 119.26 J/(kg.K); 3307 R -> 1837 K.
BP_GAS_CONSTANT = 119.26          # J/(kg.K)
BP_GAS_TEMPERATURE_K = 1837.0

#: CO2 leaves a cartridge as a gas that has just boiled and expanded, so it
#: is nowhere near flame temperature. Sizing it at roughly cabin temperature
#: is the conservative reading: colder gas means more mass for the same
#: pressure, and this number being slightly pessimistic costs grams.
CO2_MOLAR_MASS = 0.04401          # kg/mol
CO2_TEMPERATURE_K = 293.0
_R_UNIVERSAL = 8.31446

#: The cartridges actually sold, in grams.
CO2_CARTRIDGES = (8, 12, 16, 25, 33, 38, 45, 50, 60, 74, 88)

CHARGE_BLACK_POWDER = "Black powder"
CHARGE_CO2 = "CO2"


def pin_shear_force(pin: str = "2-56 nylon", count: int = 2,
                    material: str = "Nylon 6/6") -> float:
    """Force in newtons needed to shear `count` pins of this size.

    Single shear: each pin is cut on one plane, because the airframe wall and
    the coupler slide past each other in one place. Double shear would halve
    the required pressure and does not describe a shear pin hole.

    These come out at the BOTTOM of the range vendors publish - 30.8 lbf for
    a 2-56 against a published 30-40, 74 lbf for a 6-32 against 75-100 - and
    that is the direction that undersizes a charge, so it matters. The
    safety factor is what covers it: at the default 1.5 a 2-56 bay is sized
    to 46 lbf against a pin that breaks by 40 at worst. Do not drop the
    safety factor below 1.5 on the strength of these numbers, and ground
    test, which settles it properly.
    """
    _major, minor = SHEAR_PINS.get(pin, SHEAR_PINS["2-56 nylon"])
    tau = SHEAR_PIN_MATERIALS.get(material, SHEAR_PIN_MATERIALS["Nylon 6/6"])
    area = math.pi * 0.25 * minor * minor
    return tau * area * max(0, int(count))


def pins_for_force(hold_force_n: float, pin: str = "2-56 nylon",
                   material: str = "Nylon 6/6") -> int:
    """How many pins it takes to hold `hold_force_n` without shearing.

    The other direction of the same sum, and the one that decides how many
    pins go in the airframe: the pins have to survive drogue descent and the
    drag trying to pull the bay apart, and they have to break on the charge
    you can fit. Rounded UP - a fractional pin holds nothing.
    """
    one = pin_shear_force(pin, 1, material)
    if one <= 0:
        return 0
    return int(math.ceil(max(0.0, hold_force_n) / one))


@dataclass
class EjectionCharge:
    """One bay, its pins, and the charge that has to open it."""
    name: str = "Drogue bay"
    bay_diameter_m: float = 0.1016          # 4 in
    bay_length_m: float = 0.3048            # 12 in
    #: Set non-zero to override the cylinder volume - a bay packed with a
    #: chute and an av-bay does not contain its own geometric volume. The
    #: free volume is what the gas fills, and it is smaller.
    free_volume_m3: float = 0.0
    pin_type: str = "2-56 nylon"
    pin_count: int = 2
    pin_material: str = "Nylon 6/6"
    #: Friction of the coupler and the nose cone fit, in newtons. Real and
    #: unmeasurable without pulling the thing apart, so it is an input, not a
    #: correlation pretending to know.
    friction_n: float = 0.0
    safety_factor: float = 1.5
    charge_type: str = CHARGE_BLACK_POWDER
    co2_temperature_k: float = CO2_TEMPERATURE_K

    # --- geometry ----------------------------------------------------------
    @property
    def bay_area_m2(self) -> float:
        return math.pi * 0.25 * self.bay_diameter_m ** 2

    @property
    def volume_m3(self) -> float:
        if self.free_volume_m3 and self.free_volume_m3 > 0:
            return self.free_volume_m3
        return self.bay_area_m2 * self.bay_length_m

    # --- what has to break ---------------------------------------------------
    @property
    def shear_force_n(self) -> float:
        return pin_shear_force(self.pin_type, self.pin_count,
                               self.pin_material)

    @property
    def break_force_n(self) -> float:
        """Everything holding the bay shut."""
        return self.shear_force_n + max(0.0, self.friction_n)

    @property
    def required_pressure_pa(self) -> float:
        """Differential pressure the charge has to build, safety included."""
        area = self.bay_area_m2
        if area <= 0:
            return 0.0
        return self.break_force_n * max(1.0, self.safety_factor) / area

    # --- what it takes to build it -------------------------------------------
    @property
    def black_powder_kg(self) -> float:
        """m = P.V / (R.T). Independent of the altitude it fires at.

        Adding gas to a volume raises the pressure by m.R.T/V whatever is
        already in there - partial pressures simply add - so the powder a bay
        needs is set by the pressure RISE and the volume, and not by how high
        up it happens to be. The altitude term that does exist is a help, not
        a cost, and is reported separately below.
        """
        denom = BP_GAS_CONSTANT * BP_GAS_TEMPERATURE_K
        if denom <= 0:
            return 0.0
        return self.required_pressure_pa * self.volume_m3 / denom

    @property
    def black_powder_g(self) -> float:
        return self.black_powder_kg * 1000.0

    @property
    def co2_g(self) -> float:
        """Ideal gas, at the temperature the expanded gas actually arrives at."""
        t = max(1.0, self.co2_temperature_k)
        kg = (self.required_pressure_pa * self.volume_m3 * CO2_MOLAR_MASS
              / (_R_UNIVERSAL * t))
        return kg * 1000.0

    @property
    def co2_cartridge_g(self):
        """The smallest cartridge sold that covers it, or None if none does."""
        need = self.co2_g
        for size in CO2_CARTRIDGES:
            if size >= need:
                return size
        return None

    @property
    def charge_mass_g(self) -> float:
        """Whichever charge this bay is configured to use."""
        if self.charge_type == CHARGE_CO2:
            return self.co2_g
        return self.black_powder_g

    def trapped_air_assist_pa(self, ground_pressure_pa: float,
                              deploy_pressure_pa: float) -> float:
        """Pressure already trying to open the bay, from air sealed in at the pad.

        A bay closed at ground level and flown to altitude carries its ground
        pressure while the air outside falls away, and that difference pushes
        outwards for free. It is reported and never subtracted: the bay cools
        on the way up, which takes some of it back, and any vent hole - and
        there should be a vent hole - gives the rest away. Sizing a charge on
        help that leaks out through a hole drilled on purpose is how a bay
        fails to open.
        """
        return max(0.0, ground_pressure_pa - deploy_pressure_pa)

    # --- the brief -----------------------------------------------------------
    def brief(self, ground_pressure_pa: float = 0.0,
              deploy_pressure_pa: float = 0.0) -> str:
        """The whole sum, in the order it is worked, with the numbers in it."""
        psi = self.required_pressure_pa / 6894.757
        lines = [
            "%s - %.0f mm x %.0f mm bay" % (
                self.name, self.bay_diameter_m * 1000,
                self.bay_length_m * 1000),
            "  free volume          %.0f cm3 (%.1f in3)" % (
                self.volume_m3 * 1e6, self.volume_m3 / 1.6387e-5),
            "  %d x %s             %.0f N to shear (%.0f lbf)" % (
                self.pin_count, self.pin_type, self.shear_force_n,
                self.shear_force_n / 4.44822),
        ]
        if self.friction_n > 0:
            lines.append("  friction allowance   %.0f N" % self.friction_n)
        lines += [
            "  with x%.2f safety     %.0f N over %.0f cm2" % (
                self.safety_factor, self.break_force_n * self.safety_factor,
                self.bay_area_m2 * 1e4),
            "  pressure needed      %.1f kPa (%.1f psi)" % (
                self.required_pressure_pa / 1000.0, psi),
        ]
        if self.charge_type == CHARGE_CO2:
            cart = self.co2_cartridge_g
            lines.append("  CO2 needed           %.2f g at %.0f K"
                         % (self.co2_g, self.co2_temperature_k))
            lines.append("  cartridge            %s"
                         % ("%d g" % cart if cart
                            else "larger than any cartridge sold - split the "
                                 "bay or use black powder"))
            lines.append("  the charge that punctures the cartridge is")
            lines.append("  the device maker's figure, not this one")
        else:
            lines.append("  black powder         %.2f g" % self.black_powder_g)
        if ground_pressure_pa > 0 and deploy_pressure_pa > 0:
            assist = self.trapped_air_assist_pa(ground_pressure_pa,
                                                deploy_pressure_pa)
            lines.append("  air sealed in at the pad is already")
            lines.append("  pushing %.1f kPa (%.1f psi) up here - not"
                         % (assist / 1000.0, assist / 6894.757))
            lines.append("  counted; a vent hole gives it away")
        lines.append("  GROUND TEST THIS. The sum sizes the")
        lines.append("  charge; the test proves the bay opens.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bay_diameter_m": self.bay_diameter_m,
            "bay_length_m": self.bay_length_m,
            "free_volume_m3": self.free_volume_m3,
            "pin_type": self.pin_type,
            "pin_count": self.pin_count,
            "pin_material": self.pin_material,
            "friction_n": self.friction_n,
            "safety_factor": self.safety_factor,
            "charge_type": self.charge_type,
            "co2_temperature_k": self.co2_temperature_k,
        }

    @staticmethod
    def from_dict(data: dict) -> "EjectionCharge":
        charge = EjectionCharge()
        for key, value in (data or {}).items():
            if hasattr(charge, key):
                setattr(charge, key, value)
        return charge
