"""U.S. Standard Atmosphere (1976) to 86 km, with launch-site conditions.

The simulator previously modelled the troposphere properly and then fell back
to a single exponential above 11 km with the temperature frozen at 216.65 K.
That is fine to about 20 km and wrong above it - the stratosphere warms back
up, which changes density, speed of sound, and therefore every Mach number in
the flight. A 50,000 ft (15 km) shot already sits near the edge of that, and
anything reaching Mach 5 goes well past it.

This module implements the full seven-layer profile, plus a LaunchSite that
shifts the whole column to match conditions on the day (field elevation,
temperature, barometric pressure, humidity, wind).

Qt-free and importable on its own so it can be tested headlessly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

G0 = 9.80665           # standard gravity [m/s^2]
R_AIR = 287.05287      # specific gas constant, dry air [J/(kg K)]
GAMMA = 1.4            # ratio of specific heats
R_UNIVERSAL = 8314.32
M_AIR = 28.9644
R_EARTH = 6356766.0    # ISA effective Earth radius [m]

# Sutherland's law constants for air
_MU0, _T_MU0, _S_MU = 1.716e-5, 273.15, 110.4

# (base geopotential altitude [m], lapse rate [K/m], base temperature [K])
# Lapse is dT/dh; negative means cooling with altitude.
_LAYERS = [
    (0.0,      -0.0065, 288.15),
    (11000.0,   0.0,    216.65),
    (20000.0,   0.0010, 216.65),
    (32000.0,   0.0028, 228.65),
    (47000.0,   0.0,    270.65),
    (51000.0,  -0.0028, 270.65),
    (71000.0,  -0.0020, 214.65),
]
_TOP = 84852.0   # top of the modelled column [m geopotential]

# Base pressures at each layer boundary, built once from the sea-level value.
_P_SL = 101325.0


def _build_base_pressures():
    pressures = [_P_SL]
    for i in range(len(_LAYERS) - 1):
        h0, lapse, t0 = _LAYERS[i]
        h1 = _LAYERS[i + 1][0]
        p0 = pressures[i]
        if lapse == 0.0:
            p1 = p0 * math.exp(-G0 * (h1 - h0) / (R_AIR * t0))
        else:
            t1 = t0 + lapse * (h1 - h0)
            p1 = p0 * (t1 / t0) ** (-G0 / (R_AIR * lapse))
        pressures.append(p1)
    return pressures


_P_BASE = _build_base_pressures()


def geopotential(z: float) -> float:
    """Geometric altitude [m] -> geopotential altitude [m]."""
    return R_EARTH * z / (R_EARTH + z)


def viscosity(T: float) -> float:
    """Dynamic viscosity of air [Pa s] from Sutherland's law."""
    return _MU0 * (T / _T_MU0) ** 1.5 * (_T_MU0 + _S_MU) / (T + _S_MU)


def properties(z: float):
    """Standard atmosphere at geometric altitude z [m].

    Returns (T [K], P [Pa], rho [kg/m^3], a [m/s], mu [Pa s]).
    """
    h = geopotential(max(0.0, z))
    h = min(h, _TOP)
    idx = 0
    for i, (h0, _lapse, _t0) in enumerate(_LAYERS):
        if h >= h0:
            idx = i
        else:
            break
    h0, lapse, t0 = _LAYERS[idx]
    p0 = _P_BASE[idx]
    if lapse == 0.0:
        T = t0
        P = p0 * math.exp(-G0 * (h - h0) / (R_AIR * t0))
    else:
        T = t0 + lapse * (h - h0)
        P = p0 * (T / t0) ** (-G0 / (R_AIR * lapse))
    rho = P / (R_AIR * T)
    return T, P, rho, math.sqrt(GAMMA * R_AIR * T), viscosity(T)


@dataclass
class LaunchSite:
    """Conditions at the pad, and the wind the vehicle flies through.

    The standard column is shifted so that it matches the measured ground
    temperature and pressure at field elevation, rather than assuming a
    sea-level standard day.
    """
    name: str = "Standard day, sea level"
    elevation_m: float = 0.0          # field elevation above sea level
    latitude_deg: float = 28.5        # only used for the gravity model
    temperature_c: float = 15.0       # air temperature at the pad
    pressure_pa: float = 101325.0     # station pressure at the pad
    humidity_pct: float = 0.0         # relative humidity
    wind_speed_ms: float = 0.0        # steady wind at the pad
    wind_dir_deg: float = 0.0         # direction the wind blows toward
    wind_shear_exp: float = 0.143     # power-law exponent (1/7 open terrain)
    wind_ref_height_m: float = 10.0   # height the pad wind was measured at
    rail_length_m: float = 5.18
    rail_angle_deg: float = 0.0       # tilt from vertical

    # --- derived -----------------------------------------------------------
    @property
    def temperature_k(self) -> float:
        return self.temperature_c + 273.15

    def gravity(self, z: float = 0.0) -> float:
        """Latitude- and altitude-corrected gravity [m/s^2]."""
        phi = math.radians(self.latitude_deg)
        g_surface = 9.780327 * (1 + 0.0053024 * math.sin(phi) ** 2
                                - 0.0000058 * math.sin(2 * phi) ** 2)
        r = R_EARTH + self.elevation_m + max(0.0, z)
        return g_surface * (R_EARTH / r) ** 2

    def _offsets(self):
        """Temperature and pressure multipliers that pin the standard column
        to the measured conditions at field elevation."""
        T_std, P_std, _, _, _ = properties(self.elevation_m)
        dT = self.temperature_k - T_std
        p_ratio = self.pressure_pa / P_std if P_std > 0 else 1.0
        return dT, p_ratio

    def properties(self, altitude_agl: float):
        """Atmosphere at altitude above ground level.

        Returns (T [K], P [Pa], rho [kg/m^3], a [m/s], mu [Pa s]).
        """
        z = self.elevation_m + max(0.0, altitude_agl)
        T, P, _rho, _a, _mu = properties(z)
        dT, p_ratio = self._offsets()
        # The temperature offset decays with height: a hot day at the surface
        # does not shift the stratosphere with it.
        blend = math.exp(-max(0.0, altitude_agl) / 8000.0)
        T_eff = max(150.0, T + dT * blend)
        P_eff = P * p_ratio
        rho = P_eff / (R_AIR * T_eff)
        if self.humidity_pct > 0:
            rho = _humid_density(P_eff, T_eff, self.humidity_pct)
        return T_eff, P_eff, rho, math.sqrt(GAMMA * R_AIR * T_eff), viscosity(T_eff)

    def wind_at(self, altitude_agl: float):
        """Steady wind vector [m/s] at altitude, as (east, north).

        Uses the standard power-law shear profile, so wind grows with height
        above the measured reference.
        """
        if self.wind_speed_ms <= 0:
            return 0.0, 0.0
        h = max(self.wind_ref_height_m, altitude_agl)
        speed = self.wind_speed_ms * (h / self.wind_ref_height_m) ** self.wind_shear_exp
        # Cap the profile; the power law is not meant for the whole column.
        speed = min(speed, self.wind_speed_ms * 4.0)
        theta = math.radians(self.wind_dir_deg)
        return speed * math.sin(theta), speed * math.cos(theta)

    def wind_speed_at(self, altitude_agl: float) -> float:
        e, n = self.wind_at(altitude_agl)
        return math.hypot(e, n)


def _humid_density(P: float, T: float, rh_pct: float) -> float:
    """Density of moist air. Water vapour is lighter than dry air, so humidity
    lowers density slightly - worth a fraction of a percent on a hot day."""
    rh = max(0.0, min(100.0, rh_pct)) / 100.0
    # Tetens saturation vapour pressure [Pa]
    t_c = T - 273.15
    p_sat = 610.78 * math.exp(17.27 * t_c / (t_c + 237.3)) if t_c > -50 else 0.0
    p_v = rh * p_sat
    p_d = max(0.0, P - p_v)
    return (p_d / (R_AIR * T)) + (p_v / (461.495 * T))


# Convenience for callers that only want density, matching the old helper.
def density(z: float) -> float:
    return properties(z)[2]
