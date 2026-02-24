"""
PDF extraction engine for AIA insurance policy illustrations.

Handles CID-encoded fonts common in AIA policy PDFs by decoding
CID numbers to ASCII characters (CID + 29 = ASCII).

Features robust table detection with fallback strategies:
- Dynamic header detection (not hardcoded to 3 rows)
- Flexible column count matching (not restricted to exact 8/10)
- Multilingual keyword matching (Chinese + English)
- Fallback heuristic classification for unrecognized tables
- Diagnostic warnings for debugging extraction issues
"""
import re
from typing import Optional

import pdfplumber


# ---------------------------------------------------------------------------
# CID Decoding
# ---------------------------------------------------------------------------

def decode_cid(text: str) -> str:
    """Decode CID-encoded text. AIA PDFs use CID + 29 = ASCII mapping.

    If the text contains no CID patterns, it is returned as-is (handles
    PDFs that use standard Unicode encoding instead of CID).
    """
    if not text:
        return ''
    # Fast path: no CID encoding present
    if '(cid:' not in text:
        return text

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
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Table detection helpers
# ---------------------------------------------------------------------------

def _get_table_header_text(header_rows: list) -> str:
    """Get flattened decoded text from header rows for matching."""
    parts = []
    for row in header_rows:
        for cell in row:
            if cell:
                decoded = decode_cid(str(cell))
                parts.append(decoded)
                # Also include the raw text for non-CID PDFs
                raw = str(cell)
                if raw != decoded:
                    parts.append(raw)
    return ' '.join(parts)


def _identify_table_type(header_rows: list, ncols: int) -> Optional[str]:
    """Identify table type from decoded header labels and column count.

    Uses a multi-tier strategy:
    1. Exact match: original strict rules (column count + specific labels)
    2. Keyword match: Chinese/English keywords with flexible column counts
    3. Returns None if no match found

    Returns:
        'no_withdrawal' — surrender value table
        'death_benefit'  — death benefit table
        'withdrawal'     — withdrawal scenario table
        None             — unknown
    """
    flat = _get_table_header_text(header_rows)
    flat_lower = flat.lower()

    # --- Tier 1: Original strict matching ---
    if ncols == 8 and ('(A)' in flat and '(E)' in flat):
        return 'no_withdrawal'
    if ncols == 8 and ('(A)' in flat and '(B)' in flat):
        return 'no_withdrawal'
    if ncols == 10 and ('(F)' in flat or '(F ' in flat):
        return 'death_benefit'
    if ncols == 10 and ('(1)' in flat and '(2)' in flat):
        return 'withdrawal'
    if ncols == 10 and '(G)' in flat:
        return 'death_benefit'

    # --- Tier 2: Flexible keyword matching ---
    # Check for no_withdrawal keywords (surrender value tables)
    nw_keywords = [
        '保证现金价值', '保證現金價值', '退保价值', '退保價值',
        '退保发还', '退保發還', '保证退保', '保證退保',
        '累积保费', '累積保費', '累积已缴保费', '累積已繳保費',
        'guaranteed', 'surrender', 'cash value',
        '(a)', '(b)', '(c)', '(e)',
    ]
    # Check for death benefit keywords
    db_keywords = [
        '身故赔偿', '身故賠償', '死亡保障',
        'death benefit', 'death_benefit',
        '(f)', '(g)', '(h)', '(i)', '(j)',
    ]
    # Check for withdrawal keywords
    wd_keywords = [
        '提取', '提領', '每年可提取', '每年可提領',
        '提取方案', '提領方案',
        'withdrawal', 'withdraw',
        '(1)', '(2)',
    ]

    nw_score = sum(1 for kw in nw_keywords if kw in flat_lower or kw in flat)
    db_score = sum(1 for kw in db_keywords if kw in flat_lower or kw in flat)
    wd_score = sum(1 for kw in wd_keywords if kw in flat_lower or kw in flat)

    # Withdrawal tables are typically 10 columns with withdrawal keywords
    if wd_score >= 2 and ncols >= 8:
        return 'withdrawal'
    # No-withdrawal tables often also contain a death benefit column, so
    # when both NW and DB keywords match, prefer NW if NW score is higher
    if nw_score >= 2 and 6 <= ncols <= 10:
        return 'no_withdrawal'
    # Death benefit tables are typically 10 columns
    if db_score >= 2 and ncols >= 8:
        return 'death_benefit'

    # --- Tier 3: Single strong keyword match with relaxed column count ---
    # Standard AIA no-withdrawal tables include a death benefit column,
    # so allow NW even when db_score > 0 if NW score is at least as high
    if nw_score >= 1 and 6 <= ncols <= 10 and nw_score >= db_score:
        return 'no_withdrawal'
    if db_score >= 1 and ncols >= 8 and wd_score == 0:
        return 'death_benefit'
    if wd_score >= 1 and ncols >= 8:
        return 'withdrawal'

    return None


def _detect_header_rows(table: list) -> int:
    """Auto-detect the number of header rows in a table.

    Scans from the top until finding a row whose first cell contains
    a digit (indicating a year number = start of data rows).

    Returns the detected header row count, defaulting to 3 if detection fails.
    """
    for i, row in enumerate(table):
        if not row or not row[0]:
            continue
        first_cell = decode_cid(str(row[0])).strip()
        if not first_cell:
            continue
        # Check if the first value (possibly multi-line) starts with a digit
        first_val = first_cell.split('\n')[0].strip()
        if first_val.isdigit():
            return max(i, 1)  # At least 1 header row
    # Default fallback
    return min(3, len(table) - 1)


def _expand_rows(table: list, header_rows: int = -1) -> list[list[str]]:
    """Expand multi-value cells (values separated by \\n) into individual rows.

    AIA PDFs pack 5 years into a single table row, with values separated
    by newlines within each cell. This function expands them into one
    row per year.

    If header_rows is -1 (default), auto-detects the header count.
    """
    if header_rows < 0:
        header_rows = _detect_header_rows(table)

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


def _detect_nw_column_mapping(table: list, header_rows: int) -> dict:
    """Detect column mapping for no-withdrawal table from header labels.

    Returns a dict mapping field names to column indices.
    Falls back to hardcoded 8-column layout if detection fails.
    """
    # Merge all header rows into per-column strings
    ncols = max(len(row) for row in table) if table else 0
    headers = [''] * ncols
    for i in range(min(header_rows, len(table))):
        for j, cell in enumerate(table[i]):
            if j < ncols and cell:
                decoded = decode_cid(str(cell)).strip()
                raw = str(cell).strip()
                headers[j] += ' ' + decoded + ' ' + raw

    mapping = {}
    for idx, h in enumerate(headers):
        h_combined = h.lower() + ' ' + h

        # Year is always column 0, Age is column 1
        if idx == 0:
            mapping['year'] = idx
            continue
        if idx == 1:
            mapping['age'] = idx
            continue

        # Try to identify columns by keywords
        if any(kw in h_combined for kw in ['累积保费', '累積保費', 'cumulative', 'premium', '保费', '保費']):
            if 'cumulative_premium' not in mapping:
                mapping['cumulative_premium'] = idx
        elif any(kw in h_combined for kw in ['(a)', '保证现金', '保證現金', 'guaranteed']):
            if 'guaranteed_cv' not in mapping:
                mapping['guaranteed_cv'] = idx
        elif any(kw in h_combined for kw in ['(b)', '复归红利', '復歸紅利', 'reversionary']):
            if 'reversionary_bonus' not in mapping:
                mapping['reversionary_bonus'] = idx
        elif any(kw in h_combined for kw in ['(c)', '终期红利', '終期紅利', 'terminal']):
            if 'terminal_dividend' not in mapping:
                mapping['terminal_dividend'] = idx
        elif any(kw in h_combined for kw in ['#', '特别', '特別', 'special']):
            if 'special_div' not in mapping:
                mapping['special_div'] = idx
        elif any(kw in h_combined for kw in ['(e)', '退保总额', '退保總額', 'total', 'surrender']):
            if 'total_surrender' not in mapping:
                mapping['total_surrender'] = idx

    return mapping


def _detect_wd_column_mapping(table: list, header_rows: int) -> dict:
    """Detect column mapping for withdrawal table from header labels.

    Returns a dict mapping field names to column indices.
    Falls back to hardcoded 10-column layout if detection fails.
    """
    ncols = max(len(row) for row in table) if table else 0
    headers = [''] * ncols
    for i in range(min(header_rows, len(table))):
        for j, cell in enumerate(table[i]):
            if j < ncols and cell:
                decoded = decode_cid(str(cell)).strip()
                raw = str(cell).strip()
                headers[j] += ' ' + decoded + ' ' + raw

    mapping = {}
    for idx, h in enumerate(headers):
        h_combined = h.lower() + ' ' + h

        if idx == 0:
            mapping['year'] = idx
            continue
        if idx == 1:
            mapping['age'] = idx
            continue

        if any(kw in h_combined for kw in ['提取', '提領', 'withdrawal', '每年']):
            if 'withdrawal_amount' not in mapping:
                mapping['withdrawal_amount'] = idx
        elif any(kw in h_combined for kw in ['(a)', '保证', '保證', 'guaranteed']):
            if 'remaining_guaranteed' not in mapping:
                mapping['remaining_guaranteed'] = idx
        elif any(kw in h_combined for kw in ['(b)', '复归', '復歸', 'reversionary']):
            if 'remaining_bonus' not in mapping:
                mapping['remaining_bonus'] = idx
        elif any(kw in h_combined for kw in ['(c)', '终期', '終期', 'terminal']):
            if 'remaining_terminal' not in mapping:
                mapping['remaining_terminal'] = idx
        elif any(kw in h_combined for kw in ['#', '特别', '特別', 'special']):
            if 'remaining_special' not in mapping:
                mapping['remaining_special'] = idx
        elif any(kw in h_combined for kw in ['总额', '總額', 'total']):
            if 'remaining_total' not in mapping:
                mapping['remaining_total'] = idx

    return mapping


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
        unclassified_tables = []  # tables that could not be classified

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
                header_count = _detect_header_rows(table)
                header_rows = table[:min(header_count, nrows)]
                ttype = _identify_table_type(header_rows, ncols)

                if ttype == 'no_withdrawal':
                    nw_tables.append(table)
                elif ttype == 'death_benefit':
                    db_tables.append(table)
                elif ttype == 'withdrawal':
                    wd_tables.append(table)
                else:
                    # Log unclassified table for diagnostics
                    header_preview = ''
                    try:
                        preview_cells = []
                        for row in table[:min(2, nrows)]:
                            for cell in row[:min(4, len(row))]:
                                if cell:
                                    decoded = decode_cid(str(cell)).strip()[:30]
                                    if decoded:
                                        preview_cells.append(decoded)
                        header_preview = ' | '.join(preview_cells[:6])
                    except Exception:
                        header_preview = '(unable to decode)'

                    self.warnings.append(
                        f"Page {page_idx+1}: 未识别表格 ({nrows}行x{ncols}列), "
                        f"表头: {header_preview}"
                    )
                    # Keep for fallback classification
                    if nrows > 3:
                        unclassified_tables.append(table)

        # --- Fallback: if no nw_tables found, try heuristic classification ---
        if not nw_tables and unclassified_tables:
            self.warnings.append(
                "表格类型自动推断：严格匹配失败，使用启发式匹配"
            )
            # Sort by size (rows * cols) descending - largest tables first
            unclassified_tables.sort(
                key=lambda t: len(t) * max(len(r) for r in t),
                reverse=True,
            )

            for table in unclassified_tables:
                ncols = max(len(row) for row in table)
                # Try to determine type by checking data content
                ttype = self._heuristic_classify(table)
                if ttype == 'no_withdrawal' and not nw_tables:
                    nw_tables.append(table)
                    self.warnings.append(
                        f"  → 启发式: 表格({len(table)}行x{ncols}列) → no_withdrawal"
                    )
                elif ttype == 'death_benefit' and not db_tables:
                    db_tables.append(table)
                elif ttype == 'withdrawal' and not wd_tables:
                    wd_tables.append(table)

            # Last resort: if still no nw_tables, use the largest table
            if not nw_tables and unclassified_tables:
                largest = unclassified_tables[0]
                ncols = max(len(row) for row in largest)
                nw_tables.append(largest)
                self.warnings.append(
                    f"  → 最后手段: 最大表格({len(largest)}行x{ncols}列) → no_withdrawal"
                )

        # Log classification summary
        self.warnings.append(
            f"表格分类结果: no_withdrawal={len(nw_tables)}, "
            f"death_benefit={len(db_tables)}, withdrawal={len(wd_tables)}"
        )

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
    # Heuristic table classification
    # ------------------------------------------------------------------

    def _heuristic_classify(self, table: list) -> Optional[str]:
        """Classify a table by examining its data patterns when header
        detection fails.

        Looks at expanded data rows to determine if they match the pattern
        of a no-withdrawal, death-benefit, or withdrawal table.
        """
        try:
            rows = _expand_rows(table)
            if not rows:
                return None

            ncols = max(len(row) for row in table)

            # Count rows that look like data (first cell is a year number)
            data_rows = [r for r in rows if r and r[0].strip().isdigit()]
            if not data_rows:
                return None

            # Check if years are sequential starting from 1
            years = []
            for r in data_rows:
                try:
                    years.append(int(r[0].strip()))
                except ValueError:
                    pass

            if not years:
                return None

            # Tables with 8 or fewer effective columns are likely no_withdrawal
            # Tables with 10+ columns could be death_benefit or withdrawal
            effective_cols = len(data_rows[0]) if data_rows else ncols

            if effective_cols <= 9:
                return 'no_withdrawal'
            elif effective_cols >= 10:
                # Check if there are withdrawal-like amounts
                # (column 3 typically has withdrawal amounts)
                has_withdrawal_amounts = False
                for r in data_rows[5:10]:  # Check some middle rows
                    if len(r) > 3:
                        val = clean_numeric(r[3])
                        if val > 0:
                            has_withdrawal_amounts = True
                            break

                if has_withdrawal_amounts:
                    return 'withdrawal'
                else:
                    return 'death_benefit'
        except Exception:
            return None

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
        # Expand surrender value tables with auto header detection
        nw_rows = []
        nw_col_mapping = {}
        for table in nw_tables:
            header_count = _detect_header_rows(table)
            if not nw_col_mapping:
                nw_col_mapping = _detect_nw_column_mapping(table, header_count)
            nw_rows.extend(_expand_rows(table))

        # Expand death benefit tables
        db_rows = []
        for table in db_tables:
            db_rows.extend(_expand_rows(table))

        # Build death benefit lookup by year
        db_by_year = {}
        for row in db_rows:
            if len(row) < 6:
                continue
            year_str = row[0].strip()
            if year_str.isdigit():
                year = int(year_str)
                # Death benefit is last column or column containing (F+J)
                db_val = clean_numeric(row[-1])  # last column = death benefit total
                if db_val == 0 and len(row) >= 2:
                    db_val = clean_numeric(row[-2])
                db_by_year[year] = db_val

        # Parse surrender value rows
        annual_premium = policy_info.get('annual_premium', 0)
        payment_years = policy_info.get('payment_years', 5)
        age_at_issue = policy_info.get('age_at_issue', 0)

        # Determine column indices from mapping or use hardcoded defaults
        col_year = nw_col_mapping.get('year', 0)
        col_age = nw_col_mapping.get('age', 1)
        col_prem = nw_col_mapping.get('cumulative_premium', 2)
        col_guar = nw_col_mapping.get('guaranteed_cv', 3)
        col_rev = nw_col_mapping.get('reversionary_bonus', 4)
        col_term = nw_col_mapping.get('terminal_dividend', 5)
        col_spec = nw_col_mapping.get('special_div', 6)
        col_total = nw_col_mapping.get('total_surrender', -1)  # -1 = last col

        # Log the column mapping for debugging
        if nw_col_mapping:
            self.warnings.append(
                f"No-withdrawal 列映射: {nw_col_mapping}"
            )

        yearly_data = []
        for row in nw_rows:
            # Accept rows with at least 6 columns (relaxed from 8)
            if len(row) < 6:
                continue
            year_str = row[col_year].strip() if col_year < len(row) else ''
            if not year_str.isdigit():
                continue

            year = int(year_str)
            age_str = row[col_age].strip() if col_age < len(row) else ''
            age = int(age_str) if age_str.isdigit() else age_at_issue + year

            # Extract values using detected or default column indices
            cumulative_premium = clean_numeric(row[col_prem]) if col_prem < len(row) else 0.0
            guaranteed_cv = clean_numeric(row[col_guar]) if col_guar < len(row) else 0.0
            reversionary_bonus = clean_numeric(row[col_rev]) if col_rev < len(row) else 0.0
            terminal_dividend = clean_numeric(row[col_term]) if col_term < len(row) else 0.0

            # Special dividend column (may not exist in all table layouts)
            special_div = 0.0
            if col_spec >= 0 and col_spec < len(row):
                special_div = clean_numeric(row[col_spec])

            # Total surrender: use detected column or last column
            if col_total >= 0 and col_total < len(row):
                total_surrender = clean_numeric(row[col_total])
            else:
                total_surrender = clean_numeric(row[-1])

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
            # Log diagnostic info about what was found
            total_nw_rows = len(nw_rows)
            self.warnings.append(
                f"  诊断: {len(nw_tables)} 个 no_withdrawal 表格, "
                f"展开后 {total_nw_rows} 行"
            )
            if nw_rows:
                sample = nw_rows[0][:5] if nw_rows[0] else []
                self.warnings.append(f"  首行样本: {sample}")

        return yearly_data

    # ------------------------------------------------------------------
    # Withdrawal data extraction
    # ------------------------------------------------------------------

    def _extract_withdrawal_data(self, wd_tables: list) -> list[dict]:
        """Extract withdrawal scenario data."""
        if not wd_tables:
            return []

        wd_rows = []
        wd_col_mapping = {}
        for table in wd_tables:
            header_count = _detect_header_rows(table)
            if not wd_col_mapping:
                wd_col_mapping = _detect_wd_column_mapping(table, header_count)
            wd_rows.extend(_expand_rows(table))

        # Determine column indices from mapping or use hardcoded defaults
        col_year = wd_col_mapping.get('year', 0)
        col_age = wd_col_mapping.get('age', 1)
        col_wd_amt = wd_col_mapping.get('withdrawal_amount', 3)
        col_rem_guar = wd_col_mapping.get('remaining_guaranteed', 5)
        col_rem_bonus = wd_col_mapping.get('remaining_bonus', 6)
        col_rem_term = wd_col_mapping.get('remaining_terminal', 7)
        col_rem_spec = wd_col_mapping.get('remaining_special', 8)
        col_rem_total = wd_col_mapping.get('remaining_total', -1)  # -1 = last col

        if wd_col_mapping:
            self.warnings.append(
                f"Withdrawal 列映射: {wd_col_mapping}"
            )

        withdrawal_data = []
        for row in wd_rows:
            # Accept rows with at least 6 columns (relaxed from 10)
            if len(row) < 6:
                continue
            year_str = row[col_year].strip() if col_year < len(row) else ''
            if not year_str.isdigit():
                continue

            year = int(year_str)

            withdrawal_amount = clean_numeric(row[col_wd_amt]) if col_wd_amt < len(row) else 0.0
            remaining_guaranteed = clean_numeric(row[col_rem_guar]) if col_rem_guar < len(row) else 0.0
            remaining_bonus = clean_numeric(row[col_rem_bonus]) if col_rem_bonus < len(row) else 0.0
            remaining_terminal = clean_numeric(row[col_rem_term]) if col_rem_term < len(row) else 0.0

            remaining_special = 0.0
            if col_rem_spec >= 0 and col_rem_spec < len(row):
                remaining_special = clean_numeric(row[col_rem_spec])

            if col_rem_total >= 0 and col_rem_total < len(row):
                remaining_total = clean_numeric(row[col_rem_total])
            else:
                remaining_total = clean_numeric(row[-1])

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
