"""Incremental fix for installed common safety alerts; no DB or service actions."""
from datetime import datetime, timezone
from pathlib import Path
import argparse, hashlib, json, shutil


def patch_workflow(text):
    if '# symptom-prn-20261102' in text:
        return text
    anchor="FREQ=re.compile(r'"
    if text.count(anchor)!=1:
        raise ValueError('找不到唯一的FREQ頻次辨識設定，未修改。')
    pattern=r'(?:偏頭痛|頭痛|疼痛|症狀|痛|發作)(?:發作|出現|加劇)?(?:的時候|時)(?:才|再)?(?:有需要|需要|必要時|需要時)?(?:服用|使用|吃)|(?:有需要|需要|必要)才(?:服用|使用|吃)|按需(?:服用|使用|吃)?|'
    result=text.replace(anchor,anchor+pattern)+'\n# symptom-prn-20261102\n'
    compile(result,'workflow.py','exec')
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='.')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();root=Path(args.root).resolve();bundle=Path(__file__).resolve().parent
    required=['workflow.py','asgi.py','safety_alerts.py','data/safety_warning_sources.json']
    if any(not(root/n).is_file() for n in required):
        raise SystemExit('請在已安裝共用紅色警示的專案根目錄執行；未修改檔案。')
    if 'apply_safety(' not in (root/'asgi.py').read_text(encoding='utf-8-sig'):
        raise SystemExit('尚未接入共用警示流程，請先安裝前一版修正。')
    row=json.loads((bundle/'sumatriptan_warning.json').read_text(encoding='utf-8-sig'))
    if row['id']!='SAFE-SUMATRIPTAN-FLUOXETINE' or hashlib.sha256(row['quote'].encode()).hexdigest()!=row['excerpt_sha256']:
        raise SystemExit('來源摘錄不符，請重新取得更新包。')
    registry=json.loads((root/'data/safety_warning_sources.json').read_text(encoding='utf-8-sig'))
    registry['pair_rules']=[r for r in registry['pair_rules'] if r['id']!=row['id']]+[row]
    payload={'workflow.py':patch_workflow((root/'workflow.py').read_text(encoding='utf-8-sig')),
             'data/safety_warning_sources.json':json.dumps(registry,ensure_ascii=False,indent=2)}
    if args.check:
        print('檢查通過；未修改檔案。');return
    backup=root/'runtime/code-backups'/('prn-serotonin-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    for name,text in payload.items():
        target=root/name
        if target.read_text(encoding='utf-8-sig')==text:continue
        dst=backup/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,dst)
        target.write_text(text,encoding='utf-8')
    print('已更新頻次辨識與Sumatriptan／Fluoxetine仿單警示；未更改病歷、金鑰、模型或服務。')
    print('備份（有變更才建立）：'+str(backup))
    print('請重啟你自己的網頁服務，Ctrl+Shift+R後重新輸入測試；舊結果不會被自動改寫。')


if __name__=='__main__':main()
