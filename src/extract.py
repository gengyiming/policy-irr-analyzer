"""
PDF Extraction CLI Entry Point

Usage:
    python -m src.extract <pdf_path> [-o OUTPUT_JSON] [--run]

Examples:
    python -m src.extract policy_data/sample.pdf
    python -m src.extract policy_data/sample.pdf -o policy_data/client.json
    python -m src.extract policy_data/sample.pdf --run
"""
import argparse
import json
import sys
from pathlib import Path

from .pdf_extractor import AIAPDFExtractor
from .config import load_policy_from_dict


def main():
    parser = argparse.ArgumentParser(
        description="Extract AIA policy data from PDF - AIA保单PDF数据提取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.extract policy_data/sample.pdf
  python -m src.extract policy_data/sample.pdf -o policy_data/client.json
  python -m src.extract policy_data/sample.pdf -o policy_data/client.json --run
        """,
    )
    parser.add_argument(
        "pdf_path",
        help="Path to AIA policy PDF file (AIA保单PDF文件路径)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON path (default: derived from PDF name)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="After extraction, immediately run IRR analysis (提取后直接生成报告)",
    )
    parser.add_argument(
        "--name",
        help="Insured name (受保人姓名), overrides PDF detection",
    )
    parser.add_argument(
        "--product",
        help="Product name (产品名称), overrides PDF detection",
    )
    parser.add_argument(
        "--age",
        type=int,
        help="Age at issue (投保年龄), overrides PDF detection",
    )
    parser.add_argument(
        "--currency",
        choices=['USD', 'HKD', 'RMB'],
        help="Currency (保单货币), overrides PDF detection",
    )

    args = parser.parse_args()

    # 1. Extract data from PDF
    print(f"📄 Extracting data from: {args.pdf_path}")
    extractor = AIAPDFExtractor(args.pdf_path)
    data = extractor.extract()

    # Apply CLI overrides
    pi = data['policy_info']
    if args.name:
        pi['insured_name'] = args.name
    if args.product:
        pi['product_name'] = args.product
    if args.age is not None:
        pi['age_at_issue'] = args.age
        # Recalculate ages in yearly_data
        for row in data.get('yearly_data', []):
            row['age'] = args.age + row['year']
    if args.currency:
        pi['currency'] = args.currency
        pi['currency_symbol'] = {'USD': '$', 'HKD': 'HK$', 'RMB': '¥'}[args.currency]

    # 2. Print extraction summary
    pi = data['policy_info']
    print(f"\n{'='*50}")
    print(f"  Product:  {pi['product_name']}")
    print(f"  Insured:  {pi['insured_name']}")
    print(f"  Age:      {pi['age_at_issue']}")
    print(f"  Premium:  {pi['currency_symbol']}{pi['annual_premium']:,.0f}/year x {pi['payment_years']} years")
    print(f"  Total:    {pi['currency_symbol']}{pi['total_premium']:,.0f}")
    print(f"  Currency: {pi['currency']}")

    yd = data.get('yearly_data', [])
    wd = data.get('withdrawal_data', [])
    print(f"\n  Yearly data:      {len(yd)} rows")
    print(f"  Withdrawal data:  {len(wd)} rows")

    if yd:
        print(f"\n  Sample (Year {yd[0]['year']}): surrender={yd[0]['total_surrender_value']:,.0f}")
        mid = min(9, len(yd) - 1)
        print(f"  Sample (Year {yd[mid]['year']}): surrender={yd[mid]['total_surrender_value']:,.0f}")
        print(f"  Sample (Year {yd[-1]['year']}): surrender={yd[-1]['total_surrender_value']:,.0f}")

    if extractor.warnings:
        print(f"\n  ⚠️  Warnings:")
        for w in extractor.warnings:
            print(f"    - {w}")

    print(f"{'='*50}")

    # 3. Validate
    print("\nValidating extracted data...")
    try:
        config = load_policy_from_dict(data)
        print("✅ Validation passed!")
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        print("   The JSON will still be saved. You may need to manually fix some values.")

    # 4. Save JSON
    if args.output:
        output_path = args.output
    else:
        pdf_stem = Path(args.pdf_path).stem
        output_path = f"policy_data/{pdf_stem}.json"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Saved JSON: {output_path}")

    # 5. Optionally run IRR analysis
    if args.run:
        print("\n" + "="*50)
        print("Running IRR analysis...")
        print("="*50 + "\n")
        from .main import main as run_main
        sys.argv = ['', output_path]
        run_main()


if __name__ == "__main__":
    main()
