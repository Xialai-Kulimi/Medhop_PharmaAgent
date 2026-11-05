"""Run Q01 through the real API in a separate, persistent test runtime.

Usage (from the project directory): .venv/bin/python quality_auto_q01.py
No existing server, patient database, model service or port is modified.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


def agent_intake(call, text, deadline):
    """Chat input goes to the pharmacist agent (needs MODEL_URL with tool calling); wait for the run and any review it started."""
    run_id = call('POST', '/api/agent', {'message': text})['id']
    while True:
        run = call('GET', '/api/agent/' + run_id)
        if run['status'] != 'running':
            break
        if time.perf_counter() > deadline:
            call('POST', '/api/agent/cancel', {'id': run_id})
            raise TimeoutError('Agent 逾時：' + run_id)
        time.sleep(.2)
    for action in run.get('actions', []):
        while action.get('type') == 'review' and call('GET', '/api/jobs/' + action['job'])['status'] == 'running':
            time.sleep(.1)
    return {'patient': run.get('patient'), 'steps': run.get('steps'), 'message': run.get('reply') or run.get('error'), 'action': 'agent', 'agent_run': run}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--output-root', type=Path, help='測試紀錄資料夾；預設為專案 runtime/quality-tests')
    args = parser.parse_args()
    root = args.project.resolve()
    if not (root / 'asgi.py').is_file():
        parser.error('請先 cd 到包含 asgi.py 的專案資料夾，或指定 --project。')
    if args.timeout <= 0:
        parser.error('--timeout 必須大於 0。')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    output = (args.output_root.resolve() if args.output_root else root / 'runtime' / 'quality-tests') / run_id
    output.mkdir(parents=True, exist_ok=False)
    # Must precede app imports. PrivateStore derives its key from DB.parent.
    os.environ['MEDLINK_RUNTIME'] = str(output / 'isolated-runtime')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    report = {
        'case_id': 'Q01', 'run_id': run_id, 'status': 'error',
        'input': '新病人林怡安，32歲女性，目前懷孕12週，有第二型糖尿病。\n目前用藥：\nGlibenclamide 5mg，每次一錠，一天一次',
        'timing_scope': 'in-process API: workflow POST through completed review; excludes startup, browser rendering and later PDF checks',
        'model_requested': False, 'existing_patient_data_used': False,
        'manual_review': {
            'clinical_interpretation': '待藥師覆核',
            'PDF_visual_and_OCR_verification': '待人工核對',
            'no_unrequested_treatment_or_dose_change': '待人工檢查完整輸出；程式僅比對病歷用藥未變',
        },
        'checks': {},
    }
    try:
        from fastapi.testclient import TestClient
        from asgi import app
        def save(name, value):
            (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        def data(response):
            response.raise_for_status()
            return response.json()
        print('1/3 載入系統，使用獨立測試病歷……', flush=True)
        with TestClient(app, base_url='http://127.0.0.1') as client:
            started = time.perf_counter()
            call = lambda method, route, payload=None: data(client.request(method, route, json=payload) if payload is not None else client.request(method, route))
            intake = agent_intake(call, report['input'], started + args.timeout)
            report['workflow_seconds'] = time.perf_counter() - started
            save('workflow.json', intake)
            patient = intake.get('patient') or {}
            meds = [m for m in patient.get('medications', []) if m.get('status') == 'active']
            print('2/3 執行仿單檢索與規則檢核（不呼叫模型）……', flush=True)
            job_id = data(client.post('/api/review', json={'use_model': False}))['id']
            while True:
                snapshot = data(client.get('/api/jobs/' + job_id))
                if snapshot['status'] in {'done', 'error', 'cancelled'}:
                    break
                if time.perf_counter() - started > args.timeout:
                    client.post('/api/cancel', json={'id': job_id})
                    save('review.json', snapshot)
                    raise TimeoutError('檢查逾時；保留已取得的輸出。')
                time.sleep(.05)
            report['api_task_seconds'] = time.perf_counter() - started
            report['poll_interval_seconds'] = .05
            save('review.json', snapshot)
            assessment = snapshot.get('assessment') or {}
            alerts = assessment.get('population_checks', [])
            target = [a for a in alerts if a.get('drug', '').casefold() == 'glibenclamide'
                      and a.get('status') == 'contraindicated']
            sources = {s['id']: s for s in snapshot.get('sources', [])}
            refs = [sources[sid] for a in target for sid in a.get('source_ids', []) if sid in sources]
            expected_refs = [s for s in refs if s.get('file') == 'A0047151G0.pdf' and s.get('page') == 1]
            pdf_checks = []
            for source in expected_refs:
                response = client.get('/documents/' + source['file'])
                pdf_checks.append({'file': source['file'], 'page': source['page'],
                                   'http_status': response.status_code,
                                   'pdf_signature': response.content.startswith(b'%PDF-')})
            save('citation-checks.json', pdf_checks)
            after = data(client.get('/api/patients')).get('patient') or {}
            fingerprint = lambda p: [(m.get('name'), m.get('dose'), m.get('frequency'), m.get('status'))
                                      for m in p.get('medications', [])]
            checks = {
                'job_completed_without_error': snapshot['status'] == 'done' and not snapshot.get('error'),
                'name': patient.get('name') == '林怡安',
                'age': patient.get('age') == 32,
                'sex': patient.get('sex') == '女',
                'pregnancy': patient.get('pregnancy') == 'pregnant' and patient.get('pregnancy_weeks') == 12,
                'one_correct_drug': len(meds) == 1 and meds[0].get('name', '').casefold() == 'glibenclamide',
                'recorded_strength_5mg': len(meds) == 1 and re.fullmatch(r'5\s*(?:mg|毫克)', meds[0].get('dose', ''), re.I) is not None,
                'recorded_frequency_daily': len(meds) == 1 and meds[0].get('frequency') in {'一天一次', '每日一次', 'QD', 'qd'},
                'pregnancy_alert': bool(target),
                'contact_prescriber_action': bool(target) and all(a.get('action') == 'contact_prescriber' for a in target),
                'quoted_expected_page': bool(expected_refs) and all('孕婦或可能懷孕之婦女禁用本藥' in s.get('text', '') for s in expected_refs),
                'PDF_retrievable': bool(pdf_checks) and all(p['http_status'] == 200 and p['pdf_signature'] for p in pdf_checks),
                'OCR_disclosed': bool(expected_refs) and all(s.get('ocr') is True for s in expected_refs),
                'recorded_medications_unchanged_by_review': fingerprint(patient) == fingerprint(after),
            }
            report['checks'] = checks
            report['status'] = 'automatic_checks_passed' if all(checks.values()) else 'automatic_checks_failed'
            report['job_error'] = snapshot.get('error')
            report['agent'] = assessment.get('agent')
            report['answer'] = snapshot.get('answer')
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        lines = ['# Q01 自動測試紀錄', '', f"結果：{report['status']}",
                 f"API 任務時間：{report.get('api_task_seconds', '未完成')} 秒", '',
                 '| 自動核對項目 | 結果 |', '|---|---|']
        lines += [f"| {name} | {'通過' if passed else '未通過'} |" for name, passed in report['checks'].items()]
        lines += ['', '藥事正確性、原始 PDF 視讀及是否自行建議換藥／改劑量：待人工覆核。',
                  '每次一錠的實際服用量是否完整呈現：請核對 workflow.json 與 review.json。',
                  '本題使用固定虛構輸入；不是臨床正確率、瀏覽器端延遲或完整 30 題評估。',
                  '測試資料與 privacy.key 留在獨立 runtime，勿上傳 GitHub。']
        if report.get('error'):
            lines += ['', report['error']]
        (output / 'result.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print('3/3 記錄已儲存：' + str(output), flush=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'automatic_checks_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
