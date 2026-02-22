"""
Configuration loading and validation for insurance policy data.
"""
import json
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PolicyInfo:
    product_name: str
    product_name_en: str
    insurer: str
    insured_name: str
    age_at_issue: int
    gender: str
    currency: str
    currency_symbol: str
    annual_premium: float
    payment_years: int
    total_premium: float
    coverage_type: str
    plan_date: str


@dataclass
class YearlyRecord:
    year: int
    age: int
    cumulative_premium: float
    guaranteed_cash_value: float      # (A)
    reversionary_bonus: float          # (B)
    terminal_dividend: float           # (C)
    total_surrender_value: float       # (A)+(B)+(C)
    total_death_benefit: float


@dataclass
class WithdrawalRecord:
    year: int
    withdrawal_amount: float           # 0 for early years, actual amount when withdrawals start
    remaining_surrender_guaranteed: float  # (A) after withdrawal
    remaining_surrender_bonus: float       # (B) after withdrawal
    remaining_surrender_terminal: float    # (C) after withdrawal
    remaining_surrender_total: float       # (A)+(B)+(C) after withdrawal


@dataclass
class BrandConfig:
    primary_color: str = "#C8102E"
    secondary_color: str = "#FFFFFF"
    accent_color: str = "#1A1A1A"
    logo_text: str = "AIA"


@dataclass
class DisplaySettings:
    highlight_years: List[int] = field(default_factory=lambda: [5, 10, 15, 20, 25, 30])
    highlight_ages: List[int] = field(default_factory=lambda: [65, 70, 75, 80])
    irr_decimal_places: int = 2
    currency_decimal_places: int = 0


@dataclass
class PolicyConfig:
    policy_info: PolicyInfo
    brand: BrandConfig
    display: DisplaySettings
    yearly_data: List[YearlyRecord]
    withdrawal_data: List[WithdrawalRecord]


def load_policy(json_path: str) -> PolicyConfig:
    """Load and validate policy JSON configuration."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    policy_info = PolicyInfo(**data['policy_info'])

    brand = BrandConfig(**data.get('brand', {}))

    display_raw = data.get('display_settings', {})
    display = DisplaySettings(**display_raw)

    yearly_data = [YearlyRecord(**row) for row in data['yearly_data']]

    withdrawal_data = []
    if 'withdrawal_data' in data:
        withdrawal_data = [WithdrawalRecord(**row) for row in data['withdrawal_data']]

    config = PolicyConfig(
        policy_info=policy_info,
        brand=brand,
        display=display,
        yearly_data=yearly_data,
        withdrawal_data=withdrawal_data,
    )

    # Validate
    warnings = validate_policy(config)
    for w in warnings:
        print(f"  WARNING: {w}")

    return config


def validate_policy(config: PolicyConfig) -> List[str]:
    """Validate policy data consistency. Returns list of warning messages."""
    warnings = []
    pi = config.policy_info

    # Check total premium
    expected_total = pi.annual_premium * pi.payment_years
    if abs(pi.total_premium - expected_total) > 0.01:
        warnings.append(
            f"total_premium ({pi.total_premium}) != annual_premium * payment_years ({expected_total})"
        )

    # Check yearly data
    for rec in config.yearly_data:
        # Check A+B+C = total
        calc_total = rec.guaranteed_cash_value + rec.reversionary_bonus + rec.terminal_dividend
        if abs(calc_total - rec.total_surrender_value) > 1:
            warnings.append(
                f"Year {rec.year}: A+B+C ({calc_total}) != total_surrender ({rec.total_surrender_value})"
            )

        # Check cumulative premium
        expected_prem = pi.annual_premium * min(rec.year, pi.payment_years)
        if abs(rec.cumulative_premium - expected_prem) > 0.01:
            warnings.append(
                f"Year {rec.year}: cumulative_premium ({rec.cumulative_premium}) != expected ({expected_prem})"
            )

        # Check age
        expected_age = pi.age_at_issue + rec.year
        if rec.age != expected_age:
            warnings.append(
                f"Year {rec.year}: age ({rec.age}) != age_at_issue + year ({expected_age})"
            )

    # Check withdrawal data
    for rec in config.withdrawal_data:
        calc_total = (rec.remaining_surrender_guaranteed +
                      rec.remaining_surrender_bonus +
                      rec.remaining_surrender_terminal)
        if abs(calc_total - rec.remaining_surrender_total) > 1:
            warnings.append(
                f"Withdrawal Year {rec.year}: A+B+C ({calc_total}) != remaining_total ({rec.remaining_surrender_total})"
            )

    # Check year sequences
    if config.yearly_data:
        years = [r.year for r in config.yearly_data]
        if years != list(range(years[0], years[-1] + 1)):
            warnings.append("yearly_data years are not sequential")

    if config.withdrawal_data:
        w_years = [r.year for r in config.withdrawal_data]
        if w_years != list(range(w_years[0], w_years[-1] + 1)):
            warnings.append("withdrawal_data years are not sequential")

    return warnings
