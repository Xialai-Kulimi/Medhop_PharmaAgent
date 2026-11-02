"""Measure client-visible streaming TTFT and request-scoped server timings."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.request
import uuid
from quality_benchmark30 import stats
from power_monitor import PowerMonitor


def get(base, suffix):
    with urllib.request.urlopen(base.rstrip('/')+'/'+suffix, timeout=10) as r:
        return json.load(r)


def loaded(health):
    result = {health['model_loaded']} if isinstance(health.get('model_loaded'), str) else set()
    for row in health.get('all_models_loaded', []):
        if isinstance(row, dict) and row.get('model_name'):
            result.add(row['model_name'])
    return result


def consume(response, stream, started):
    row = {'client_ttft_seconds': None, 'first_generated_chunk_seconds': None,
           'server_generation_tps': None, 'response': '', 'reasoning': '', 'usage': None,
           'timings': None, 'completed': False, 'finish_reason': None}
    events = []
    if stream:
        for line in response:
            if not line.startswith(b'data:'):
                continue
            payload = line[5:].strip()
            if payload == b'[DONE]':
                row['completed'] = True
                break
            obj = json.loads(payload)
            events.append(obj)
            if obj.get('error'):
                raise ValueError(str(obj['error']))
            for choice in obj.get('choices', []):
                delta = choice.get('delta') or {}
                content, reasoning = delta.get('content') or '', delta.get('reasoning_content') or ''
                elapsed = time.perf_counter()-started
                if (content or reasoning) and row['first_generated_chunk_seconds'] is None:
                    row['first_generated_chunk_seconds'] = elapsed
                if content and row['client_ttft_seconds'] is None:
                    row['client_ttft_seconds'] = elapsed
                row['response'] += content
                row['reasoning'] += reasoning
                if choice.get('finish_reason') is not None:
                    row['finish_reason'] = choice['finish_reason']
    else:
        obj = json.load(response); events.append(obj)
        if obj.get('error'):
            raise ValueError(str(obj['error']))
        choices = obj.get('choices', [])
        if choices:
            row['response'] = (choices[0].get('message') or {}).get('content') or ''
            row['finish_reason'] = choices[0].get('finish_reason')
            row['completed'] = row['finish_reason'] is not None
    for obj in events:
        if obj.get('usage'):
            row['usage'] = obj['usage']
        if obj.get('timings'):
            row['timings'] = obj['timings']
    if row['timings']:
        tps = row['timings'].get('predicted_per_second')
        if isinstance(tps, (int, float)) and 0 < tps < float('inf'):
            row['server_generation_tps'] = tps
    row['seconds'] = time.perf_counter()-started
    row['raw_events'] = events
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:13305/api/v1')
    p.add_argument('--model')
    p.add_argument('--probe', action='store_true')
    p.add_argument('--runs', type=int, default=30)
    p.add_argument('--warmup', type=int, default=2)
    p.add_argument('--mode', choices=['stream','nonstream','both'], default='both')
    p.add_argument('--max-tokens', type=int, default=256)
    p.add_argument('--timeout', type=float, default=180)
    args = p.parse_args()
    health = get(args.url, 'health')
    if args.probe:
        print(json.dumps({'health': health, 'loaded_models_detected': sorted(loaded(health))}, ensure_ascii=False, indent=2)); return
    if not args.model or args.runs < 1 or args.warmup < 0 or args.max_tokens < 1 or args.timeout <= 0:
        p.error('請指定已載入的 --model；runs/max-tokens/timeout須大於0，warmup不可負數。')
    if args.model not in loaded(health):
        p.error('health未確認此模型已載入；停止，避免自動載入或切換共享模型。請貼回 --probe 結果。')
    out = Path('runtime')/'model-benchmarks'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    prompt = ('請用繁體中文寫一篇約300字的說明，解釋藥師如何依據藥袋、病人年齡、過敏史及可靠來源進行用藥核對。'
              '請完整敘述步驟，不給任何具體藥名、個人劑量或醫療處置。')
    modes = [True, False] if args.mode == 'both' else [args.mode == 'stream']
    rows = []
    metadata = {'model': args.model, 'url': args.url, 'initial_health': health,
        'runs_per_mode': args.runs, 'warmup_per_mode': args.warmup, 'prompt': prompt,
        'temperature': 0, 'max_tokens': args.max_tokens, 'model_load_seconds': None,
        'cache_policy': '相同prompt重複；不清除共享伺服器cache，保留返回timings與usage；為熱服務條件。',
        'scope': '獨立模型API測試，非30題Agent全程或瀏覽器TTFT；兩種模式分開統計。'}
    print('結果資料夾：'+str(out.resolve()), flush=True)
    def write():
        groups = {}
        for stream in modes:
            items = [r for r in rows if r['stream'] == stream and not r['warmup']]
            good = [r for r in items if r.get('completed') and not r.get('error')]
            groups['stream' if stream else 'nonstream'] = {'planned_runs': args.runs,
                'recorded_runs': len(items), 'completed_runs': len(good),
                'completion_rate': len(good)/args.runs,
                'client_ttft_seconds': stats([r['client_ttft_seconds'] for r in good if r.get('client_ttft_seconds') is not None]),
                'server_generation_tps': stats([r['server_generation_tps'] for r in good if r.get('server_generation_tps') is not None]),
                'request_seconds': stats([r['seconds'] for r in good])}
        value = {'metadata': metadata, 'groups': groups, 'results': rows,
            'limits': ['stream：從送出請求至收到第一個非空content delta；reasoning另計，SSE chunk不等於一個token。',
                       'tokens/sec只採用同一次回應timings.predicted_per_second；沒有就留空，不用字數或共享/stats猜測。',
                       'nonstream不量client TTFT；both預設共60次正式要求，加上4次暖機，兩模式不可混成同一樣本。',
                       '共享伺服器可能有排隊或cache；結果保留原始回應，未驗證NPU/GPU/CPU實際分工。']}
        (out/'model_report.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        lines = ['# 模型API效能測試', '', '|模式|完成/預定|TTFT P50/P95 秒|生成TPS P50/P95|', '|---|---|---|---|']
        for name, g in groups.items():
            t, s = g['client_ttft_seconds'], g['server_generation_tps']
            lines.append(f'|{name}|{g["completed_runs"]}/{g["planned_runs"]}|{t["p50"]} / {t["p95"]}|{s["p50"]} / {s["p95"]}|')
        lines += ['', 'None表示沒有可用量測，不是0。功耗看同資料夾power_summary.json，不能把感測器domain當整機耗電。']
        (out/'model_report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    with PowerMonitor(out):
        for stream in modes:
            for i in range(args.warmup+args.runs):
                warmup = i < args.warmup
                row = {'stream': stream, 'warmup': warmup, 'index': i+1, 'completed': False}
                start = time.perf_counter()
                try:
                    current = get(args.url, 'health')
                    if args.model not in loaded(current):
                        raise RuntimeError('模型不再載入；停止此測試，避免切換共享模型。')
                    payload = {'model': args.model, 'messages': [{'role':'user','content':prompt}],
                        'temperature': 0, 'max_tokens': args.max_tokens, 'stream': stream}
                    req = urllib.request.Request(args.url.rstrip('/')+'/chat/completions',
                        data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
                    start = time.perf_counter()
                    with urllib.request.urlopen(req, timeout=args.timeout) as response:
                        row.update(consume(response, stream, start))
                except KeyboardInterrupt:
                    row['error'] = '使用者中止'; raise
                except Exception as exc:
                    row['error'] = type(exc).__name__+': '+str(exc)
                finally:
                    row['wall_seconds'] = time.perf_counter()-start
                    rows.append(row); write()
                    print(('暖機' if warmup else '正式')+' '+str(i+1)+' stream='+str(stream)+' 完成='+str(row['completed']), flush=True)
                if row.get('error'):
                    raise RuntimeError(row['error']+'；已保留紀錄，不再送出後續要求。')
    print(out/'model_report.md')


if __name__ == '__main__':
    main()
