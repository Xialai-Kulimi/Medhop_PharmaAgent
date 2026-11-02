"""Back up and surgically update workflow/server in the current project."""
import ast
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil


METHOD='''    def classify_pairs(self):
        self.step('逐對分批核對仿單原文','本機模型','running')
        while not MODEL_LOCK.acquire(timeout=.25):
            if self.stop.is_set():return
        try:
            if self.stop.is_set():return
            from model_pair_check import classify
            classify(self.assessment,MODEL_URL,MODEL,self.stop)
            if self.stop.is_set():return
            rag=self.assessment.setdefault('pharmacy_rag',{})
            rag['model_status']='completed_with_source_gaps_manual_review' if rag.get('source_budget_omissions') else 'completed_quote_validated'
            self.steps[-1].update(state='done',result='已分批核對並保留逐字來源驗證；缺口仍需人工覆核')
        finally:
            self.response=None
            MODEL_LOCK.release()
'''


def transform(workflow,server):
    if '# parser-frequency-fix-20261102' not in workflow:
        old="FREQ=re.compile(r'"
        pos=workflow.find(old)
        if pos<0:raise ValueError('無法辨識workflow.py的FREQ版本，未修改。')
        start=pos+len(old)
        workflow=workflow[:start]+r'需要時(?:服用)?|必要時(?:服用)?|持續貼用|貼片每\d+小時更換一次|'+workflow[start:]
        units=r'(?:mcg|mg|mL|毫克|微克|公克|g\b|錠|顆)'
        if workflow.count(units)<2:raise ValueError('劑量格式與預期不同，未修改。')
        workflow=workflow.replace(units,units+r'(?:\s*/\s*(?:hour|hr|h|小時))?')
        anchor="if not previous[field]:previous[field]=value;previous['text']+='，'+piece.strip()"
        if workflow.count(anchor)!=1:raise ValueError('頻次欄位版本不同，未修改。')
        workflow=workflow.replace(anchor,anchor+"\n                    elif field=='frequency' and previous[field].startswith('持續貼用') and value.startswith('貼片每') and value not in previous[field]:\n                        previous[field]+='，'+value;previous['text']+='，'+piece.strip()")
        workflow+='\n# parser-frequency-fix-20261102\n'
    tree=ast.parse(server)
    cls=next((n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ReviewJob'),None)
    method=next((n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='classify_pairs'),None) if cls else None
    if not method:raise ValueError('找不到ReviewJob.classify_pairs，未修改。')
    lines=server.splitlines(keepends=True)
    server=''.join(lines[:method.lineno-1])+METHOD+''.join(lines[method.end_lineno:])
    compile(workflow,'workflow.py','exec');compile(server,'server.py','exec')
    return workflow,server


def main():
    root=Path.cwd()
    for name in ['workflow.py','server.py','pharmacy_rag_updated.py','model_pair_check.py','acceptance_cases30_updated.json']:
        if not (root/name).is_file():raise SystemExit('缺少 '+name+'；請在專案根目錄放好本包檔案再執行。')
    before={name:(root/name).read_bytes() for name in ['workflow.py','server.py','pharmacy_rag.py','acceptance_cases30.json'] if (root/name).is_file()}
    workflow,server=transform(before['workflow.py'].decode('utf-8-sig'),before['server.py'].decode('utf-8-sig'))
    rag=(root/'pharmacy_rag_updated.py').read_text(encoding='utf-8-sig')
    compile(rag,'pharmacy_rag.py','exec')
    cases=json.loads((root/'acceptance_cases30_updated.json').read_text(encoding='utf-8-sig'))
    if cases.get('schema_version')!='mixed30-1' or len(cases['cases'])!=30:raise ValueError('更新題庫格式錯誤，未修改。')
    backup=root/'runtime'/'code-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True,exist_ok=False)
    for name,data in before.items():(backup/name).write_bytes(data)
    # Backup occurs before either mutation; restore both automatically if a write fails.
    try:
        (root/'workflow.py').write_text(workflow,encoding='utf-8')
        (root/'server.py').write_text(server,encoding='utf-8')
        (root/'pharmacy_rag.py').write_text(rag,encoding='utf-8')
        (root/'acceptance_cases30.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        for name,data in before.items():(root/name).write_bytes(data)
        raise
    print('已修正workflow.py、server.py、pharmacy_rag.py及一題頻次核對值，備份：'+str(backup))
    print('沒有修改病歷、模型設定或任何系統服務。')


if __name__=='__main__':main()
