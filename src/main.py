"""
Insurance Policy IRR Calculator - CLI Entry Point

Usage:
    python -m src.main policy_data/aia_visionlife_5pay.json -o output/
"""
import argparse
import sys
from pathlib import Path

from .config import load_policy
from .irr import calculate_all_irr
from .excel_writer import create_excel_report
from .html_writer import create_html_report


def main():
    parser = argparse.ArgumentParser(
        description="Insurance Policy IRR Calculator - 保单IRR计算工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main policy_data/aia_visionlife_5pay.json
  python -m src.main policy_data/aia_visionlife_5pay.json -o output/
  python -m src.main policy_data/aia_visionlife_5pay.json --excel-only
  python -m src.main policy_data/aia_visionlife_5pay.json --html-only
        """,
    )
    parser.add_argument(
        "policy_json",
        help="Path to policy JSON configuration file (保单数据JSON文件路径)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="./output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--excel-only",
        action="store_true",
        help="Generate Excel output only",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Generate HTML output only",
    )

    args = parser.parse_args()

    # 1. Load config
    print(f"Loading policy data from: {args.policy_json}")
    config = load_policy(args.policy_json)
    print(f"  Product: {config.policy_info.product_name}")
    print(f"  Insured: {config.policy_info.insured_name}")
    print(f"  Premium: {config.policy_info.currency_symbol}{config.policy_info.annual_premium:,.0f}/year × {config.policy_info.payment_years} years")
    print(f"  Data: {len(config.yearly_data)} years (no-withdrawal), {len(config.withdrawal_data)} years (withdrawal)")

    # 2. Calculate IRR
    print("\nCalculating IRR...")
    irr_results = calculate_all_irr(config)
    print(f"  Calculated IRR for {len(irr_results)} years")

    # Print a few key IRR values
    for r in irr_results:
        if r['year'] in [5, 10, 15, 20, 30]:
            nw = r['irr_no_withdrawal_total']
            wd = r['irr_withdrawal_total']
            nw_str = f"{nw*100:.2f}%" if nw is not None else "N/A"
            wd_str = f"{wd*100:.2f}%" if wd is not None else "N/A"
            print(f"  Year {r['year']:>3d} (Age {r['age']:>3d}): No-withdrawal={nw_str:>10s}  Withdrawal={wd_str:>10s}")

    # 3. Generate outputs
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = config.policy_info.insurer.lower().replace(' ', '_')

    if not args.html_only:
        excel_path = output_dir / f"{slug}_irr_report.xlsx"
        create_excel_report(config, irr_results, str(excel_path))
        print(f"\n✅ Excel report: {excel_path}")

    if not args.excel_only:
        html_path = output_dir / f"{slug}_irr_report.html"
        create_html_report(config, irr_results, str(html_path))
        print(f"✅ HTML report:  {html_path}")

    print("\nDone!")


if __name__ == "__main__":
    main()
