"""Merge observed agent results, existing model benchmark and pharmacist adjudication."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path


def power_measurement(path, start, end):
    def ts(value):
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if d.utcoffset() is None:
            raise ValueError('功耗時間必須有時區，例如 +00:00 或 Z。')
        return d.timestamp()
    points = []
    with path.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            w = float(row['power_w'])
            if not 0 <= w < float('inf'):
                raise ValueError('功耗必須是非負有限數值。')
            points.append((ts(row['timestamp_utc']), w))
    points.sort()
    a, b = ts(start), ts(end)
    if len(points) < 2 or points[0][0] > a or points[-1][0] < b or b <= a:
        raise ValueError('功耗樣本必須完整涵蓋本次開始至結束時間。')
    if any(y[0] <= x[0] for x, y in zip(points, points[1:])):
        raise ValueError('功耗樣本時間不可重複。')
    energy = 0
    peaks = []
    gaps = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        lo, hi = max(a, x0), min(b, x1)
        if hi <= lo:
            continue
        yl = y0+(y1-y0)*(lo-x0)/(x1-x0)
        yh = y0+(y1-y0)*(hi-x0)/(x1-x0)
        energy += (yl+yh)/2*(hi-lo)
        peaks += [yl, yh]
        gaps.append(x1-x0)
    return {'mean_w': energy/(b-a), 'peak_sampled_w': max(peaks), 'energy_j': energy,
            'window_seconds': b-a, 'max_sample_gap_seconds': max(gaps),
            'scope': '使用者提供之整機／量測迴路功耗；包括背景負載、評分與引用讀取，不能歸因於單一Agent或NPU。',
            'input_csv': str(path.resolve())}


def adjudication(path, case_ids):
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('藥師覆核表題號重複。')
    if {r['case_id'] for r in rows} != set(case_ids):
        raise ValueError('藥師覆核表題號必須和本次選定題目一致。')
    result = {}
    for field in ['clinical_correct', 'citation_support_correct', 'intervention_appropriate']:
        for row in rows:
            if row[field].strip() not in {'', 'pass', 'fail', 'na'}:
                raise ValueError(field+'只能填 pass、fail、na 或留空。')
            if row[field].strip() and (not row['reviewer'].strip() or not row['notes'].strip()):
                raise ValueError('每個已覆核結果須填覆核者與理由／來源。')
        known = [r for r in rows if r[field].strip() in {'pass', 'fail'}]
        pending = sum(not r[field].strip() for r in rows)
        passed = sum(r[field].strip() == 'pass' for r in known)
        result[field] = {'passed': passed, 'reviewed_applicable': len(known), 'pending': pending,
            'not_applicable': sum(r[field].strip() == 'na' for r in rows),
            'rate_reviewed_only': passed/len(known) if known else None,
            'final_rate': passed/len(known) if known and not pending else None}
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True, help='含 summary.json 的本次測試輸出資料夾')
    p.add_argument('--model-json', type=Path, help='既有 Lemonade bench JSON；不執行或重載模型')
    p.add_argument('--power-csv', type=Path, help='timestamp_utc,power_w；由實際量測設備提供')
    p.add_argument('--sensor-json', type=Path, help='本包power_monitor產生的power_summary.json；僅感測domain')
    args = p.parse_args(argv)
    summary = json.loads((args.run/'summary.json').read_text(encoding='utf-8-sig'))
    resources = []
    with (args.run/'resources.csv').open(encoding='utf-8-sig', newline='') as f:
        resources = list(csv.DictReader(f))
    result = {'agent': {k: v for k, v in summary.items() if k != 'results'},
        'pharmacist_review': None,
        'model_benchmarks': [], 'model_load_seconds': None, 'power': None, 'sensor_power': None,
        'peak_process_tree_rss_bytes': max((int(r['process_tree_rss_bytes']) for r in resources), default=None),
        'peak_system_used_bytes': max((int(r['system_used_bytes']) for r in resources), default=None)}
    # Validate selected IDs against the result metadata. Incomplete executions retain pending rows.
    selection = summary['metadata'].get('selected_ids', [r['id'] for r in summary['results']])
    result['pharmacist_review'] = adjudication(args.run/'pharmacist_review.csv', selection)
    if args.model_json:
        bench = json.loads(args.model_json.read_text(encoding='utf-8-sig'))
        if 'groups' in bench:
            for mode, group in bench['groups'].items():
                ttft = {k: (v*1000 if v is not None and k != 'n' else v)
                        for k,v in group['client_ttft_seconds'].items()}
                result['model_benchmarks'].append({'model': bench['metadata']['model'],
                    'scenario': mode, 'ttft_ms': ttft, 'generation_tps': group['server_generation_tps'],
                    'config': bench['metadata'], 'failed_runs': group['planned_runs']-group['completed_runs'],
                    'input_file': str(args.model_json.resolve()),
                    'scope': '獨立模型API測試，不是30題Agent首字延遲；TPS只讀同要求timings，缺值保留。'})
        for model in bench.get('models', []):
            for run in model.get('results', []):
                for scenario in run.get('scenarios', []):
                    result['model_benchmarks'].append({'model': model['model'], 'backend': run.get('backend'),
                        'context_size': run.get('ctx_size'), 'scenario': scenario.get('name'),
                        'ttft_ms': scenario.get('ttft_ms'), 'generation_tps': scenario.get('tps'),
                        'duration_ms': scenario.get('duration_ms'), 'failed_runs': scenario.get('failed_runs'),
                        'config': model.get('config'), 'timestamp': model.get('timestamp'),
                        'hardware': bench.get('hardware'), 'input_file': str(args.model_json.resolve()),
                        'scope': '既有獨立模型bench，不是30題Agent之首字延遲；共享主機背景負載需另記錄。'})
        if not result['model_benchmarks']:
            raise ValueError('model-json 不是支援的 Lemonade bench 結構。')
    if args.power_csv:
        result['power'] = power_measurement(args.power_csv, summary['metadata']['started_utc'],
                                            summary['metadata']['recorded_through_utc'])
    if args.sensor_json:
        result['sensor_power'] = json.loads(args.sensor_json.read_text(encoding='utf-8-sig'))
    (args.run/'kpi_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 六項KPI彙整', '', '|項目|數值／狀態|', '|---|---|',
        '|模型載入時間|未量測；不卸載共享模型|',
        '|首字延遲、生成速度|'+('見下方獨立模型測試' if result['model_benchmarks'] else '未匯入模型bench JSON')+'|',
        '|Agent任務耗時|P50 '+str(summary['case_latency_seconds_completed_only']['p50'])+' 秒；P95 '+str(summary['case_latency_seconds_completed_only']['p95'])+' 秒|',
        '|流程完成率|'+str(summary['completed_cases'])+'/'+str(summary['selected_cases'])+'|',
        '|自動條件通過率|'+str(summary['automatic_contract_passed_cases'])+'/'+str(summary['selected_cases'])+'；非臨床正確率|',
        '|臨床與引用正確性|見覆核統計；未填部分仍待藥師|',
        '|測試程序樹RSS峰值|'+str(result['peak_process_tree_rss_bytes'])+' bytes；不含獨立模型服務|',
        '|功耗|'+(str(result['power']['mean_w'])+' W（整機／量測迴路）' if result['power'] else '未量測')+'|', '',
        '## 獨立模型測試', '', '|情境|TTFT P50/P95 ms|生成TPS P50/P95|', '|---|---|---|']
    for r in result['model_benchmarks']:
        ttft, tps = r['ttft_ms'] or {}, r['generation_tps'] or {}
        lines.append('|'+str(r['scenario'])+'|'+str(ttft.get('p50'))+' / '+str(ttft.get('p95'))+'|'+str(tps.get('p50'))+' / '+str(tps.get('p95'))+'|')
    lines += ['', '## 藥師覆核統計', '', json.dumps(result['pharmacist_review'], ensure_ascii=False, indent=2),
        '', '未做相同模型、量化、工作負載及背景條件的對照測試，不作AMD較快／NPU已執行的結論。']
    if result['sensor_power'] is not None:
        lines += ['', '## 感測器功耗（獨立domain，非整機）', '',
                  '量測區間以原power_summary.json為準；匯入不表示與Agent或模型測試時間相同。', '',
                  '|感測器|平均W|採樣峰值W|涵蓋秒數|', '|---|---:|---:|---:|']
        for s in result['sensor_power'].get('sensors', []):
            lines.append('|'+str(s.get('name',s['path']))+'|'+str(s.get('mean_w'))+'|'+str(s.get('sampled_peak_w'))+'|'+str(s.get('covered_seconds'))+'|')
    (args.run/'kpi_report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(args.run/'kpi_report.md')


if __name__ == '__main__':
    main()
