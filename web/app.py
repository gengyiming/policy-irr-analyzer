"""
Insurance Policy IRR Analyzer - Web Application

Upload an AIA policy PDF to generate IRR analysis reports.
"""
import os
import uuid
import shutil
import tempfile
import threading
import time
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect,
    url_for, send_file, abort
)

# Add project root to path so we can import src modules
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pdf_extractor import AIAPDFExtractor
from src.config import load_policy_from_dict
from src.irr import calculate_all_irr
from src.excel_writer import create_excel_report
from src.html_writer import create_html_report

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# Task storage: task_id -> {dir, excel_path, html_path, policy_info, warnings, created_at}
_tasks = {}
_tasks_lock = threading.Lock()

CLEANUP_AFTER_SECONDS = 3600  # 1 hour


def _cleanup_old_tasks():
    """Background thread to clean up expired task files."""
    while True:
        time.sleep(300)  # Check every 5 minutes
        now = time.time()
        expired = []
        with _tasks_lock:
            for task_id, info in _tasks.items():
                if now - info['created_at'] > CLEANUP_AFTER_SECONDS:
                    expired.append(task_id)
            for task_id in expired:
                info = _tasks.pop(task_id)
                shutil.rmtree(info['dir'], ignore_errors=True)


# Start cleanup thread
_cleanup_thread = threading.Thread(target=_cleanup_old_tasks, daemon=True)
_cleanup_thread.start()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/analyze', methods=['POST'])
def analyze():
    if 'pdf_file' not in request.files:
        return render_template('error.html',
                               title='未选择文件',
                               message='请选择一个 PDF 文件上传。'), 400

    file = request.files['pdf_file']
    if file.filename == '':
        return render_template('error.html',
                               title='未选择文件',
                               message='请选择一个 PDF 文件上传。'), 400

    if not file.filename.lower().endswith('.pdf'):
        return render_template('error.html',
                               title='文件格式错误',
                               message='仅支持 PDF 文件。请上传 AIA 保单计划书 PDF。'), 400

    # Create task directory
    task_id = uuid.uuid4().hex[:12]
    task_dir = tempfile.mkdtemp(prefix=f'irr-{task_id}-')

    try:
        # Save uploaded PDF
        pdf_path = os.path.join(task_dir, 'upload.pdf')
        file.save(pdf_path)

        # 1. Extract data from PDF
        extractor = AIAPDFExtractor(pdf_path)
        data = extractor.extract()
        warnings = extractor.warnings

        # 2. Load and validate
        config = load_policy_from_dict(data)

        # 3. Calculate IRR
        irr_results = calculate_all_irr(config)

        # 4. Generate reports
        slug = config.policy_info.insurer.lower().replace(' ', '_')
        excel_path = os.path.join(task_dir, f'{slug}_irr_report.xlsx')
        html_path = os.path.join(task_dir, f'{slug}_irr_report.html')

        create_excel_report(config, irr_results, excel_path)
        create_html_report(config, irr_results, html_path)

        # Store task info
        with _tasks_lock:
            _tasks[task_id] = {
                'dir': task_dir,
                'excel_path': excel_path,
                'html_path': html_path,
                'excel_name': f'{slug}_irr_report.xlsx',
                'html_name': f'{slug}_irr_report.html',
                'policy_info': {
                    'product_name': config.policy_info.product_name,
                    'insured_name': config.policy_info.insured_name,
                    'currency_symbol': config.policy_info.currency_symbol,
                    'annual_premium': config.policy_info.annual_premium,
                    'payment_years': config.policy_info.payment_years,
                    'total_premium': config.policy_info.total_premium,
                },
                'warnings': warnings,
                'created_at': time.time(),
            }

        return redirect(url_for('result', task_id=task_id))

    except Exception as e:
        shutil.rmtree(task_dir, ignore_errors=True)
        return render_template('error.html',
                               title='分析失败',
                               message=f'PDF 解析或 IRR 计算过程中出错：{str(e)}'), 500


@app.route('/result/<task_id>')
def result(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return render_template('error.html',
                               title='任务不存在',
                               message='该分析结果已过期或不存在，请重新上传 PDF。'), 404

    return render_template('result.html',
                           task_id=task_id,
                           policy_info=task['policy_info'],
                           warnings=task['warnings'])


@app.route('/report/<task_id>')
def report(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        abort(404)
    return send_file(task['html_path'], mimetype='text/html')


@app.route('/download/<task_id>/excel')
def download_excel(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        abort(404)
    return send_file(task['excel_path'], as_attachment=True,
                     download_name=task['excel_name'])


@app.route('/download/<task_id>/html')
def download_html(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        abort(404)
    return send_file(task['html_path'], as_attachment=True,
                     download_name=task['html_name'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()
    app.run(host='0.0.0.0', port=args.port, debug=True)
