"""Read-only diagnostics. No patient DB access, HTTP requests or service changes."""
from pathlib import Path
import importlib.util,json,sys

root=Path.cwd().resolve();sys.dont_write_bytecode=True;sys.path.insert(0,str(root))
print('專案位置：',root)
checks=[]
try:
    import workflow
    print('實際載入workflow：',workflow.__file__)
    phrase='偏頭痛發作時需要服用'
    rows=workflow.medications('Sumatriptan 50mg，'+phrase)
    actual=rows[0].get('frequency','') if len(rows)==1 else None
    ok=actual==phrase;checks.append(ok)
    print('① 頻次辨識：', '通過' if ok else '未通過',repr(actual))
except Exception as e:
    checks.append(False);print('① 頻次辨識：失敗',type(e).__name__,str(e))
try:
    rules=json.loads((root/'data/safety_warning_sources.json').read_text(encoding='utf-8-sig'))
    ok=any(r['id']=='SAFE-SUMATRIPTAN-FLUOXETINE' for r in rules['pair_rules']);checks.append(ok)
    print('② 仿單警示來源：','已安裝' if ok else '尚未安裝')
except Exception as e:
    checks.append(False);print('② 仿單警示來源：失敗',type(e).__name__,str(e))
try:
    from safety_alerts import apply_safety
    patient={'id':'diagnostic-only','version':1,'age':45,'sex':'男','medications':[
        {'id':1,'name':'Fluoxetine','status':'active','dose':'20mg','frequency':'一天一次'},
        {'id':2,'name':'Sumatriptan','status':'active','dose':'50mg','frequency':'偏頭痛發作時需要服用'}]}
    result={'pairs':[],'sources':[]};apply_safety(root,[],patient,result)
    ok=any(r['id']=='SAFE-SUMATRIPTAN-FLUOXETINE' for r in result['safety_alerts']);checks.append(ok)
    print('③ 警示引擎：','可產生血清素症候群提醒' if ok else '未產生提醒')
except Exception as e:
    checks.append(False);print('③ 警示引擎：失敗',type(e).__name__,str(e))
try:
    ok='apply_safety(' in (root/'asgi.py').read_text(encoding='utf-8-sig');checks.append(ok)
    print('④ 網頁後端接點：','已接入' if ok else '尚未接入')
except Exception as e:
    checks.append(False);print('④ 網頁後端接點：失敗',type(e).__name__,str(e))
print('結論：', '磁碟程式檢查全部通過；仍須重啟自己的服務並產生新檢查，舊快照不會改寫。' if all(checks) else '磁碟程式尚未全部更新；請回傳以上輸出確認缺少哪一步。')
print('本指令未讀取病歷、未更改檔案、未連線模型、未停止服務。')
