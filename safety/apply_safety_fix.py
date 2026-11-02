"""Run with the project venv. Patch only this project; no service/DB/model actions."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import json
import shutil
import sys

MARK = '# safety-alerts-backend-20261102'


def transform_workflow(text):
    if '# safety-followup-identity-20261102' in text:
        return text
    anchor = "        if value and value not in {'資料','基本資料','姓名','狀況','資訊','服藥','用藥','原文'}:"
    if text.count(anchor) != 1:
        raise ValueError('workflow.py姓名辨識版本不同，未修改。')
    replacement = "        if value and not re.match(r'^(?:的|請|需|仍|已|尚未|這位|那位|同一|本人|狀態)',value) and value not in {'資料','基本資料','姓名','狀況','資訊','服藥','用藥','原文'}:"
    result=text.replace(anchor,replacement)+'\n# safety-followup-identity-20261102\n'
    compile(result,'workflow.py','exec')
    return result


def transform(text):
    if MARK in text:
        return text
    anchor = "        self.assessment['medication_checks']=medication_checks(self.patient,app.state.catalog)"
    if text.count(anchor) != 1:
        raise ValueError('asgi.py與已知版本不同，找不到唯一的medication_checks接點；未修改。')
    text = text.replace(anchor, "        from safety_alerts import apply_safety\n        apply_safety(core.ROOT,core.CORPUS.pages,self.patient,self.assessment)\n"+anchor)
    tree = ast.parse(text)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'with_population'), None)
    if fn is None or not isinstance(fn.body[-1], ast.Return):
        raise ValueError('找不到with_population回傳接點；未修改。')
    lines = text.splitlines(keepends=True)
    insert = """    from safety_alerts import apply_safety
    safety={'population_checks':population['checks'],'interaction_alerts':indexed['alerts'],
            'sources':list(population['sources'])+list(indexed['sources'])}
    apply_safety(core.ROOT,core.CORPUS.pages,patient,safety)
    result['safety_alerts']=safety['safety_alerts']
    result['safety_alert_sources']=safety['sources']
"""
    lines.insert(fn.body[-1].lineno-1, insert)
    result = ''.join(lines)+'\n'+MARK+'\n'
    compile(result, 'asgi.py', 'exec')
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    parser.add_argument('--check', action='store_true', help='Only verify supported code and source files; do not modify.')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    bundle = Path(__file__).resolve().parent
    required = ['asgi.py', 'workflow.py', 'static/workspace.js', 'static/workspace.css', 'data/pharma_pages.json']
    for name in required:
        if not (root/name).is_file():
            raise SystemExit('不是完整專案目錄，缺少：'+name)
    text = (root/'asgi.py').read_text(encoding='utf-8-sig')
    changed = transform(text)
    js = (root/'static/workspace.js').read_text(encoding='utf-8-sig')
    css = (root/'static/workspace.css').read_text(encoding='utf-8-sig')
    if 'renderAssessment' not in js or 'renderWorkflowResponse' not in js:
        raise SystemExit('workspace.js版本不同，未修改。')
    payload = {'asgi.py': changed, 'safety_alerts.py': (bundle/'safety_alerts.py').read_text(encoding='utf-8'),
               'data/safety_warning_sources.json': (bundle/'safety_warning_sources.json').read_text(encoding='utf-8')}
    payload['workflow.py']=transform_workflow((root/'workflow.py').read_text(encoding='utf-8-sig'))
    payload['static/workspace.js'] = js if '// safety-alerts-20261102:' in js else js+'\n'+(bundle/'safety_ui.js').read_text(encoding='utf-8')
    payload['static/workspace.css'] = css if '/* safety-alerts-20261102 */' in css else css+'\n'+(bundle/'safety_ui.css').read_text(encoding='utf-8')
    # Preflight the actual PDF hash, quoted page and a real under-six evaluation.
    namespace = {}
    exec(compile(payload['safety_alerts.py'], 'safety_alerts.py', 'exec'), namespace)
    import hashlib
    rules = json.loads(payload['data/safety_warning_sources.json'])
    pages = json.loads((root/'data/pharma_pages.json').read_text(encoding='utf-8'))
    for r in rules['age_rules']:
        page = next(p for p in pages if p['file'] == r['file'] and p['page'] == r['page'])
        if r['quote'] not in page['text'] or hashlib.sha256((root/'data/drug_info'/r['file']).read_bytes()).hexdigest() != r['pdf_sha256']:
            raise SystemExit('原PDF或文字版本不同；未修改。請核對來源。')
    if args.check:
        print('檢查通過；此步驟未寫入檔案。')
        return
    backup = root/'runtime/code-backups'/('safety-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    for name in payload:
        old = root/name
        if old.exists():
            dst = backup/name; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(old, dst)
    for name, content in payload.items():
        dst = root/name; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_text(content, encoding='utf-8')
    print('已更新本專案的6個檔案；未動病歷、金鑰、模型或服務。')
    print('原程式備份：'+str(backup))
    print('請只重啟你自己的8502服務，再於瀏覽器Ctrl+Shift+R。舊檢查結果須重新檢查，才會產生新版警示。')


if __name__ == '__main__':
    main()
