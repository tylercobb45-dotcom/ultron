"""
EngineModel: internal ballistics as an ODE, y = [m_ox, T_tank, r_port, m_fuel, Pc].
Adaptive RK45 with terminal events (liquid depleted / fuel out / port at wall).

Physics: Dyer NHNE injector, Marxman regression, CEA c* table with first-order
chamber lag, isentropic nozzle + Summerfield separation.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from . import n2o
from .config import Engine, SimConfig, CSTAR_OF, CSTAR_VAL, P_SL


class EngineModel:
    def __init__(self, eng: Engine, cfg: SimConfig | None = None, Pa: float = P_SL):
        self.e, self.c, self.Pa = eng, cfg or SimConfig(), Pa
        self._Me = self._exit_mach(eng.eps_exp, eng.gamma)
        rl = float(n2o.rho_l(eng.T_tank_0)); rv = float(n2o.rho_v(eng.T_tank_0))
        self.m_l0 = eng.fill_frac*eng.V_tank*rl
        self.m_v0 = (1-eng.fill_frac)*eng.V_tank*rv
        self.m_ox0 = self.m_l0 + self.m_v0
        self.m_f0 = eng.m_fuel_0()

    @staticmethod
    def _exit_mach(eps, g):
        def ar(M):
            return (1/M)*((2/(g+1))*(1+(g-1)/2*M*M))**((g+1)/(2*(g-1)))
        return brentq(lambda M: ar(M)-eps, 1+1e-9, 50.0)

    def _liquid_mass(self, m_ox, T):
        rl, rv = n2o.rho_l(T), n2o.rho_v(T)
        den = 1/rl - 1/rv
        if abs(den) < 1e-12: return 0.0
        return float(np.clip((self.e.V_tank - m_ox/rv)/den, 0.0, m_ox))

    def _cstar(self, OF):
        of = np.clip(OF + self.e.fuel.of_shift, CSTAR_OF[0], CSTAR_OF[-1])
        return self.e.fuel.cstar_scale*float(np.interp(of, CSTAR_OF, CSTAR_VAL))

    def _mdot_ox(self, m_l, T, Pc):
        if m_l <= self.c.eps_mass: return 0.0, float(n2o.psat(T))
        Pt = float(n2o.psat(T))
        Pc_eff = min(Pc, Pt*self.c.Pc_to_Ptank_max)
        dP = max(0.0, Pt - Pc_eff)
        rl, rv, hv = float(n2o.rho_l(T)), float(n2o.rho_v(T)), float(n2o.h_v(T))
        CdA = self.e.Cd_inj*self.e.A_inj
        spi = CdA*math.sqrt(2*rl*dP)
        hem = CdA*rv*math.sqrt(2*hv*max(0.0, 1 - Pc_eff/Pt))
        w = 1.0/(1.0 + self.c.kappa)
        return (1-w)*spi + w*hem, Pt

    def _cf(self, Pc):
        if Pc <= 1e4: return 0.0
        g, Me = self.e.gamma, self._Me
        Pe = Pc*(1+(g-1)/2*Me*Me)**(-g/(g-1))
        Pe_eff = max(Pe, self.c.sep_criterion*self.Pa)
        mom = self.e.Gamma_gam*math.sqrt(max(0.0, 2*g/(g-1)*(1-(Pe/Pc)**((g-1)/g))))
        return mom + (Pe_eff - self.Pa)/Pc*self.e.eps_exp

    def _pc_target(self, mdot_tot, OF, Pt):
        if mdot_tot <= 0: return 0.0
        pc = self.e.eta_cstar*mdot_tot*self._cstar(OF)/self.e.A_throat
        return min(pc, Pt*self.c.Pc_to_Ptank_max)

    def _rhs(self, t, y):
        m_ox, T, r_port, m_f, Pc = y
        e, c = self.e, self.c
        m_l = self._liquid_mass(m_ox, T)
        mdot_ox, Pt = self._mdot_ox(m_l, T, Pc)

        if r_port < e.R_outer and m_f > c.eps_mass and mdot_ox > 0:
            G = mdot_ox/(math.pi*r_port*r_port)
            rdot = e.fuel.a*G**e.fuel.n
            mdot_f = 2*math.pi*e.fuel.rho*e.L_grain*r_port*rdot
        else:
            rdot = mdot_f = 0.0

        mdot_tot = mdot_ox + mdot_f
        OF = mdot_ox/mdot_f if mdot_f > 1e-9 else 0.0

        if m_l > c.eps_mass:
            dT = -c.cooling_coeff*mdot_ox*float(n2o.h_v(T))/(m_l*float(n2o.cp_l(T)))
        else:
            dT = 0.0

        dPc = (self._pc_target(mdot_tot, OF, Pt) - Pc)/c.tau_chamber
        return [-mdot_ox, dT, rdot, -mdot_f, dPc]

    def _events(self):
        c = self.c
        dead = c.eps_liq_dead*self.m_l0
        def liq(t, y): return self._liquid_mass(y[0], y[1]) - dead
        def fuel(t, y): return y[3] - c.eps_mass
        def wall(t, y): return self.e.R_outer - y[2]
        for f in (liq, fuel, wall): f.terminal, f.direction = True, -1
        return [liq, fuel, wall]

    def run(self, n_out: int = 600):
        e, c = self.e, self.c
        y0 = [self.m_ox0, e.T_tank_0, e.r_port_0, self.m_f0, P_SL]
        sol = solve_ivp(self._rhs, (0.0, c.t_max), y0, method="RK45",
                        events=self._events(), rtol=c.rtol, atol=c.atol,
                        max_step=c.max_step, dense_output=True)
        t = np.linspace(0.0, sol.t[-1], n_out)
        return self._post(t, sol.sol(t))

    def _post(self, t, Y):
        e, c = self.e, self.c
        m_ox, T, r_port, m_f, Pc = Y
        n = len(t)
        out = {k: np.zeros(n) for k in
               ("thrust","mdot_ox","mdot_fuel","OF","cf","P_tank")}
        for i in range(n):
            m_l = self._liquid_mass(m_ox[i], T[i])
            mo, pt = self._mdot_ox(m_l, T[i], Pc[i])
            out["mdot_ox"][i], out["P_tank"][i] = mo, pt
            if r_port[i] < e.R_outer and m_f[i] > c.eps_mass and mo > 0:
                G = mo/(math.pi*r_port[i]**2)
                out["mdot_fuel"][i] = 2*math.pi*e.fuel.rho*e.L_grain*r_port[i]*e.fuel.a*G**e.fuel.n
            out["OF"][i] = mo/out["mdot_fuel"][i] if out["mdot_fuel"][i] > 1e-9 else 0.0
            out["cf"][i] = self._cf(Pc[i])
            out["thrust"][i] = max(0.0, e.lambda_div*e.eta_nozzle*out["cf"][i]*Pc[i]*e.A_throat)
        out.update(t=t, Pc=Pc, T_tank=T, r_port=r_port, m_ox=m_ox, m_fuel=m_f,
                   mdot_tot=out["mdot_ox"]+out["mdot_fuel"],
                   m_l0=self.m_l0, m_v0=self.m_v0, m_f0=self.m_f0)
        return out
