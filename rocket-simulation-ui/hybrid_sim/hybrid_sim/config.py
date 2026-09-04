"""Dataclasses + constants for the hybrid engine/flight model.

The Engine dataclass is organised by physical component - tank, injector,
fuel grain / combustion chamber, nozzle - because that is how the hardware is
actually built and bought, and how the Engine Lab presents it.

Every field added beyond the original v2.0 set defaults to a value that
reproduces the original behaviour exactly, so existing configurations and the
validation suite are unaffected until a field is deliberately changed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np

G0 = 9.80665
R_UNIV = 8314.46
R_AIR, GAMMA_AIR = 287.058, 1.4
P_SL, T_SL = 101325.0, 288.15
LAPSE, T_TROP, BARO_EXP = 0.0065, 216.65, 5.2561

@dataclass(frozen=True)
class Fuel:
    name: str
    rho: float
    a: float          # Marxman rdot = a*G_ox^n  (SI)
    n: float
    of_shift: float = 0.0
    cstar_scale: float = 1.0

FUELS = {
    "HTPB":     Fuel("HTPB",     920, 4.5e-5, 0.681,  0.0, 1.00),
    "Paraffin": Fuel("Paraffin", 900, 1.55e-4, 0.5,   0.0, 1.00),
    "ABS":      Fuel("ABS",     1040, 6.5e-5, 0.498, -1.0, 0.99),
    "HDPE":     Fuel("HDPE",     960, 5.5e-5, 0.5,    0.5, 1.00),
    "PMMA":     Fuel("PMMA",    1180, 4.5e-5, 0.5,   -1.5, 0.96),
    "Sorbitol": Fuel("Sorbitol",1490, 6.5e-5, 0.5,   -1.5, 0.96),
    "Nylon":    Fuel("Nylon",   1140, 6.0e-5, 0.5,   -1.5, 0.98),
    "PLA":      Fuel("PLA",     1240, 5.0e-5, 0.5,   -2.0, 0.96),
}

# c* vs O/F, HTPB/N2O baseline (NASA CEA)
CSTAR_OF = np.array([1,1.5,2,2.5,3,3.5,4,4.5,5,5.5,6,6.5,7,7.5,
                     8,8.5,9,9.5,10,11,12,14,16,20], float)
CSTAR_VAL = np.array([1100,1230,1310,1395,1465,1520,1560,1590,1605,1620,1630,
                      1625,1610,1595,1575,1555,1530,1510,1485,1430,1380,1290,
                      1210,1080], float)

# Injector styles. The number is a typical discharge coefficient; it is only a
# starting point, the Cd_inj field is what the model actually uses.
INJECTOR_TYPES = {
    "Single orifice": (0.70, "One drilled orifice. What the HyperTEK injector "
                             "bell uses, with interchangeable inserts."),
    "Showerhead":     (0.75, "A plate of straight parallel holes. Simple, "
                             "poor atomisation, needs a longer chamber."),
    "Pintle":         (0.65, "Central post metering an annular sheet. Deep "
                             "throttling, good mixing."),
    "Swirl":          (0.45, "Tangential entry spins the liquid into a hollow "
                             "cone. Best atomisation, lowest Cd."),
    "Impinging":      (0.80, "Paired jets collide and shatter. Good mixing, "
                             "sensitive to drill alignment."),
}


@dataclass
class Engine:
    # --- oxidiser tank ------------------------------------------------------
    # N2O is self-pressurising: the tank sits on its own saturation curve, so
    # tank pressure is set by temperature, not by a regulator. Filling warm
    # gives ~700 psi and a hot start; the tank then cools as it empties.
    d_tank: float = 0.100          # tank internal diameter [m]
    L_tank: float = 1.019          # tank internal length [m]
    fill_frac: float = 0.85        # liquid volume fraction at ignition
    T_tank_0: float = 293.0        # initial tank temperature [K]
    d_vent: float = 0.0            # vent orifice diameter [m]; 0 = vent closed
    Cd_vent: float = 0.65          # vent discharge coefficient
    # Effective evaporative cooling: the fraction of the boil-off latent heat
    # that actually comes out of the liquid rather than being fed back by the
    # tank walls and the surrounding air. It sets how fast the tank cools, and
    # therefore how steeply the blowdown decays - a small tank has a lot of
    # wall area per kg of liquid, so it holds pressure up better than a big
    # one. 0 means "use the solver default" (0.16, calibrated on an 8 L tank).
    cooling_coeff: float = 0.0

    # --- injector -----------------------------------------------------------
    inj_type: str = "Showerhead"
    n_holes: int = 4
    d_hole: float = 0.00252        # per-hole diameter [m]
    Cd_inj: float = 0.7

    # --- fuel grain / combustion chamber ------------------------------------
    fuel: Fuel = field(default_factory=lambda: FUELS["HTPB"])
    # Regression-law overrides for the chosen fuel: rdot = a * G_ox^n.
    # A real grain's coefficients depend on the formulation, the binder, any
    # additives and how it was made, and manufacturers do not publish them for
    # proprietary fuels. 0 means "use the value from the named fuel".
    fuel_a: float = 0.0
    fuel_n: float = 0.0
    L_grain: float = 0.30
    d_grain_outer: float = 0.076
    d_port_0: float = 0.036
    n_ports: int = 1               # multi-port grain: ports burning in parallel
    L_pre: float = 0.0             # pre-combustion chamber length [m]
    L_post: float = 0.0            # post-combustion chamber length [m]

    # --- nozzle -------------------------------------------------------------
    d_throat: float = 0.018
    eps_exp: float = 5.0           # Ae/At
    alpha_deg: float = 15.0        # divergence half angle
    beta_conv_deg: float = 30.0    # convergence half angle (geometry only)
    erosion_rate: float = 0.0      # throat *radius* growth [m/s]; 0 = none
    eta_cstar: float = 0.90
    eta_nozzle: float = 0.95

    # --- combustion gas -----------------------------------------------------
    gamma: float = 1.22
    MW: float = 26.0

    # --- tank geometry ------------------------------------------------------
    @property
    def V_tank(self): return math.pi*(self.d_tank/2)**2*self.L_tank
    @property
    def V_tank_cc(self): return self.V_tank*1e6
    @property
    def A_vent(self): return math.pi*(self.d_vent/2)**2

    # --- injector geometry --------------------------------------------------
    @property
    def A_inj(self): return self.n_holes*math.pi*(self.d_hole/2)**2
    @property
    def CdA_inj(self): return self.Cd_inj*self.A_inj

    # --- grain geometry -----------------------------------------------------
    @property
    def R_outer(self): return self.d_grain_outer/2
    @property
    def r_port_0(self): return self.d_port_0/2
    @property
    def A_port_0(self): return self.n_ports*math.pi*self.r_port_0**2
    @property
    def web_0(self):
        """Initial fuel web thickness [m] - how much wall there is to burn."""
        return self.R_outer - self.r_port_0

    def A_port(self, r_port):
        """Total flow area of the port(s) at radius r_port [m^2]."""
        return self.n_ports*math.pi*r_port*r_port

    def A_burn(self, r_port):
        """Total burning surface at radius r_port [m^2]."""
        return self.n_ports*2*math.pi*r_port*self.L_grain

    def V_chamber(self, r_port):
        """Gas volume in the chamber at port radius r_port [m^3].

        The port itself plus any pre- and post-combustion chamber. Used for
        L* and gas residence time.
        """
        A_case = math.pi*self.R_outer**2
        return self.A_port(r_port)*self.L_grain + A_case*(self.L_pre + self.L_post)

    def L_star(self, r_port=None):
        """Characteristic length V_c/At [m] - the classic chamber sizing number."""
        r = self.r_port_0 if r_port is None else r_port
        at = self.A_throat
        return self.V_chamber(r)/at if at > 0 else 0.0

    # --- nozzle geometry ----------------------------------------------------
    @property
    def A_throat(self): return math.pi*(self.d_throat/2)**2
    @property
    def A_exit(self): return self.A_throat*self.eps_exp
    @property
    def d_exit(self): return self.d_throat*math.sqrt(self.eps_exp)

    def d_throat_at(self, t):
        """Throat diameter after t seconds of erosion [m]."""
        if self.erosion_rate <= 0:
            return self.d_throat
        return self.d_throat + 2.0*self.erosion_rate*max(0.0, t)

    def A_throat_at(self, t):
        """Throat area after t seconds of erosion [m^2]."""
        d = self.d_throat_at(t)
        return math.pi*(d/2)**2

    # --- gas ----------------------------------------------------------------
    @property
    def R_gas(self): return R_UNIV/self.MW
    @property
    def lambda_div(self): return (1+math.cos(math.radians(self.alpha_deg)))/2
    @property
    def Gamma_gam(self):
        g = self.gamma
        return math.sqrt(g)*(2/(g+1))**((g+1)/(2*(g-1)))

    @property
    def fuel_eff(self) -> Fuel:
        """The fuel actually used, with any regression overrides applied."""
        if self.fuel_a <= 0 and self.fuel_n <= 0:
            return self.fuel
        f = self.fuel
        return Fuel(f.name, f.rho,
                    self.fuel_a if self.fuel_a > 0 else f.a,
                    self.fuel_n if self.fuel_n > 0 else f.n,
                    f.of_shift, f.cstar_scale)

    def m_fuel_0(self):
        """Loaded fuel mass [kg]."""
        return (self.fuel.rho*self.n_ports
                * math.pi*(self.R_outer**2/self.n_ports - self.r_port_0**2)
                * self.L_grain) if self.n_ports > 1 else (
                self.fuel.rho*math.pi*(self.R_outer**2-self.r_port_0**2)*self.L_grain)

@dataclass
class Rocket:
    m_dry: float = 20.0
    Cd_body: float = 1.625
    d_body: float = 0.14
    Cd_drogue: float = 0.8
    d_drogue: float = 0.892
    Cd_main: float = 1.5
    d_main: float = 2.61
    h_main_ft: float = 1000.0
    @property
    def CdA_body(self): return self.Cd_body*math.pi*(self.d_body/2)**2
    @property
    def CdA_drogue(self): return self.Cd_drogue*math.pi*(self.d_drogue/2)**2
    @property
    def CdA_main(self): return self.Cd_main*math.pi*(self.d_main/2)**2

@dataclass
class SimConfig:
    kappa: float = 1.0             # Dyer NHNE weight (saturated tank -> 50/50)
    Pc_to_Ptank_max: float = 0.99
    cooling_coeff: float = 0.16    # calibrated effective evaporative cooling
    tau_chamber: float = 0.05      # chamber lag [s] (realistic L* residence scale)
    sep_criterion: float = 0.4     # Summerfield
    eps_mass: float = 1e-3
    eps_liq_dead: float = 0.05
    t_max: float = 60.0
    rtol: float = 1e-6
    atol: float = 1e-8
    max_step: float = 0.05
