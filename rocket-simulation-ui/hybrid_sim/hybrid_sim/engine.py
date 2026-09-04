"""
EngineModel: internal ballistics as an ODE, y = [m_ox, T_tank, r_port, m_fuel, Pc].
Adaptive RK45 with terminal events (liquid depleted / fuel out / port at wall).

Physics: Dyer NHNE injector, Marxman regression, CEA c* table with first-order
chamber lag, isentropic nozzle + Summerfield separation.

Component features beyond the v2.0 core, all defaulting to off/neutral so the
baseline results are bit-for-bit unchanged:

  * throat erosion  - the throat opens up during the burn, which drops chamber
    pressure and thrust. Real for graphite and phenolic nozzles.
  * multi-port grains - N ports burning in parallel: more burn area for the
    same length, at the cost of a lower oxidiser flux per port.
  * tank venting - a vent orifice bleeding vapour overboard.

The oxidiser tank is *self-pressurising*: N2O sits on its own saturation
curve, so tank pressure comes from tank temperature, and the tank cools as
liquid boils off to replace what the injector draws. There is no regulator and
no pressurant gas - that is the whole point of flying N2O.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from . import n2o
from .config import Engine, SimConfig, CSTAR_OF, CSTAR_VAL, P_SL

# N2O vapour ratio of specific heats, for choked flow out of the vent.
GAMMA_VAP = 1.27


class EngineModel:
    def __init__(self, eng: Engine, cfg: SimConfig | None = None, Pa: float = P_SL):
        self.e, self.c, self.Pa = eng, cfg or SimConfig(), Pa
        # A per-engine cooling coefficient overrides the solver default, so a
        # small tank and a big one can each decay the way they really do.
        self._cool = (eng.cooling_coeff if getattr(eng, "cooling_coeff", 0.0) > 0
                      else self.c.cooling_coeff)
        self._fuel = eng.fuel_eff
        self._Me_cache = {}
        self._Me = self._exit_mach(eng.eps_exp, eng.gamma)
        # With a fixed exit area, erosion opens the throat and *lowers* the
        # area ratio, so the exit Mach has to be re-solved as the burn goes on.
        self._A_exit_fixed = eng.A_exit
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

    def _exit_mach_cached(self, eps):
        """Exit Mach for an area ratio, memoised on a coarse grid.

        Only exercised when the throat is eroding; without erosion the constant
        self._Me is used and this never runs.
        """
        key = round(float(eps), 4)
        Me = self._Me_cache.get(key)
        if Me is None:
            Me = self._exit_mach(max(1.0001, key), self.e.gamma)
            self._Me_cache[key] = Me
        return Me

    def _throat(self, t):
        """(A_throat, area ratio, exit Mach) at time t, allowing for erosion."""
        if self.e.erosion_rate <= 0:
            return self.e.A_throat, self.e.eps_exp, self._Me
        at = self.e.A_throat_at(t)
        eps = max(1.0001, self._A_exit_fixed/at)
        return at, eps, self._exit_mach_cached(eps)

    def _liquid_mass(self, m_ox, T):
        rl, rv = n2o.rho_l(T), n2o.rho_v(T)
        den = 1/rl - 1/rv
        if abs(den) < 1e-12: return 0.0
        return float(np.clip((self.e.V_tank - m_ox/rv)/den, 0.0, m_ox))

    def _cstar(self, OF):
        of = np.clip(OF + self._fuel.of_shift, CSTAR_OF[0], CSTAR_OF[-1])
        return self._fuel.cstar_scale*float(np.interp(of, CSTAR_OF, CSTAR_VAL))

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

    def _mdot_vent(self, T, Pt):
        """Vapour bled overboard through the vent orifice [kg/s].

        Choked flow, which it is for any vent to atmosphere at N2O tank
        pressures. Zero unless a vent diameter has been set.
        """
        if self.e.d_vent <= 0:
            return 0.0
        g = GAMMA_VAP
        gfun = math.sqrt(g)*(2/(g+1))**((g+1)/(2*(g-1)))
        return (self.e.Cd_vent*self.e.A_vent*Pt*gfun
                / math.sqrt(n2o.RG*float(T)))

    def _cf(self, Pc, eps=None, Me=None):
        if Pc <= 1e4: return 0.0
        g = self.e.gamma
        if eps is None: eps = self.e.eps_exp
        if Me is None: Me = self._Me
        Pe = Pc*(1+(g-1)/2*Me*Me)**(-g/(g-1))
        Pe_eff = max(Pe, self.c.sep_criterion*self.Pa)
        mom = self.e.Gamma_gam*math.sqrt(max(0.0, 2*g/(g-1)*(1-(Pe/Pc)**((g-1)/g))))
        return mom + (Pe_eff - self.Pa)/Pc*eps

    def _pc_target(self, mdot_tot, OF, Pt, At):
        if mdot_tot <= 0: return 0.0
        pc = self.e.eta_cstar*mdot_tot*self._cstar(OF)/At
        return min(pc, Pt*self.c.Pc_to_Ptank_max)

    def _rhs(self, t, y):
        m_ox, T, r_port, m_f, Pc = y
        e, c = self.e, self.c
        At, _, _ = self._throat(t)
        m_l = self._liquid_mass(m_ox, T)
        mdot_ox, Pt = self._mdot_ox(m_l, T, Pc)
        mdot_vent = self._mdot_vent(T, Pt)

        if r_port < e.R_outer and m_f > c.eps_mass and mdot_ox > 0:
            G = mdot_ox/e.A_port(r_port)
            rdot = self._fuel.a*G**self._fuel.n
            mdot_f = self._fuel.rho*e.A_burn(r_port)*rdot
        else:
            rdot = mdot_f = 0.0

        mdot_tot = mdot_ox + mdot_f
        OF = mdot_ox/mdot_f if mdot_f > 1e-9 else 0.0

        if m_l > c.eps_mass:
            # Every kg leaving as vapour - injector draw plus anything vented -
            # has to be boiled, and the latent heat comes out of the liquid.
            dT = -self._cool*(mdot_ox + mdot_vent)*float(n2o.h_v(T))/(m_l*float(n2o.cp_l(T)))
        else:
            dT = 0.0

        dPc = (self._pc_target(mdot_tot, OF, Pt, At) - Pc)/c.tau_chamber
        return [-(mdot_ox + mdot_vent), dT, rdot, -mdot_f, dPc]

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
        keys = ("thrust","mdot_ox","mdot_fuel","OF","cf","P_tank",
                "cstar","G_ox","rdot","m_liquid","m_vapor","mdot_vent",
                "d_throat","eps","P_exit","L_star","t_residence",
                "inj_dP","inj_stiffness","web_left","A_port","expansion_ratio",
                "Isp_inst","c_star_eff","fill_frac","Pc_over_Pt")
        out = {k: np.zeros(n) for k in keys}
        for i in range(n):
            At, eps, Me = self._throat(t[i])
            m_l = self._liquid_mass(m_ox[i], T[i])
            mo, pt = self._mdot_ox(m_l, T[i], Pc[i])
            mv = self._mdot_vent(T[i], pt)
            out["mdot_ox"][i], out["P_tank"][i] = mo, pt
            out["mdot_vent"][i] = mv
            out["m_liquid"][i] = m_l
            out["m_vapor"][i] = max(0.0, m_ox[i] - m_l)
            out["fill_frac"][i] = (m_l/float(n2o.rho_l(T[i]))/e.V_tank
                                   if e.V_tank > 0 else 0.0)

            a_port = e.A_port(r_port[i])
            out["A_port"][i] = a_port
            if r_port[i] < e.R_outer and m_f[i] > c.eps_mass and mo > 0:
                G = mo/a_port
                rd = self._fuel.a*G**self._fuel.n
                out["G_ox"][i] = G
                out["rdot"][i] = rd
                out["mdot_fuel"][i] = self._fuel.rho*e.A_burn(r_port[i])*rd
            out["OF"][i] = mo/out["mdot_fuel"][i] if out["mdot_fuel"][i] > 1e-9 else 0.0
            out["cstar"][i] = self._cstar(out["OF"][i])
            out["c_star_eff"][i] = e.eta_cstar*out["cstar"][i]
            out["cf"][i] = self._cf(Pc[i], eps, Me)
            out["thrust"][i] = max(0.0, e.lambda_div*e.eta_nozzle*out["cf"][i]*Pc[i]*At)

            g = e.gamma
            out["P_exit"][i] = Pc[i]*(1+(g-1)/2*Me*Me)**(-g/(g-1))
            out["d_throat"][i] = e.d_throat_at(t[i])
            out["eps"][i] = eps
            out["expansion_ratio"][i] = eps
            out["L_star"][i] = e.V_chamber(r_port[i])/At if At > 0 else 0.0
            cs = max(1.0, out["cstar"][i])
            out["t_residence"][i] = out["L_star"][i]/cs
            out["inj_dP"][i] = max(0.0, pt - Pc[i])
            out["inj_stiffness"][i] = (out["inj_dP"][i]/Pc[i]) if Pc[i] > 1e4 else 0.0
            out["Pc_over_Pt"][i] = (Pc[i]/pt) if pt > 0 else 0.0
            out["web_left"][i] = max(0.0, e.R_outer - r_port[i])
            mt = mo + out["mdot_fuel"][i]
            out["Isp_inst"][i] = (out["thrust"][i]/(mt*9.80665)) if mt > 1e-9 else 0.0

        out.update(t=t, Pc=Pc, T_tank=T, r_port=r_port, m_ox=m_ox, m_fuel=m_f,
                   mdot_tot=out["mdot_ox"]+out["mdot_fuel"],
                   m_l0=self.m_l0, m_v0=self.m_v0, m_f0=self.m_f0)
        return out
