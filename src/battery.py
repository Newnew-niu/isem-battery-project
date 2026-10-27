"""Daily battery arbitrage optimisation (DAM only) and a rule-based simulator.

Per trading day, with hourly prices p_t (EUR/MWh), dt = 1 h:
    max  sum_t p_t (d_t - c_t) dt
    s.t. soc_t = soc_{t-1} + eta_c c_t dt - d_t dt / eta_d
         soc_min <= soc_t <= soc_max,   soc_0 = soc_T = soc_min
         0 <= c_t <= P u_t,  0 <= d_t <= P (1 - u_t),  u_t binary   (no simultaneous charge/discharge)
         sum_t d_t dt / eta_d <= soc_max - soc_min                  (at most one full cycle per day)
with eta_c = eta_d = sqrt(round-trip efficiency). See README for the full formulation.
"""
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class Battery:
    power_mw: float = 1.0
    energy_mwh: float = 2.0
    rte: float = 0.88
    soc_min_frac: float = 0.05
    soc_max_frac: float = 0.95
    cycles_per_day: float = 1.0

    @property
    def eta(self):
        return np.sqrt(self.rte)

    @property
    def soc_min(self):
        return self.soc_min_frac * self.energy_mwh

    @property
    def soc_max(self):
        return self.soc_max_frac * self.energy_mwh

    @property
    def usable(self):
        return self.soc_max - self.soc_min


def optimise_day(prices, b: Battery, dt=1.0):
    """Return (charge, discharge, soc) arrays in MW / MWh that maximise revenue at `prices`.

    Solved with HiGHS via scipy.optimize.milp. Variables x = [c (T), d (T), s (T), u (T)].
    """
    p = np.asarray(prices, dtype=float)
    T = len(p)
    n = 4 * T
    ic, id_, is_, iu = 0, T, 2 * T, 3 * T
    cost = np.zeros(n)
    cost[ic:ic + T] = p * dt      # milp minimises: -revenue = sum p c - p d
    cost[id_:id_ + T] = -p * dt
    rows, lo, hi = [], [], []

    def row():
        r = np.zeros(n)
        rows.append(r)
        return r

    for t in range(T):  # SoC dynamics: s_t - s_{t-1} - eta c_t dt + d_t dt / eta = 0 (s_{-1} = soc_min)
        r = row()
        r[is_ + t] = 1
        if t:
            r[is_ + t - 1] = -1
        r[ic + t] = -b.eta * dt
        r[id_ + t] = dt / b.eta
        lo.append(b.soc_min if t == 0 else 0)
        hi.append(b.soc_min if t == 0 else 0)
    for t in range(T):  # c_t <= P u_t ;  d_t <= P (1 - u_t)
        r = row(); r[ic + t] = 1; r[iu + t] = -b.power_mw; lo.append(-np.inf); hi.append(0)
        r = row(); r[id_ + t] = 1; r[iu + t] = b.power_mw; lo.append(-np.inf); hi.append(b.power_mw)
    r = row(); r[id_:id_ + T] = dt / b.eta; lo.append(-np.inf); hi.append(b.cycles_per_day * b.usable)
    r = row(); r[is_ + T - 1] = 1; lo.append(b.soc_min); hi.append(b.soc_min)

    bounds_lo = np.r_[np.zeros(2 * T), np.full(T, b.soc_min), np.zeros(T)]
    bounds_hi = np.r_[np.full(2 * T, b.power_mw), np.full(T, b.soc_max), np.ones(T)]
    integrality = np.r_[np.zeros(3 * T), np.ones(T)]
    res = milp(cost, constraints=LinearConstraint(np.array(rows), lo, hi), integrality=integrality,
               bounds=Bounds(bounds_lo, bounds_hi))
    if not res.success:
        raise RuntimeError(res.message)
    x = res.x
    c, d = x[ic:ic + T], x[id_:id_ + T]
    return np.where(c < 1e-9, 0, c), np.where(d < 1e-9, 0, d), x[is_:is_ + T]


def simulate_rule(charge_hours, discharge_hours, hours, b: Battery, dt=1.0):
    """Charge at full power in `charge_hours`, discharge in `discharge_hours` (local hours),
    respecting SoC limits, starting and ending the day at soc_min."""
    T = len(hours)
    c, d, s = np.zeros(T), np.zeros(T), np.zeros(T)
    soc = b.soc_min
    for t, h in enumerate(hours):
        if h in charge_hours:
            c[t] = min(b.power_mw, (b.soc_max - soc) / (b.eta * dt))
            soc += b.eta * c[t] * dt
        elif h in discharge_hours:
            d[t] = min(b.power_mw, (soc - b.soc_min) * b.eta / dt)
            soc -= d[t] * dt / b.eta
        s[t] = soc
    # Any energy left (discharge hours earlier than charge hours) stays stored but is not sold:
    # the rule is evaluated as specified, without a corrective trade.
    return c, d, s


def revenue(prices, c, d, dt=1.0):
    return float(np.sum(np.asarray(prices) * (d - c) * dt))
