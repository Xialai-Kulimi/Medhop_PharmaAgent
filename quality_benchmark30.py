"""Isolated mixed30 recorder. Mechanical checks are not clinical adjudication."""
import argparse
import csv
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).casefold()


def dose(value):
    return norm(value).replace('毫克', 'mg').replace('微克', 'mcg').replace('公克', 'g')


def freq(value):
    n = norm(value)
    return {'qd': '一天一次', '每日一次': '一天一次', 'bid': '一天兩次',
            '一天二次': '一天兩次', 'tid': '一天三次', 'qid': '一天四次',
            'prn': '需要時服用', '需要時': '需要時服用'}.get(n, n)


def meds(patient):
    return [m for m in (patient or {}).get('medications', []) if m.get('status') == 'active']


def fingerprint(patient):
    return [(m.get('id'), m.get('name'), m.get('dose'), m.get('frequency'), m.get('status'))
            for m in (patient or {}).get('medications', [])]


def stats(values):
    v = sorted(values)
    if not v:
        return {'n': 0, 'p50': None, 'p95': None}
    def percentile(q):
        p = (len(v)-1)*q
        lo = int(p)
        return v[lo] + (v[min(lo+1, len(v)-1)]-v[lo])*(p-lo)
    return {'n': len(v), 'p50': percentile(.5), 'p95': percentile(.95),
            'mean': statistics.mean(v), 'min': v[0], 'max': v[-1]}


class Sampler:
    def __init__(self, path):
        import psutil
        self.psutil = psutil
        self.process = psutil.Process()
        self.path = path
        self.rows = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def sample(self):
        rss = 0
        for p in [self.process] + self.process.children(recursive=True):
            try:
                rss += p.memory_info().rss
            except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                pass
        vm = self.psutil.virtual_memory()
        self.rows.append({'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                          'process_tree_rss_bytes': rss, 'system_used_bytes': vm.used,
                          'system_available_bytes': vm.available})

    def loop(self):
        while not self.stop.wait(.1):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        self.sample()
        with self.path.open('w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0]))
            w.writeheader()
            w.writerows(self.rows)


def grade(case, patient, after, job, stage, corpus, source_gold):
    checks = {}
    e = case['expected']
    for k in ['name', 'age', 'sex', 'pregnancy', 'pregnancy_weeks', 'lactation']:
        if k in e:
            checks['欄位_' + k] = patient.get(k) == e[k]
    weight = case.get('population_expected', {}).get('weight_kg')
    if weight is not None:
        checks['體重文字保留_非兒科劑量驗證'] = bool(re.search(
            re.escape(str(weight)) + r'\s*(公斤|kg)', json.dumps(patient, ensure_ascii=False), re.I))
    expected = e['drugs'][:2] if stage == 0 else e['drugs']
    actual = meds(patient)
    checks['藥名數量與順序'] = [norm(m['name']) for m in actual] == [norm(m['name']) for m in expected]
    for m in expected:
        found = [x for x in actual if norm(x['name']) == norm(m['name'])]
        checks[m['name']+'_用量'] = len(found) == 1 and dose(found[0].get('dose', '')) == dose(m['dose'])
        checks[m['name']+'_頻次'] = len(found) == 1 and freq(found[0].get('frequency', '')) == freq(m['frequency'])
    pairs = (job.get('assessment') or {}).get('pairs', [])
    expected_pairs = {tuple(sorted(norm(d['name']) for d in pair)) for pair in itertools.combinations(expected, 2)}
    checks['指定配對完整且無重複'] = (len(pairs) == len(expected_pairs) and
        {tuple(sorted(norm(n) for n in p['drugs'])) for p in pairs} == expected_pairs)
    checks['完成檢查'] = job.get('status') == 'done' and not job.get('error')
    checks['檢查沒有更動用藥'] = fingerprint(patient) == fingerprint(after)
    sources = job.get('sources', [])
    refs = {s['id']: s for s in sources}
    checks['來源識別碼不重複'] = len(refs) == len(sources)
    used = re.findall(r'\[([A-Za-z][A-Za-z0-9_-]*)\]', job.get('answer', ''))
    checks['回答引用識別碼均存在'] = all(sid in refs for sid in used)
    evidence = []
    ids = list(case.get('source_ids', []))
    if stage == 0:
        ids = [sid for sid in ids if sid != e.get('new_source_id')]
    expected_names = {norm(d['name']) for d in expected}
    ids = [sid for sid in ids if source_gold[sid]['type'] != 'medhop_pair_audit' or
           {norm(x['input']) for x in source_gold[sid].get('mapping', [])} <= expected_names]
    if stage > 0 and e.get('new_source_id'):
        ids.append(e['new_source_id'])
    for sid in dict.fromkeys(ids):
        gold = source_gold[sid]
        if gold['type'] == 'local_pdf':
            found = any(s.get('file') == gold['file'] and s.get('page') == gold['page'] for s in sources)
            checks['標準來源頁_'+sid] = found
            evidence.append({'reference': sid, 'retrieved': found, 'file': gold['file'], 'page': gold['page']})
    mappings = []
    for drug in expected:
        identity = corpus._benchmark_integration.resolve({'name': drug['name']})
        reported = [i for p in pairs for i in p.get('medhop', {}).get('identities', []) if i['input'] == drug['name']]
        checks[drug['name']+'_索引對應符合'] = bool(reported) and all(i.get('entity') == identity['entity'] for i in reported)
        mappings.append({'name': drug['name'], 'entity': identity['entity']})
    paths = job.get('paths', [])
    verified = 0
    for path in paths:
        nodes, edges = path.get('nodes', []), path.get('edges', [])
        valid = len(nodes) >= 2 and len(edges) == len(nodes)-1
        for i, edge in enumerate(edges):
            if i+1 >= len(nodes):
                valid = False
                break
            originals = [x for x in corpus.graph.adjacency.get(edge['source'], [])
                         if str(x.get('human_readable_id')) == str(edge.get('id'))
                         and x['source'] == edge['source'] and x['target'] == edge['target']]
            support = False
            for rid in edge.get('source_ids', edge.get('citations', [])):
                src = refs.get(rid, {})
                unit = corpus.unitmap.get(str(src.get('unit_id')))
                if unit and originals and src.get('text') == unit['text']:
                    support = support or (str(unit['id']) in [str(u) for u in originals[0].get('text_unit_ids', [])]
                        and all(re.search(r'(?<![A-Za-z])'+re.escape(n)+r'(?![A-Za-z])', unit['text'], re.I)
                                for n in [edge['source'], edge['target']]))
            valid = valid and {edge['source'], edge['target']} == {nodes[i], nodes[i+1]} and support
        verified += int(valid)
    checks['已顯示研究路徑均逐邊有原文支持'] = verified == len(paths)
    required_graph = any(source_gold[s].get('status') == 'supported_path' for s in ids if s in source_gold)
    if required_graph:
        entities = {corpus._benchmark_integration.resolve({'name': n})['entity'] for n in e['pair']}
        checks['指定研究配對路徑存在'] = any(len(p.get('nodes', [])) >= 2 and
            {p['nodes'][0], p['nodes'][-1]} == entities for p in paths)
    return {'checks': checks, 'label_pages': evidence, 'mappings': mappings,
            'paths_returned': len(paths), 'paths_verified': verified,
            'clinical_verdict': '待藥師覆核', 'citation_semantic_support': None,
            'citation_marker_count': len(used)}


def report(out, suite, rows, selected, metadata):
    metadata['recorded_through_utc'] = datetime.now(timezone.utc).isoformat()
    denomin = len(selected)
    completed = sum(r['completed'] for r in rows)
    passed = sum(r['automatic_pass'] for r in rows)
    times = [r['task_seconds'] for r in rows if r['completed']]
    summary = {'metadata': metadata, 'selected_cases': denomin, 'recorded_cases': len(rows),
        'completed_cases': completed, 'automatic_contract_passed_cases': passed,
        'operational_completion_rate': completed/denomin, 'automatic_contract_pass_rate': passed/denomin,
        'case_latency_seconds_completed_only': stats(times),
        'clinical_accuracy': None, 'citation_semantic_accuracy': None, 'power_w': None,
        'load_time_seconds': None, 'model_ttft_seconds': None, 'generation_tokens_per_second': None,
        'hardware_execution_verified': False,
        'limits': ['首字延遲與模型速度另用模型測試，不能以工作流程第一個進度訊息代替。',
                   '功耗需實測；本工具不估算。模型已在其他服務載入，未為測試卸載共享模型。',
                   '記憶體涵蓋測試程式與其子程序，不含獨立模型服務；系統RAM另有背景負載。',
                   '30題是30個不同案例，各跑一次；不是同一情境重複30次。接續題共45階段。',
                   '自動通過率為欄位／流程／來源結構契約，不等於臨床任務成功率。',
                   '失敗與未完成保留在選定題目分母；耗時分位數僅對已完成案例計算。'],
        'results': rows}
    save(out/'summary.json', summary)
    with (out/'results.csv').open('w', newline='', encoding='utf-8-sig') as f:
        fields = ['id','completed','automatic_pass','task_seconds','peak_process_tree_rss_bytes','error']
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    review = out/'pharmacist_review.csv'
    if not review.exists():
        with review.open('w', newline='', encoding='utf-8-sig') as f:
            fields = ['case_id','clinical_correct','citation_support_correct','intervention_appropriate','reviewer','notes']
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
            w.writerows({'case_id': c['id']} for c in selected)
    text = ['# 30題自動驗收與KPI紀錄', '', f'本次選定 {denomin} 題；已記錄 {len(rows)} 題。',
        f'流程完成 {completed}/{denomin}；全部自動條件通過 {passed}/{denomin}。',
        f'已完成案例耗時 P50：{stats(times)["p50"]} 秒；P95：{stats(times)["p95"]} 秒。',
        '臨床正確率、引用語意正確性與處置適切性：待藥師填 pharmacist_review.csv。', '',
        '|題號|完成|自動條件|總秒數|記憶體峰值 MiB|', '|---|---|---|---:|---:|']
    text += [f'|{r["id"]}|{r["completed"]}|{r["automatic_pass"]}|{r["task_seconds"]:.3f}|{r["peak_process_tree_rss_bytes"]/1048576:.1f}|' for r in rows]
    text += ['', '## 量測限制', ''] + ['- '+x for x in summary['limits']]
    (out/'summary.md').write_text('\n'.join(text)+'\n', encoding='utf-8')


def run(args):
    root = args.project.resolve()
    if not (root/'asgi.py').is_file():
        raise ValueError('請在含 asgi.py 的專案中執行，或用 --project 指定路徑。')
    suite = json.loads(args.cases.read_text(encoding='utf-8-sig'))
    if suite.get('schema_version') != 'mixed30-1':
        raise ValueError('必須使用本包的 acceptance_cases30.json。')
    wanted = set(args.only.split(',')) if args.only else {c['id'] for c in suite['cases']}
    if wanted - {c['id'] for c in suite['cases']}:
        raise ValueError('有不存在的題號。')
    selected = [c for c in suite['cases'] if c['id'] in wanted]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    out = root/'runtime'/'benchmark30'/stamp
    out.mkdir(parents=True, exist_ok=False)
    os.environ['MEDLINK_RUNTIME'] = str(out/'isolated-runtime')
    if not args.use_model:
        os.environ['MEDHOP_PYTHON'] = ''
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from fastapi.testclient import TestClient
    from asgi import app
    import server as core
    from pair_integration import PairIntegration
    metadata = {'version': suite['audit']['version'], 'label': args.label,
        'selected_ids': [c['id'] for c in selected],
        'started_utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
        'python': sys.version, 'model_requested': args.use_model, 'warmup_cases': args.warmup,
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'code_sha256': {n: hashlib.sha256((root/n).read_bytes()).hexdigest()
                        for n in ['asgi.py','server.py','workflow.py','governance.py',
                                  'clinical_advice.py', 'data/clinical_advice_sources.json'] if (root/n).is_file()},
        'timing_scope': '每階段 workflow POST 到 review 結束，排除啟動、評分、引用下載和去重重送；各階段相加為案例耗時。非瀏覽器端延遲。'}
    rows = []
    print('隔離測試輸出：'+str(out), flush=True)
    with Sampler(out/'resources.csv') as sampler, TestClient(app, base_url='http://127.0.0.1') as client:
        core.CORPUS._benchmark_integration = PairIntegration(core.CORPUS)
        metadata['model'] = core.MODEL
        metadata['model_url'] = core.MODEL_URL
        def call(method, route, payload=None):
            res = client.request(method, route, json=payload) if payload is not None else client.request(method, route)
            res.raise_for_status()
            return res.json()
        def stage(text):
            start = time.perf_counter()
            intake = call('POST', '/api/workflow', {'message': text})
            if not intake.get('patient') or intake.get('action') in {'ambiguous','identity_conflict','need_identity'}:
                raise ValueError('病人身分未完成：'+json.dumps(intake, ensure_ascii=False))
            jid = call('POST','/api/review', {'use_model': args.use_model})['id']
            while True:
                job = call('GET', '/api/jobs/'+jid)
                if job['status'] != 'running':
                    break
                if time.perf_counter()-start > args.timeout:
                    call('POST','/api/cancel', {'id': jid})
                    raise TimeoutError('只取消本題測試job '+jid)
                time.sleep(.05)
            return intake, job, time.perf_counter()-start
        # Warmups use a fresh TestClient cookie/session and explicit unique fictional name.
        for i in range(args.warmup):
            client.cookies.clear()
            c = selected[0]
            text = c['steps'][0].replace(c['expected']['name'], '暖機王'+chr(0x4e00+i))
            print('暖機 '+str(i+1), flush=True)
            stage(text)
        for c in selected:
            folder = out/c['id']; folder.mkdir()
            client.cookies.clear()
            r = {'id': c['id'], 'completed': False, 'automatic_pass': False, 'task_seconds': 0.0,
                 'stages': [], 'checks': {}, 'clinical_verdict': '待藥師覆核', 'error': ''}
            events = []; n0 = len(sampler.rows)
            case_started = time.perf_counter()
            try:
                before = None
                for i, message in enumerate(c['steps']):
                    print(c['id']+' 階段 '+str(i+1), flush=True)
                    intake, job, seconds = stage(message)
                    r['task_seconds'] += seconds
                    events.append({'stage': i+1, 'intake': intake, 'job': job})
                    patient = intake['patient']
                    after = call('GET', '/api/patients').get('patient')
                    result = grade(c, patient, after, job, i, core.CORPUS, suite['sources'])
                    result['seconds'] = seconds
                    result['workflow_progress_messages'] = intake.get('steps')
                    result['review_progress_messages'] = job.get('steps')
                    result['model_execution_observation'] = {
                        'requested': args.use_model,
                        'pharmacy_rag': (job.get('assessment') or {}).get('pharmacy_rag'),
                        'agent': (job.get('assessment') or {}).get('agent'),
                    }
                    if before:
                        result['checks']['接續同一病歷'] = before['id'] == patient['id']
                        result['checks']['接續保留原兩藥'] = fingerprint(before) == fingerprint({'medications': patient['medications'][:2]})
                        result['checks']['新增用藥類型'] = any(norm(m['name']) == norm(c['expected']['new_drug'])
                            and m['kind'] == c['expected']['new_kind'] for m in meds(patient))
                    availability = {}
                    for s in job.get('sources', []):
                        name = s.get('file')
                        if name and name.lower().endswith('.pdf') and name not in availability:
                            response = client.get('/documents/'+name)
                            availability[name] = response.status_code == 200 and response.content.startswith(b'%PDF-')
                    result['pdf_available'] = availability
                    if availability:
                        result['checks']['引用PDF可開啟'] = all(availability.values())
                    r['stages'].append(result)
                    before = patient
                r['completed'] = all(s['checks']['完成檢查'] for s in r['stages'])
                if len(c['steps']) == 2:
                    repeat = call('POST','/api/workflow', {'message': c['steps'][1]})
                    events.append({'dedup_repeat': repeat})
                    r['checks']['重送不重複新增'] = fingerprint(before) == fingerprint(repeat.get('patient'))
                r['automatic_pass'] = r['completed'] and all(r['checks'].values()) and all(
                    all(s['checks'].values()) for s in r['stages'])
            except KeyboardInterrupt:
                r['error'] = '使用者中止；此題未完成'
                raise
            except Exception as exc:
                r['error'] = type(exc).__name__+': '+str(exc)
            finally:
                sampler.sample()
                r['wall_seconds_including_grading'] = time.perf_counter()-case_started
                samples = sampler.rows[n0:]
                r['peak_process_tree_rss_bytes'] = max((s['process_tree_rss_bytes'] for s in samples), default=0)
                save(folder/'events.json', events); save(folder/'result.json', r)
                (folder/'核對答案.md').write_text('\n'.join(['# '+c['id'], '', *c['checks'], '',
                    json.dumps(c['expected'], ensure_ascii=False, indent=2)]), encoding='utf-8')
                rows.append(r); report(out, suite, rows, selected, metadata)
                print('  完成='+str(r['completed'])+' 自動條件='+str(r['automatic_pass'])+' '+r['error'], flush=True)
    print('結果：'+str(out/'summary.md'), flush=True)
    from summarize_kpis import main as summarize
    summarize(['--run', str(out)])
    return int(any(not r['automatic_pass'] for r in rows))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path.cwd())
    p.add_argument('--cases', type=Path, default=Path(__file__).with_name('acceptance_cases30.json'))
    p.add_argument('--only', default='')
    p.add_argument('--warmup', type=int, default=2)
    p.add_argument('--use-model', action='store_true')
    p.add_argument('--timeout', type=float, default=300)
    p.add_argument('--label', default='lab-shared-mixed30')
    args = p.parse_args()
    if args.warmup < 0 or args.timeout <= 0:
        p.error('warmup 不可負數，timeout 必須大於0。')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
