"""
PDF extraction engine for AIA insurance policy illustrations.

Handles CID-encoded fonts common in AIA policy PDFs by decoding
CID numbers to ASCII characters (CID + 29 = ASCII).
"""
import re
from typing import Optional

import pdfplumber


# ---------------------------------------------------------------------------
# CID Decoding
# ---------------------------------------------------------------------------

def decode_cid(text: str) -> str:
    """Decode CID-encoded text. AIA PDFs use CID + 29 = ASCII mapping."""
    if not text:
        return ''

    def _replace(m):
        cid = int(m.group(1))
        ascii_code = cid + 29
        if 32 <= ascii_code <= 126:
            return chr(ascii_code)
        return ''

    return re.sub(r'\(cid:(\d+)\)', _replace, text)


def clean_numeric(text: str) -> float:
    """Parse a numeric string from PDF, handling commas, dashes, whitespace."""
    if not text:
        return 0.0
    text = text.strip()
    if text in ('-', '—', 'N/A', '不适用', ''):
        return 0.0
    cleaned = text.replace(',', '').replace('$', '').replace('HK$', '')
    cleaned = re.sub(r'[^\d.\-]', '', cleaned)
    if not cleaned or cleaned == '-':
        return 0.0
    return float(cleaned)


# ---------------------------------------------------------------------------
# Table detection helpers
# ---------------------------------------------------------------------------

def _identify_table_type(header_rows: list, ncols: int) -> Optional[str]:
    """Identify table type from decoded header labels and column count.

    Returns:
        'no_withdrawal' — surrender value table (8 cols, labels A/B/C/D/E)
        'death_benefit'  — death benefit table (10 cols, labels A + F + G/H/I/J)
        'withdrawal'     — withdrawal scenario table (10 cols, labels (1),(2) + A/B/C/D)
        None             — unknown
    """
    flat = ' '.join(
        decode_cid(str(cell)) for row in header_rows for cell in row if cell
    )

    if ncols == 8 and ('(A)' in flat and '(E)' in flat):
        return 'no_withdrawal'
    if ncols == 8 and ('(A)' in flat and '(B)' in flat):
        return 'no_withdrawal'
    if ncols == 10 and ('(F)' in flat or '(F ' in flat):
        return 'death_benefit'
    if ncols == 10 and ('(1)' in flat and '(2)' in flat):
        return 'withdrawal'
    # Fallback by column count and position on pages
    if ncols == 10 and '(G)' in flat:
        return 'death_benefit'
    return None


def _expand_rows(table: list, header_rows: int = 3) -> list[list[str]]:
    """Expand multi-value cells (values separated by \\n) into individual rows.

    AIA PDFs pack 5 years into a single table row, with values separated
    by newlines within each cell. This function expands them into one
    row per year.
    """
    data_rows = table[header_rows:]
    expanded = []
    for row in data_rows:
        # Decode all cells first
        decoded = [decode_cid(str(c)).strip() if c else '' for c in row]
        # Split each cell by newline
        split_cells = [cell.split('\n') for cell in decoded]
        # Number of sub-rows = max splits
        n_sub = max(len(parts) for parts in split_cells) if split_cells else 0
        for j in range(n_sub):
            sub_row = []
            for parts in split_cells:
                if j < len(parts):
                    sub_row.append(parts[j].strip())
                else:
                    sub_row.append('')
            expanded.append(sub_row)
    return expanded


# ---------------------------------------------------------------------------
# Main Extractor
# ---------------------------------------------------------------------------

class AIAPDFExtractor:
    """Extract insurance policy data from AIA PDF illustrations."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.warnings: list[str] = []
        self._pdf = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self) -> dict:
        """Main entry point. Returns a dict matching the JSON schema."""
        self._pdf = pdfplumber.open(self.pdf_path)
        try:
            return self._do_extract()
        finally:
            self._pdf.close()
            self._pdf = None

    def _do_extract(self) -> dict:
        # 1. Classify all tables across all pages
        nw_tables = []       # no-withdrawal surrender value tables
        db_tables = []       # death benefit tables
        wd_tables = []       # withdrawal scenario tables
        policy_tables = []   # small tables on page 1 (policy info)

        for page_idx, page in enumerate(self._pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue
                ncols = max(len(row) for row in table)
                nrows = len(table)

                # Small tables on page 1 are policy info
                if page_idx == 0 and nrows <= 3:
                    policy_tables.append(table)
                    continue

                # Identify table type from header
                header_rows = table[:min(3, nrows)]
                ttype = _identify_table_type(header_rows, ncols)

                if ttype == 'no_withdrawal':
                    nw_tables.append(table)
                elif ttype == 'death_benefit':
                    db_tables.append(table)
                elif ttype == 'withdrawal':
                    wd_tables.append(table)

        # 2. Extract policy info from page 1
        policy_info = self._extract_policy_info(policy_tables)

        # 3. Extract yearly data (no-withdrawal + death benefit)
        yearly_data = self._extract_yearly_data(nw_tables, db_tables, policy_info)

        # 4. Auto-detect premium from yearly data (most reliable source)
        if yearly_data:
            first_year_prem = yearly_data[0].get('cumulative_premium', 0)
            if first_year_prem > 0:
                policy_info['annual_premium'] = first_year_prem
                # Detect payment_years from when cumulative stops increasing
                for i in range(1, len(yearly_data)):
                    if yearly_data[i]['cumulative_premium'] == yearly_data[i-1]['cumulative_premium']:
                        policy_info['payment_years'] = i
                        break
                policy_info['total_premium'] = policy_info['annual_premium'] * policy_info['payment_years']
                self.warnings.append(
                    f"Auto-detected premium: {policy_info['annual_premium']:,.0f}/year x "
                    f"{policy_info['payment_years']} years = {policy_info['total_premium']:,.0f}"
                )

        # Auto-detect age from yearly data
        if yearly_data and policy_info['age_at_issue'] == 0:
            first_age = yearly_data[0].get('age', 0)
            if first_age > 0:
                policy_info['age_at_issue'] = first_age - 1

        # 5. Extract withdrawal data
        withdrawal_data = self._extract_withdrawal_data(wd_tables)

        # 6. Assemble result
        result = {
            'policy_info': policy_info,
            'brand': {
                'primary_color': '#C8102E',
                'secondary_color': '#FFFFFF',
                'accent_color': '#1A1A1A',
                'logo_text': 'AIA',
            },
            'display_settings': {
                'highlight_years': [5, 10, 15, 20, 25, 30],
                'highlight_ages': [65, 70, 75, 80, 85, 90, 95, 100],
                'irr_decimal_places': 2,
                'currency_decimal_places': 0,
            },
            'yearly_data': yearly_data,
        }
        if withdrawal_data:
            result['withdrawal_data'] = withdrawal_data

        return result

    # ------------------------------------------------------------------
    # Policy info extraction
    # ------------------------------------------------------------------

    def _extract_policy_info(self, policy_tables: list) -> dict:
        """Extract basic policy info from page 1 tables and text."""
        info = {
            'product_name': '',
            'product_name_en': '',
            'insurer': 'AIA',
            'insured_name': '',
            'age_at_issue': 0,
            'gender': 'M',
            'currency': 'USD',
            'currency_symbol': '$',
            'annual_premium': 0,
            'payment_years': 5,
            'total_premium': 0,
            'coverage_type': '终身 Whole Life',
            'plan_date': '',
        }

        # Try to extract from page 1 text
        page1_text = self._pdf.pages[0].extract_text() or ''
        page1_decoded = decode_cid(page1_text)

        # Extract from page 1 tables (decoded CID data)
        all_decoded_cells = []
        for table in policy_tables:
            for row in table:
                decoded = [decode_cid(str(c)).strip() if c else '' for c in row]
                all_decoded_cells.extend(decoded)

        # First table typically: [name, age, gender/empty, ...]
        if policy_tables:
            first_row = [decode_cid(str(c)).strip() if c else '' for c in policy_tables[0][0]]
            # First non-empty cell is likely the name
            for cell in first_row:
                if cell and not cell.replace(' ', '').isdigit() and cell != '-':
                    info['insured_name'] = cell
                    break
            # Next numeric cell is likely age
            for cell in first_row:
                if cell.strip().isdigit():
                    age = int(cell.strip())
                    if 0 <= age <= 120:
                        info['age_at_issue'] = age
                        break

        # Scan all decoded cells for premium amounts and payment years
        for cell in all_decoded_cells:
            # Look for comma-separated numbers (premium amounts)
            amounts = re.findall(r'([\d,]+(?:\.\d+)?)', cell)
            for amt in amounts:
                val = clean_numeric(amt)
                if 1000 <= val <= 1000000 and info['annual_premium'] == 0:
                    info['annual_premium'] = val
            # Look for standalone small numbers (payment years)
            if re.match(r'^\d{1,2}$', cell.strip()):
                py = int(cell.strip())
                if 2 <= py <= 30:
                    info['payment_years'] = py

        # Auto-detect product from page text
        page1_fitz_text = ''
        try:
            import fitz
            doc = fitz.open(self.pdf_path)
            page1_fitz_text = doc[0].get_text()
            doc.close()
        except Exception:
            pass

        # Try to detect product name from fitz text (better for Chinese)
        for text_source in [page1_fitz_text, page1_decoded]:
            if '环宇盈活' in text_source or '環宇盈活' in text_source:
                info['product_name'] = '环宇盈活储蓄保险计划'
                info['product_name_en'] = 'AIA Vision Life Savings Plan'
            elif '活享储蓄' in text_source or '活享儲蓄' in text_source:
                info['product_name'] = '活享储蓄保险计划'
                info['product_name_en'] = 'AIA Flexi Savings Plan'
            elif '爱伴航' in text_source or '愛伴航' in text_source:
                info['product_name'] = '爱伴航保险计划'
                info['product_name_en'] = 'AIA Love Navigator Plan'

            # Detect currency
            if 'USD' in text_source or '美元' in text_source:
                info['currency'] = 'USD'
                info['currency_symbol'] = '$'
            elif 'HKD' in text_source or '港元' in text_source or '港幣' in text_source:
                info['currency'] = 'HKD'
                info['currency_symbol'] = 'HK$'
            elif 'RMB' in text_source or 'CNY' in text_source or '人民币' in text_source:
                info['currency'] = 'RMB'
                info['currency_symbol'] = '¥'

        # Calculate total premium
        if info['annual_premium'] > 0 and info['payment_years'] > 0:
            info['total_premium'] = info['annual_premium'] * info['payment_years']

        # Add payment years to product name
        if info['payment_years'] and info['product_name']:
            info['product_name'] += f"（{info['payment_years']}年缴费）"
            info['product_name_en'] += f" ({info['payment_years']}-Year Payment)"

        return info

    # ------------------------------------------------------------------
    # Yearly data extraction (no-withdrawal scenario)
    # ------------------------------------------------------------------

    def _extract_yearly_data(self, nw_tables: list, db_tables: list,
                              policy_info: dict) -> list[dict]:
        """Extract no-withdrawal yearly data from surrender value + death benefit tables."""
        # Expand surrender value tables
        nw_rows = []
        for table in nw_tables:
            nw_rows.extend(_expand_rows(table, header_rows=3))

        # Expand death benefit tables
        db_rows = []
        for table in db_tables:
            db_rows.extend(_expand_rows(table, header_rows=3))

        # Build death benefit lookup by year
        db_by_year = {}
        for row in db_rows:
            if len(row) >= 10:
                year_str = row[0].strip()
                if year_str.isdigit():
                    year = int(year_str)
                    # Death benefit is last column or column containing (F+J)
                    # In 10-col death benefit table: cols are
                    # Year, Age, Cumul.Premium, (A), (F)DeathBenefit, (G), (H), #(I), (J)Total, (F+J)
                    db_val = clean_numeric(row[-1])  # last column = death benefit total
                    if db_val == 0:
                        db_val = clean_numeric(row[-2])
                    db_by_year[year] = db_val

        # Parse surrender value rows
        annual_premium = policy_info.get('annual_premium', 0)
        payment_years = policy_info.get('payment_years', 5)
        age_at_issue = policy_info.get('age_at_issue', 0)

        yearly_data = []
        for row in nw_rows:
            if len(row) < 8:
                continue
            year_str = row[0].strip()
            if not year_str.isdigit():
                continue

            year = int(year_str)
            age = int(row[1]) if row[1].strip().isdigit() else age_at_issue + year

            # 8-col no-withdrawal table:
            # Year, Age, Cumul.Premium, (A)Guaranteed, (B)Reversionary, (C)Terminal, #(D), (E)Total
            cumulative_premium = clean_numeric(row[2])
            guaranteed_cv = clean_numeric(row[3])
            reversionary_bonus = clean_numeric(row[4])
            terminal_dividend = clean_numeric(row[5])
            # Column 6 might be "#(D)" special dividend or 0
            special_div = clean_numeric(row[6])
            total_surrender = clean_numeric(row[7])

            # If cumulative premium is 0, calculate it
            if cumulative_premium == 0 and annual_premium > 0:
                cumulative_premium = annual_premium * min(year, payment_years)

            # Get death benefit from db table
            death_benefit = db_by_year.get(year, 0)
            if death_benefit == 0:
                death_benefit = max(total_surrender, cumulative_premium)

            # Merge special dividend into terminal dividend if present
            if special_div > 0:
                terminal_dividend += special_div

            yearly_data.append({
                'year': year,
                'age': age,
                'cumulative_premium': cumulative_premium,
                'guaranteed_cash_value': guaranteed_cv,
                'reversionary_bonus': reversionary_bonus,
                'terminal_dividend': terminal_dividend,
                'total_surrender_value': total_surrender,
                'total_death_benefit': death_benefit,
            })

        if not yearly_data:
            self.warnings.append("No yearly data extracted from PDF")

        return yearly_data

    # ------------------------------------------------------------------
    # Withdrawal data extraction
    # ------------------------------------------------------------------

    def _extract_withdrawal_data(self, wd_tables: list) -> list[dict]:
        """Extract withdrawal scenario data."""
        if not wd_tables:
            return []

        wd_rows = []
        for table in wd_tables:
            wd_rows.extend(_expand_rows(table, header_rows=3))

        withdrawal_data = []
        for row in wd_rows:
            if len(row) < 10:
                continue
            year_str = row[0].strip()
            if not year_str.isdigit():
                continue

            year = int(year_str)

            # 10-col withdrawal table:
            # Year, Age, RemainingGuaranteed, WithdrawalAmount, DeathBenefit?,
            # (A)remaining_guar, (B)remaining_bonus, (C)remaining_terminal, #(D), Total
            #
            # Actual layout from decoded data:
            # Col 0: Year
            # Col 1: Age
            # Col 2: Remaining guaranteed (small number)
            # Col 3: Withdrawal amount per year
            # Col 4: Some cumulative value
            # Col 5: (A) remaining_surrender_guaranteed
            # Col 6: (B) remaining_surrender_bonus
            # Col 7: (C) remaining_surrender_terminal
            # Col 8: #(D) special
            # Col 9: Total remaining

            withdrawal_amount = clean_numeric(row[3])
            remaining_guaranteed = clean_numeric(row[5])
            remaining_bonus = clean_numeric(row[6])
            remaining_terminal = clean_numeric(row[7])
            remaining_special = clean_numeric(row[8])
            remaining_total = clean_numeric(row[9])

            # Merge special into terminal if present
            if remaining_special > 0:
                remaining_terminal += remaining_special

            withdrawal_data.append({
                'year': year,
                'withdrawal_amount': withdrawal_amount,
                'remaining_surrender_guaranteed': remaining_guaranteed,
                'remaining_surrender_bonus': remaining_bonus,
                'remaining_surrender_terminal': remaining_terminal,
                'remaining_surrender_total': remaining_total,
            })

        return withdrawal_data
