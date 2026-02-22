"""
IRR (Internal Rate of Return) calculation engine.

Uses bisection method - no scipy/numpy dependencies needed.
Supports two scenarios: no-withdrawal and with-withdrawal.
"""
from typing import List, Optional, Tuple, Dict
from .config import PolicyConfig


def npv(rate: float, cashflows: List[float], times: List[float]) -> float:
    """Calculate Net Present Value at a given discount rate.

    NPV = sum(cf_i / (1+rate)^t_i) for all i
    """
    if rate <= -1.0:
        return float('inf')
    total = 0.0
    for cf, t in zip(cashflows, times):
        total += cf / ((1 + rate) ** t)
    return total


def calculate_irr(
    cashflows: List[float],
    times: List[float],
    lo: float = -0.99,
    hi: float = 5.0,
    tol: float = 1e-10,
    max_iter: int = 2000,
) -> Optional[float]:
    """Calculate IRR using bisection method.

    Returns None if no solution found (e.g., all cashflows are negative).

    Args:
        cashflows: List of cash flows (negative = outflow, positive = inflow)
        times: Corresponding time points for each cash flow
        lo: Lower bound for rate search (default -0.99 = -99%)
        hi: Upper bound for rate search (default 5.0 = 500%)
        tol: Convergence tolerance
        max_iter: Maximum iterations

    Returns:
        IRR as a decimal (e.g., 0.05 = 5%), or None if no solution
    """
    # Check if there's any positive cash flow
    if all(cf <= 0 for cf in cashflows):
        return None

    # Check if there's any negative cash flow
    if all(cf >= 0 for cf in cashflows):
        return None

    # Evaluate NPV at boundaries
    npv_lo = npv(lo, cashflows, times)
    npv_hi = npv(hi, cashflows, times)

    # If NPV at both ends has the same sign, try to expand the bracket
    if npv_lo * npv_hi > 0:
        # Try expanding hi
        for test_hi in [10.0, 50.0, 100.0]:
            npv_test = npv(test_hi, cashflows, times)
            if npv_lo * npv_test < 0:
                hi = test_hi
                npv_hi = npv_test
                break
        else:
            # Try with a smaller lo
            for test_lo in [-0.999, -0.9999]:
                npv_test = npv(test_lo, cashflows, times)
                if npv_test * npv_hi < 0:
                    lo = test_lo
                    npv_lo = npv_test
                    break
            else:
                return None

    # Bisection
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        npv_mid = npv(mid, cashflows, times)

        if abs(npv_mid) < tol:
            return mid

        if npv_lo * npv_mid < 0:
            hi = mid
            npv_hi = npv_mid
        else:
            lo = mid
            npv_lo = npv_mid

        if abs(hi - lo) < tol:
            return (lo + hi) / 2.0

    return (lo + hi) / 2.0


def build_cashflows_no_withdrawal(
    year: int,
    annual_premium: float,
    payment_years: int,
    surrender_value: float,
) -> Tuple[List[float], List[float]]:
    """Build cash flow vector for no-withdrawal IRR at a given year.

    Convention:
    - Premium paid at t=0, 1, ..., min(year, payment_years)-1
    - Surrender value received at t=year
    """
    n_premiums = min(year, payment_years)
    cashflows = [-annual_premium] * n_premiums + [surrender_value]
    times = list(range(n_premiums)) + [year]
    return cashflows, times


def build_cashflows_with_withdrawal(
    year: int,
    annual_premium: float,
    payment_years: int,
    withdrawal_data: list,
    remaining_surrender: float,
) -> Tuple[List[float], List[float]]:
    """Build cash flow vector for withdrawal scenario IRR at a given year.

    Convention:
    - Premium paid at t=0, 1, ..., min(year, payment_years)-1
    - Withdrawals received at t=withdrawal_year for each year with amount > 0
    - At t=year: remaining surrender value + year's withdrawal amount

    The withdrawal_data list provides each year's withdrawal amount.
    No hardcoded start year or amount - fully data-driven.
    """
    n_premiums = min(year, payment_years)
    cashflows = [-annual_premium] * n_premiums
    times = list(range(n_premiums))

    # Build a dict: year -> withdrawal_amount
    withdrawal_by_year = {}
    for wd in withdrawal_data:
        if wd.year <= year and wd.withdrawal_amount > 0:
            withdrawal_by_year[wd.year] = wd.withdrawal_amount

    # Add past withdrawal cash flows (years before the current year)
    for wd_year in sorted(withdrawal_by_year.keys()):
        if wd_year < year:
            cashflows.append(withdrawal_by_year[wd_year])
            times.append(wd_year)

    # Final year: withdrawal + remaining surrender
    final_withdrawal = withdrawal_by_year.get(year, 0)
    final_cashflow = final_withdrawal + remaining_surrender
    cashflows.append(final_cashflow)
    times.append(year)

    return cashflows, times


def calculate_all_irr(config: PolicyConfig) -> List[Dict]:
    """Calculate IRR for every year, both scenarios.

    Returns list of dicts, one per year:
    {
        'year': int,
        'age': int,
        'irr_no_withdrawal_guaranteed': float or None,
        'irr_no_withdrawal_total': float or None,
        'irr_withdrawal_guaranteed': float or None,
        'irr_withdrawal_total': float or None,
    }
    """
    pi = config.policy_info
    results = []

    # Build withdrawal lookup for fast access
    withdrawal_by_year = {}
    for wd in config.withdrawal_data:
        withdrawal_by_year[wd.year] = wd

    for rec in config.yearly_data:
        year = rec.year
        age = rec.age

        # --- Scenario A: No Withdrawal ---
        # Guaranteed only
        cf_g, t_g = build_cashflows_no_withdrawal(
            year, pi.annual_premium, pi.payment_years,
            rec.guaranteed_cash_value
        )
        irr_nw_g = calculate_irr(cf_g, t_g)

        # Total (expected)
        cf_t, t_t = build_cashflows_no_withdrawal(
            year, pi.annual_premium, pi.payment_years,
            rec.total_surrender_value
        )
        irr_nw_t = calculate_irr(cf_t, t_t)

        # --- Scenario B: With Withdrawal ---
        irr_wd_g = None
        irr_wd_t = None

        if config.withdrawal_data and year in withdrawal_by_year:
            wd_rec = withdrawal_by_year[year]

            # Guaranteed only
            cf_wg, t_wg = build_cashflows_with_withdrawal(
                year, pi.annual_premium, pi.payment_years,
                config.withdrawal_data,
                wd_rec.remaining_surrender_guaranteed,
            )
            irr_wd_g = calculate_irr(cf_wg, t_wg)

            # Total (expected)
            cf_wt, t_wt = build_cashflows_with_withdrawal(
                year, pi.annual_premium, pi.payment_years,
                config.withdrawal_data,
                wd_rec.remaining_surrender_total,
            )
            irr_wd_t = calculate_irr(cf_wt, t_wt)

        results.append({
            'year': year,
            'age': age,
            'irr_no_withdrawal_guaranteed': irr_nw_g,
            'irr_no_withdrawal_total': irr_nw_t,
            'irr_withdrawal_guaranteed': irr_wd_g,
            'irr_withdrawal_total': irr_wd_t,
        })

    return results
