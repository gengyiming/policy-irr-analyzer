"""
HTML report generation for PPT presentation.
Self-contained HTML with embedded CSS and Chart.js.
"""
import json as json_module
from .config import PolicyConfig


def create_html_report(config: PolicyConfig, irr_results: list, output_path: str):
    """Generate a self-contained HTML report."""
    pi = config.policy_info
    brand = config.brand

    # Filter key years for presentation tables
    key_years = set(config.display.highlight_years)
    key_ages = set(config.display.highlight_ages)

    key_indices = [
        i for i, r in enumerate(irr_results)
        if r['year'] in key_years or r['age'] in key_ages
    ]

    # Prepare chart data (filter to years 3+ where IRR is meaningful)
    chart_labels = []
    chart_nw_total = []
    chart_wd_total = []

    for r in irr_results:
        if r['year'] < 3:
            continue
        chart_labels.append(r['year'])

        nw_t = r['irr_no_withdrawal_total']
        wd_t = r['irr_withdrawal_total']

        chart_nw_total.append(round(nw_t * 100, 2) if nw_t is not None else None)
        chart_wd_total.append(round(wd_t * 100, 2) if wd_t is not None else None)

    chart_data = {
        'labels': chart_labels,
        'nw_total': chart_nw_total,
        'wd_total': chart_wd_total,
    }

    # Prepare cash value chart data (all years)
    cv_labels = [r.year for r in config.yearly_data]
    cv_nw_total = [r.total_surrender_value for r in config.yearly_data]

    # Withdrawal scenario: total value = remaining surrender + cumulative withdrawals
    cv_wd_total = []
    if config.withdrawal_data:
        wd_by_year = {wd.year: wd for wd in config.withdrawal_data}
        cum_wd = 0.0
        for r in config.yearly_data:
            if r.year in wd_by_year:
                cum_wd += wd_by_year[r.year].withdrawal_amount
                remaining = wd_by_year[r.year].remaining_surrender_total or 0
                cv_wd_total.append(remaining + cum_wd)
            else:
                cv_wd_total.append(None)

    cv_chart_data = {
        'labels': cv_labels,
        'nw_total': cv_nw_total,
        'wd_total': cv_wd_total,
        'total_premium': pi.total_premium,
    }

    # Build no-withdrawal key rows HTML
    nw_rows_html = _build_nw_table_rows(config, irr_results, key_indices)

    # Build withdrawal key rows HTML
    wd_rows_html = _build_wd_table_rows(config, irr_results, key_indices)

    # Build full data tables
    full_nw_rows = _build_nw_table_rows(config, irr_results, list(range(len(irr_results))))
    full_wd_rows = _build_wd_table_rows(config, irr_results, list(range(len(irr_results))))

    # Withdrawal description
    first_wd = next((wd for wd in config.withdrawal_data if wd.withdrawal_amount > 0), None)
    wd_desc = ""
    if first_wd:
        wd_desc = f"从第{first_wd.year}年起每年提取 {pi.currency_symbol}{first_wd.withdrawal_amount:,.0f}"

    html = _HTML_TEMPLATE.format(
        product_name=pi.product_name,
        product_name_en=pi.product_name_en,
        insurer=pi.insurer,
        insured_name=pi.insured_name,
        age_at_issue=pi.age_at_issue,
        gender="男" if pi.gender == "M" else "女",
        currency=pi.currency,
        currency_symbol=pi.currency_symbol,
        annual_premium=f"{pi.annual_premium:,.0f}",
        payment_years=pi.payment_years,
        total_premium=f"{pi.total_premium:,.0f}",
        coverage_type=pi.coverage_type,
        plan_date=pi.plan_date,
        primary_color=brand.primary_color,
        accent_color=brand.accent_color,
        logo_text=brand.logo_text,
        chart_data_json=json_module.dumps(chart_data),
        cv_chart_data_json=json_module.dumps(cv_chart_data),
        nw_rows=nw_rows_html,
        wd_rows=wd_rows_html,
        full_nw_rows=full_nw_rows,
        full_wd_rows=full_wd_rows,
        wd_description=wd_desc,
        has_withdrawal='true' if config.withdrawal_data else 'false',
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def _fmt_irr(val):
    """Format IRR value for HTML display."""
    if val is None:
        return '<span class="irr-na">N/A</span>'
    pct = val * 100
    css_class = "irr-positive" if pct >= 0 else "irr-negative"
    return f'<span class="{css_class}">{pct:.2f}%</span>'


def _fmt_money(val):
    """Format currency value."""
    if val is None:
        return '-'
    return f"{val:,.0f}"


def _build_nw_table_rows(config, irr_results, indices):
    """Build HTML table rows for no-withdrawal scenario."""
    rows = []
    pi = config.policy_info
    for i in indices:
        rec = config.yearly_data[i]
        irr = irr_results[i]
        highlight = ''
        if rec.year in config.display.highlight_years:
            highlight = ' class="highlight"'
        elif rec.age in config.display.highlight_ages:
            highlight = ' class="highlight-age"'

        rows.append(f"""<tr{highlight}>
            <td class="center">{rec.year}</td>
            <td class="center">{rec.age}</td>
            <td class="right">{_fmt_money(rec.cumulative_premium)}</td>
            <td class="right">{_fmt_money(rec.guaranteed_cash_value)}</td>
            <td class="right">{_fmt_money(rec.total_surrender_value)}</td>
            <td class="right">{_fmt_irr(irr['irr_no_withdrawal_total'])}</td>
            <td class="right">{_fmt_money(rec.total_death_benefit)}</td>
        </tr>""")
    return '\n'.join(rows)


def _build_wd_table_rows(config, irr_results, indices):
    """Build HTML table rows for withdrawal scenario."""
    if not config.withdrawal_data:
        return '<tr><td colspan="7" class="center">No withdrawal data</td></tr>'

    wd_by_year = {wd.year: wd for wd in config.withdrawal_data}
    cumulative_wd = 0.0

    # Pre-calculate cumulative withdrawals
    cum_map = {}
    running = 0.0
    for wd in config.withdrawal_data:
        running += wd.withdrawal_amount
        cum_map[wd.year] = running

    rows = []
    for i in indices:
        irr = irr_results[i]
        year = irr['year']
        age = irr['age']

        if year not in wd_by_year:
            continue

        wd_rec = wd_by_year[year]
        highlight = ''
        if year in config.display.highlight_years:
            highlight = ' class="highlight"'
        elif age in config.display.highlight_ages:
            highlight = ' class="highlight-age"'

        rows.append(f"""<tr{highlight}>
            <td class="center">{year}</td>
            <td class="center">{age}</td>
            <td class="right">{_fmt_money(wd_rec.withdrawal_amount)}</td>
            <td class="right">{_fmt_money(cum_map.get(year, 0))}</td>
            <td class="right">{_fmt_money(wd_rec.remaining_surrender_guaranteed)}</td>
            <td class="right">{_fmt_money(wd_rec.remaining_surrender_total)}</td>
            <td class="right">{_fmt_irr(irr['irr_withdrawal_total'])}</td>
        </tr>""")
    return '\n'.join(rows)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{product_name} - IRR Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
:root {{
    --primary: {primary_color};
    --accent: {accent_color};
    --bg: #f8f9fa;
    --card-bg: #ffffff;
    --text: #1a1a1a;
    --text-light: #666666;
    --border: #e9ecef;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}}
.container {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 20px 40px;
}}

/* Header */
.header {{
    background: var(--primary);
    color: white;
    padding: 24px 0;
    margin-bottom: 24px;
}}
.header-inner {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 20px;
    display: flex;
    align-items: center;
    gap: 20px;
}}
.logo {{
    font-size: 28px;
    font-weight: 800;
    letter-spacing: 2px;
}}
.logo-img {{
    height: 44px;
    object-fit: contain;
}}
.header-text h1 {{
    font-size: 20px;
    font-weight: 600;
}}
.header-text p {{
    font-size: 14px;
    opacity: 0.85;
}}

/* Cards */
.card {{
    background: var(--card-bg);
    border-radius: 10px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.06);
    padding: 24px;
    margin-bottom: 24px;
}}
.card h2 {{
    color: var(--primary);
    font-size: 18px;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--primary);
}}
.card h3 {{
    color: var(--text);
    font-size: 15px;
    margin: 16px 0 10px;
}}

/* Summary Grid */
.summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
}}
.summary-item {{
    padding: 10px 14px;
    background: var(--bg);
    border-radius: 6px;
}}
.summary-item .label {{
    font-size: 12px;
    color: var(--text-light);
    margin-bottom: 2px;
}}
.summary-item .value {{
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
}}

/* Tables */
.table-wrapper {{
    overflow-x: auto;
    margin-top: 12px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
th {{
    background: var(--primary);
    color: white;
    padding: 10px 12px;
    text-align: center;
    font-weight: 600;
    white-space: nowrap;
    font-size: 12px;
}}
td {{
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
}}
td.center {{ text-align: center; }}
td.right {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr.highlight {{ background: #FFF8E1; }}
tr.highlight-age {{ background: #E3F2FD; }}
tr:hover {{ background: #f5f5f5; }}

/* IRR colors */
.irr-positive {{ color: #2E7D32; font-weight: 700; }}
.irr-negative {{ color: #C62828; font-weight: 700; }}
.irr-na {{ color: #9E9E9E; font-style: italic; }}

/* Chart */
.chart-container {{
    position: relative;
    height: 400px;
    margin: 16px 0;
}}

/* Tabs */
.tabs {{
    display: flex;
    gap: 4px;
    margin-bottom: 0;
}}
.tab {{
    padding: 10px 20px;
    cursor: pointer;
    border: none;
    background: var(--bg);
    color: var(--text-light);
    font-size: 14px;
    font-weight: 600;
    border-radius: 8px 8px 0 0;
    transition: all 0.2s;
}}
.tab.active {{
    background: var(--card-bg);
    color: var(--primary);
    box-shadow: 0 -2px 8px rgba(0,0,0,0.05);
}}
.tab-content {{
    display: none;
}}
.tab-content.active {{
    display: block;
}}

/* Collapsible */
.collapsible-btn {{
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 16px;
    cursor: pointer;
    font-size: 14px;
    color: var(--text-light);
    width: 100%;
    text-align: left;
    margin-top: 16px;
    transition: all 0.2s;
}}
.collapsible-btn:hover {{
    background: var(--bg);
    color: var(--text);
}}
.collapsible-content {{
    display: none;
    margin-top: 8px;
}}

/* Footer */
.footer {{
    text-align: center;
    color: var(--text-light);
    font-size: 12px;
    padding: 20px;
    border-top: 1px solid var(--border);
    margin-top: 20px;
}}

/* Print styles */
@media print {{
    body {{ background: white; }}
    .header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    th {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .no-print {{ display: none !important; }}
    .collapsible-content {{ display: block !important; }}
    .card {{ box-shadow: none; border: 1px solid #ddd; page-break-inside: avoid; }}
    tr {{ page-break-inside: avoid; }}
}}

/* Scenario comparison side-by-side */
.comparison {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
}}
@media (max-width: 900px) {{
    .comparison {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
    <div class="header-inner">
        <img src="https://companieslogo.com/img/orig/1299.HK_BIG.D-f5e85a14.png?t=1720244490" alt="{logo_text}" class="logo-img">
        <div class="header-text">
            <h1>{product_name}</h1>
            <p>IRR Analysis Report | 内部收益率分析报告</p>
        </div>
    </div>
</div>

<div class="container">

<!-- Policy Summary -->
<div class="card">
    <h2>保单摘要 Policy Summary</h2>
    <div class="summary-grid">
        <div class="summary-item">
            <div class="label">受保人 Insured</div>
            <div class="value">{insured_name}</div>
        </div>
        <div class="summary-item">
            <div class="label">投保年龄 / 性别</div>
            <div class="value">{age_at_issue} / {gender}</div>
        </div>
        <div class="summary-item">
            <div class="label">年缴保费 Annual Premium</div>
            <div class="value">{currency_symbol}{annual_premium}</div>
        </div>
        <div class="summary-item">
            <div class="label">缴费年期 Payment Period</div>
            <div class="value">{payment_years} 年</div>
        </div>
        <div class="summary-item">
            <div class="label">总保费 Total Premium</div>
            <div class="value">{currency_symbol}{total_premium}</div>
        </div>
        <div class="summary-item">
            <div class="label">保障类型 Coverage</div>
            <div class="value">{coverage_type}</div>
        </div>
        <div class="summary-item">
            <div class="label">保单货币 Currency</div>
            <div class="value">{currency}</div>
        </div>
        <div class="summary-item">
            <div class="label">现金提取方案 Withdrawal</div>
            <div class="value">{wd_description}</div>
        </div>
    </div>
</div>

<!-- Cash Value Chart -->
<div class="card">
    <h2>累积现金价值走势图 Cumulative Cash Value</h2>
    <div class="chart-container">
        <canvas id="cvChart"></canvas>
    </div>
</div>

<!-- IRR Chart -->
<div class="card">
    <h2>IRR 走势图 IRR Performance Chart</h2>
    <div class="chart-container">
        <canvas id="irrChart"></canvas>
    </div>
</div>

<!-- Key Milestones - Tabbed -->
<div class="card">
    <h2>关键年度 IRR 对比 Key Milestones</h2>
    <div class="tabs">
        <button class="tab active" onclick="switchTab('nw')">不提取 No Withdrawal</button>
        <button class="tab" onclick="switchTab('wd')">提取 With Withdrawal</button>
    </div>

    <!-- No Withdrawal Tab -->
    <div id="tab-nw" class="tab-content active">
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Year<br>年期</th>
                        <th>Age<br>年龄</th>
                        <th>Cumulative Premium<br>累计保费</th>
                        <th>Guaranteed CV<br>保证现金价值</th>
                        <th>Total Surrender<br>退保总额</th>
                        <th>IRR (Total)<br>预期IRR</th>
                        <th>Death Benefit<br>身故赔偿</th>
                    </tr>
                </thead>
                <tbody>
                    {nw_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- Withdrawal Tab -->
    <div id="tab-wd" class="tab-content">
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Year<br>年期</th>
                        <th>Age<br>年龄</th>
                        <th>Withdrawal<br>当年提取</th>
                        <th>Cumul. Withdrawals<br>累计提取</th>
                        <th>Remaining Guaranteed<br>剩余保证</th>
                        <th>Remaining Total<br>剩余退保总额</th>
                        <th>IRR (Total)<br>预期IRR</th>
                    </tr>
                </thead>
                <tbody>
                    {wd_rows}
                </tbody>
            </table>
        </div>
    </div>
</div>

<!-- Full Data (Collapsible) -->
<div class="card no-print">
    <h2>完整数据 Full Data</h2>
    <button class="collapsible-btn" onclick="toggleSection('full-nw')">
        ▸ 不提取完整数据 No Withdrawal Full Data
    </button>
    <div id="full-nw" class="collapsible-content">
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Year</th><th>Age</th><th>Cumul. Premium</th>
                        <th>Guaranteed CV</th><th>Total Surrender</th>
                        <th>IRR (Total)</th>
                        <th>Death Benefit</th>
                    </tr>
                </thead>
                <tbody>{full_nw_rows}</tbody>
            </table>
        </div>
    </div>

    <button class="collapsible-btn" onclick="toggleSection('full-wd')">
        ▸ 提取完整数据 Withdrawal Full Data
    </button>
    <div id="full-wd" class="collapsible-content">
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Year</th><th>Age</th><th>Withdrawal</th>
                        <th>Cumul. Withdrawals</th><th>Remaining Guar.</th>
                        <th>Remaining Total</th><th>IRR (Total)</th>
                    </tr>
                </thead>
                <tbody>{full_wd_rows}</tbody>
            </table>
        </div>
    </div>
</div>

<!-- Footer -->
<div class="footer">
    <p>本报告由保单IRR计算工具自动生成 | Generated by Insurance IRR Calculator</p>
    <p>数据来源：{insurer} {product_name} 保单计划书</p>
    <p>免责声明：IRR仅供参考，非保证金额部分的实际回报可能与预期不同。</p>
    <p style="margin-top: 12px; font-weight: 600;">版权所有 &copy;GengWealth</p>
</div>

</div>

<script>
// Cash Value Chart
const cvData = {cv_chart_data_json};
const cvCtx = document.getElementById('cvChart').getContext('2d');
const cvDatasets = [
    {{
        label: '不提取 退保总额 No Withdrawal Surrender',
        data: cvData.nw_total,
        borderColor: '{primary_color}',
        backgroundColor: 'rgba(200, 16, 46, 0.05)',
        borderWidth: 2.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 10,
    }},
];
if (cvData.wd_total && cvData.wd_total.length > 0) {{
    cvDatasets.push({{
        label: '提取 总价值 Withdrawal Total Value',
        data: cvData.wd_total,
        borderColor: '#1565C0',
        borderWidth: 2.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 10,
        spanGaps: true,
    }});
}}
new Chart(cvCtx, {{
    type: 'line',
    data: {{
        labels: cvData.labels,
        datasets: cvDatasets,
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{
            mode: 'index',
            intersect: false,
        }},
        plugins: {{
            tooltip: {{
                callbacks: {{
                    label: function(ctx) {{
                        if (ctx.parsed.y === null) return null;
                        return ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString();
                    }}
                }}
            }},
            legend: {{
                position: 'bottom',
                labels: {{
                    usePointStyle: true,
                    padding: 16,
                    font: {{ size: 12 }}
                }}
            }},
            annotation: {{
                annotations: {{
                    premiumLine: {{
                        type: 'line',
                        yMin: cvData.total_premium,
                        yMax: cvData.total_premium,
                        borderColor: '#999',
                        borderWidth: 1.5,
                        borderDash: [6, 4],
                        label: {{
                            content: 'Total Premium $' + cvData.total_premium.toLocaleString(),
                            display: true,
                            position: 'start',
                            font: {{ size: 10 }},
                            color: '#666',
                            backgroundColor: 'rgba(255,255,255,0.8)',
                        }}
                    }}
                }}
            }}
        }},
        scales: {{
            x: {{
                title: {{
                    display: true,
                    text: 'Policy Year 保单年期',
                    font: {{ size: 13 }}
                }},
                ticks: {{
                    callback: function(val, idx) {{
                        const label = cvData.labels[idx];
                        return (label % 5 === 0) ? label : '';
                    }},
                    maxRotation: 0,
                }}
            }},
            y: {{
                title: {{
                    display: true,
                    text: 'Cash Value ({currency_symbol})',
                    font: {{ size: 13 }}
                }},
                ticks: {{
                    callback: function(val) {{
                        return '$' + val.toLocaleString();
                    }}
                }},
                beginAtZero: true,
            }}
        }}
    }}
}});

// IRR Chart
const chartData = {chart_data_json};
const hasWithdrawal = {has_withdrawal};

const ctx = document.getElementById('irrChart').getContext('2d');
const datasets = [
    {{
        label: '不提取 IRR (预期Total)',
        data: chartData.nw_total,
        borderColor: '{primary_color}',
        backgroundColor: 'rgba(200, 16, 46, 0.05)',
        borderWidth: 2.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 10,
        spanGaps: true,
    }},
];

if (hasWithdrawal) {{
    datasets.push(
        {{
            label: '提取 IRR (预期Total)',
            data: chartData.wd_total,
            borderColor: '#1565C0',
            borderWidth: 2.5,
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHitRadius: 10,
            spanGaps: true,
        }}
    );
}}

new Chart(ctx, {{
    type: 'line',
    data: {{
        labels: chartData.labels,
        datasets: datasets,
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{
            mode: 'index',
            intersect: false,
        }},
        plugins: {{
            title: {{
                display: false,
            }},
            tooltip: {{
                callbacks: {{
                    label: function(ctx) {{
                        if (ctx.parsed.y === null) return null;
                        return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + '%';
                    }}
                }}
            }},
            legend: {{
                position: 'bottom',
                labels: {{
                    usePointStyle: true,
                    padding: 16,
                    font: {{ size: 12 }}
                }}
            }},
            annotation: {{
                annotations: {{
                    zeroline: {{
                        type: 'line',
                        yMin: 0,
                        yMax: 0,
                        borderColor: '#666',
                        borderWidth: 1,
                        borderDash: [4, 4],
                        label: {{
                            content: 'Break-even 0%',
                            display: true,
                            position: 'start',
                            font: {{ size: 10 }},
                            color: '#666',
                            backgroundColor: 'rgba(255,255,255,0.8)',
                        }}
                    }}
                }}
            }}
        }},
        scales: {{
            x: {{
                title: {{
                    display: true,
                    text: 'Policy Year 保单年期',
                    font: {{ size: 13 }}
                }},
                ticks: {{
                    callback: function(val, idx) {{
                        const label = chartData.labels[idx];
                        return (label % 5 === 0) ? label : '';
                    }},
                    maxRotation: 0,
                }}
            }},
            y: {{
                title: {{
                    display: true,
                    text: 'IRR (%)',
                    font: {{ size: 13 }}
                }},
                ticks: {{
                    callback: function(val) {{
                        return val.toFixed(1) + '%';
                    }}
                }},
                suggestedMin: -20,
                suggestedMax: 8,
            }}
        }}
    }}
}});

// Tab switching
function switchTab(tabId) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.add('active');
    event.target.classList.add('active');
}}

// Collapsible sections
function toggleSection(id) {{
    const el = document.getElementById(id);
    const btn = el.previousElementSibling;
    if (el.style.display === 'block') {{
        el.style.display = 'none';
        btn.textContent = btn.textContent.replace('▾', '▸');
    }} else {{
        el.style.display = 'block';
        btn.textContent = btn.textContent.replace('▸', '▾');
    }}
}}
</script>

</body>
</html>
"""
