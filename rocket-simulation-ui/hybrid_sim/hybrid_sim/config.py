"""Dataclasses + constants for the hybrid engine/flight model."""
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

@dataclass
class Engine:
    d_tank: float
    L_tank: float
    fill_frac: float = 0.85
    T_tank_0: float = 293.0
    n_holes: int = 4
    d_hole: float = 0.00252
    Cd_inj: float = 0.7
    fuel: Fuel = field(default_factory=lambda: FUELS["HTPB"])
    L_grain: float = 0.30
    d_grain_outer: float = 0.076
    d_port_0: float = 0.036
    d_throat: float = 0.018
    eps_exp: float = 5.0
    alpha_deg: float = 15.0
    eta_cstar: float = 0.90
    eta_nozzle: float = 0.95
    gamma: float = 1.22
    MW: float = 26.0

    @property
    def V_tank(self): return math.pi*(self.d_tank/2)**2*self.L_tank
    @property
    def A_inj(self): return self.n_holes*math.pi*(self.d_hole/2)**2
    @property
    def R_outer(self): return self.d_grain_outer/2
    @property
    def r_port_0(self): return self.d_port_0/2
    @property
    def A_throat(self): return math.pi*(self.d_throat/2)**2
    @property
    def A_exit(self): return self.A_throat*self.eps_exp
    @property
    def R_gas(self): return R_UNIV/self.MW
    @property
    def lambda_div(self): return (1+math.cos(math.radians(self.alpha_deg)))/2
    @property
    def Gamma_gam(self):
        g = self.gamma
        return math.sqrt(g)*(2/(g+1))**((g+1)/(2*(g-1)))
    def m_fuel_0(self):
        return self.fuel.rho*math.pi*(self.R_outer**2-self.r_port_0**2)*self.L_grain

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
