"""Source-grounded warnings and explicit evidence gaps, independent of case IDs/LLM."""
from pathlib import Path
import hashlib
import json
import re
import unicodedata


def norm(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value))).strip().casefold()


def apply_safety(root, pages, patient, assessment):
    """Idempotently enrich assessment; never change a medication or invent a risk."""
    root = Path(root)
    manifest = json.loads((root/'data/safety_warning_sources.json').read_text(encoding='utf-8'))
    meds = [m for m in patient.get('medications', []) if m.get('status') == 'active']
    assessment.setdefault('medications', meds)
    sources = assessment.setdefault('sources', [])
    source_map = {s['id']: s for s in sources}
    warnings = []
    seen = set()

    def add(item):
        signature = (item.get('rule_id') or item.get('id'), tuple(sorted(norm(n) for n in item.get('drugs', []))), item.get('message'))
        if signature not in seen:
            seen.add(signature)
            warnings.append(item)

    def source(row):
        sid = row['id']
        if sid not in source_map:
            if row.get('file'):
                page = next((p for p in pages if p['file'] == row['file'] and p['page'] == row['page']), None)
                if not page or row['quote'] not in page['text']:
                    return None
                pdf = root/'data/drug_info'/row['file']
                if not pdf.is_file() or hashlib.sha256(pdf.read_bytes()).hexdigest() != row['pdf_sha256']:
                    return None
                item = {'id': sid, 'title': f"{row['file']} · 第 {row['page']} 頁", 'file': row['file'], 'page': row['page'],
                        'kind': '原始藥事 Agent 仿單 · 新增條件警示', 'text': row['quote'], 'full_text': page['text'],
                        'origin': 'moda-agent/data/drug_info/'+row['file'], 'url': f"/documents/{row['file']}#page={row['page']}", 'external': False}
            else:
                if hashlib.sha256(row['quote'].encode()).hexdigest() != row['excerpt_sha256']:
                    return None
                item = {'id': sid, 'title': row['title'], 'kind': '新增官方來源', 'text': row['quote'], 'full_text': row['quote'],
                        'url': row['url'], 'origin': row['url'], 'external': True, 'verified_on': row['verified_on']}
            sources.append(item)
            source_map[sid] = item
        return sid

    def find(names):
        anchors = {norm(n) for n in names}
        return [m for m in meds if norm(m['name']) in anchors or any(norm(c) in anchors for c in m.get('components', []))]

    for item in assessment.get('population_checks', []):
        if item.get('status') not in {'not_applicable', 'checked', 'safe'}:
            add({**item, 'category': 'risk' if item.get('status') == 'contraindicated' else 'review', 'title': '病人條件用藥提醒'})
    for item in assessment.get('interaction_alerts', []):
        add({**item, 'category': 'risk', 'title': '來源記載的併用風險'})

    age = patient.get('age')
    age_covered = set()
    for row in manifest['age_rules']:
        for med in find(row['names']):
            if isinstance(age, (int, float)) and age >= row['under_years']:
                age_covered.add(med['id'])
                continue
            sid = source(row)
            if not sid:
                add({'id': 'age-source-unavailable-'+str(med['id']), 'category': 'gap', 'title': '年齡警語來源待核對', 'drug': med['name'],
                     'message': med['name']+'：已設定的年齡來源缺失或內容變動，不能完成原文核對；請藥師查閱實際產品仿單。', 'source_ids': []})
                continue
            age_covered.add(med['id'])
            message = (f"{med['name']}：病人 {age:g} 歲，這份原仿單記載「{row['quote']}」請在調劑前聯繫開方醫師，核對適應症、實際產品及兒童用法；不要自行突然停藥。"
                       if isinstance(age, (int, float)) else med['name']+'：原仿單有兒童年齡限制，請先補充年齡再核對是否適用。')
            add({'id': 'age-'+str(med['id']), 'rule_id': row['id'], 'category': 'risk' if age is not None else 'review',
                 'title': '兒童年齡適用性提醒', 'drug': med['name'], 'status': 'age_restriction', 'quote': row['quote'], 'message': message,
                 'source_ids': [sid], 'scope': '此為指定產品仿單的「不宜使用」記載，不等於所有兒童或全部Diazepam製劑一律禁用；仍須核對實際產品。'})

    for row in manifest['pair_rules']:
        left, right = find(row['left_names']), find(row['right_names'])
        for a, b in ((a, b) for a in left for b in right if a['id'] != b['id']):
            sid = source(row)
            if not sid:
                continue
            drugs = [a['name'], b['name']]
            add({'id': row['id'], 'rule_id': row['id'], 'category': 'risk', 'title': '來源記載的併用風險', 'status': 'interaction',
                 'drugs': drugs, 'message': row['message'], 'quote': row['quote'], 'source_ids': [sid], 'scope': row['scope']})

    covered = {tuple(sorted(norm(n) for n in w.get('drugs', []))) for w in warnings if w['category'] == 'risk' and w.get('drugs')}
    pairs = assessment.get('pairs', [])
    for pair in pairs:
        names = pair.get('drugs', [])
        signature = tuple(sorted(norm(n) for n in names))
        if signature in covered:
            continue
        status = pair.get('status')
        if status in {'avoid', 'interaction'}:
            add({**pair, 'category': 'risk', 'title': '來源記載的併用風險',
                 'message': pair.get('reason') or (' × '.join(names)+'：'+(pair.get('quote') or '來源記載併用影響')+'；調劑前請核對適用條件及聯繫開方醫師。')})
        elif status != 'no_effect_reported':
            add({'id': 'gap-'+pair.get('id', '|'.join(names)), 'category': 'gap', 'title': '配對來源待核對', 'drugs': names,
                 'message': ' × '.join(names)+'：目前未取得足以完成用藥判定的來源或核對結果。這不是已證實的交互作用，也不能視為沒有風險；請核對實際產品仿單或補充來源。',
                 'source_ids': pair.get('source_ids', []), 'scope': 'MedHop共同靶點／研究路徑不直接當作臨床交互作用。'})
    # Immediate intake has not run pair retrieval yet. Keep that state explicit.
    if 'pairs' not in assessment and len(meds) > 1:
        add({'id': 'pair-check-pending', 'category': 'review', 'title': '併用檢查尚待完成',
             'message': f'目前有 {len(meds)} 項用藥，須檢查 {len(meds)*(len(meds)-1)//2} 組配對；尚未完成來源檢索，請勿把未出現特定警語視為可以併用。', 'source_ids': []})
    if isinstance(age, (int, float)) and age < 18:
        names = [m['name'] for m in meds if m['id'] not in age_covered]
        if names:
            add({'id': 'pediatric-coverage', 'category': 'review', 'title': '兒童／青少年用法待核對',
                 'message': f"病人 {age:g} 歲："+'、'.join(names)+' 尚未完成對應產品的兒科年齡、體重與用量核對；不可直接套用成人劑量。此為核對提醒，不是禁用結論。', 'source_ids': []})
    for field, value, label in [('pregnancy', 'pregnant', '懷孕'), ('lactation', 'breastfeeding', '哺乳')]:
        if patient.get(field) != value:
            continue
        covered_ids = {mid for c in assessment.get('population_checks', []) if c.get('condition') == field
                       and c.get('status') == 'contraindicated' for mid in c.get('med_ids', [c.get('med_id')])}
        names = [m['name'] for m in meds if m['id'] not in covered_ids]
        if names:
            add({'id': field+'-coverage', 'category': 'review', 'title': label+'用藥條件待核對',
                 'message': '病人目前'+label+'：'+'、'.join(names)+' 尚未完成每項實際產品的'+label+'適用性與風險效益核對；沒有命中禁忌規則不代表適合使用，請聯繫開方醫師確認。', 'source_ids': []})
    if age is None and meds:
        add({'id': 'age-missing', 'category': 'review', 'title': '病人資料待補充', 'message': '尚未提供年齡，無法完成年齡適用性核對。', 'source_ids': []})
    missing = [m['name'] for m in meds if not m.get('dose') or not m.get('frequency')]
    if missing:
        add({'id': 'dose-frequency-missing', 'category': 'review', 'title': '藥單資訊待補充',
             'message': '、'.join(missing)+'：每次用量或頻次尚未完整，請補充藥袋資訊後核對；不自行補上處方。', 'source_ids': []})
    if assessment.get('pharmacy_rag', {}).get('model_status') in {'failed_manual_review', 'not_run'}:
        add({'id': 'model-incomplete', 'category': 'gap', 'title': '模型核對未完成', 'message': '部分配對尚未完成模型核對；已保留可用原文，請由藥師人工核對。', 'source_ids': []})
    if meds and not warnings:
        add({'id': 'review-boundary', 'category': 'review', 'title': '交付前核對提醒',
             'message': '本次已核對範圍內沒有新增明確風險警語；不代表整張藥單已確認安全。仍請核對實際產品、病人條件與用法後再交付。', 'source_ids': []})
    if not meds:
        add({'id': 'medication-missing', 'category': 'review', 'title': '尚無可核對用藥', 'message': '請先輸入目前服用、近期新開或自行購買的藥物及用法，才能進行用藥核對。', 'source_ids': []})
    assessment['safety_alerts'] = warnings
    return assessment
