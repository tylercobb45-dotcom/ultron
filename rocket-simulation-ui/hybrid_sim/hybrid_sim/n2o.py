"""
N2O saturation properties, 200-309.5 K. Closed-form fits (vectorized).

P_sat : Wagner equation (Ferreira & Lobo 2009 coefficients)
rho_l : Wagner-form saturated liquid density (same source)
rho_v : ideal-gas vapor P_sat/(Rg*T)
H_v   : Watson latent-heat correlation
Cp_l  : cubic fit to NIST liquid heat capacity
"""
import numpy as np

PC, TC, RHO_C, RG = 7.245e6, 309.57, 452.0, 188.91
T_FLOOR = 200.0

_PA = (-6.8657, 1.9373, -2.6440, 0.0387); _PP = (1.0, 1.5, 2.5, 5.0)
_RB = (1.72328, -0.8395, 0.5106, -0.10412); _RQ = (1/3, 2/3, 1.0, 4/3)
_CP = (0.004311, -3.2501, 819.213, -66944.4)

def _clamp(T):
    return np.clip(np.asarray(T, dtype=float), T_FLOOR, TC)

def psat(T):
    T = _clamp(T); tau = 1.0 - T/TC
    s = sum(a*tau**p for a, p in zip(_PA, _PP))
    return PC*np.exp((TC/T)*s)

def rho_l(T):
    T = _clamp(T); tau = 1.0 - T/TC
    return RHO_C*np.exp(sum(b*tau**q for b, q in zip(_RB, _RQ)))

def rho_v(T):
    T = _clamp(T)
    return psat(T)/(RG*T)

def h_v(T):
    T = _clamp(T)
    return 140.01*(TC - T)**0.2041*1000.0

def cp_l(T):
    T = _clamp(T); c3, c2, c1, c0 = _CP
    return c3*T**3 + c2*T**2 + c1*T + c0
