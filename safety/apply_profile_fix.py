"""Add optional anthropometrics and remove routine lactation UI. No live DB access."""
from datetime import datetime, timezone
from pathlib import Path
import argparse, ast, shutil

HELPERS='''
# anthropometrics-20261103
def anthropometrics(text):
    text=canonical(text)
    result={}
    patterns=[('height_cm',r'身高\\s*[：:]?\\s*(\\d+(?:\\.\\d+)?)\\s*(公分|厘米|cm|公尺|m)(?![A-Za-z])'),
              ('weight_kg',r'(?:體重|体重)\\s*[：:]?\\s*(\\d+(?:\\.\\d+)?)\\s*(公斤|千克|kg)(?![A-Za-z])')]
    for field,pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match:
            value=float(match[1])
            if field=='height_cm' and match[2].lower() in {'公尺','m'}:value*=100
            result[field]=round(value,6)
    validate_anthropometrics(result)
    return result

def validate_anthropometrics(values):
    import math
    for field,maximum,label in [('height_cm',300,'身高（公分）'),('weight_kg',600,'體重（公斤）')]:
        value=values.get(field)
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<=maximum):
            raise ValueError(label+'數值無效，請核對單位與輸入；未自動推算。')

'''

UI='''
// anthropometrics-ui-20261103
const anthropometricsPreviousRecord=renderPatientRecord;
renderPatientRecord=function(){
  anthropometricsPreviousRecord();
  if(!currentPatient)return;
  const box=$('#recScroll'),fields=box.querySelector('.rec-fields');
  if(!fields)return;
  for(const [key,label,unit] of [['height_cm','身高','公分'],['weight_kg','體重','公斤']]){
    const field=el('div','anthropometrics-field');
    field.append(el('dt','',label),el('dd','',currentPatient[key]!=null?currentPatient[key]+' '+unit:'未提供'));
    fields.append(field);
  }
};
'''


def replace_once(text,old,new,label):
    if text.count(old)!=1:raise ValueError(label+'版本不同，未修改。')
    return text.replace(old,new)


def workflow_patch(text):
    if '# anthropometrics-20261103' in text:return text
    text=replace_once(text,'def identity(text):',HELPERS+'def identity(text):','workflow identity')
    tree=ast.parse(text);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='identity')
    if not isinstance(fn.body[-1],ast.Return):raise ValueError('identity結構不同')
    lines=text.splitlines(keepends=True);lines.insert(fn.body[-1].lineno-1,'    result.update(anthropometrics(text))\n');text=''.join(lines)
    old="[('pregnancy',\"TEXT NOT NULL DEFAULT 'unknown'\"),('pregnancy_weeks','INTEGER'),('lactation',\"TEXT NOT NULL DEFAULT 'unknown'\")]"
    new=old[:-1]+",('height_cm','REAL'),('weight_kg','REAL')]"
    text=replace_once(text,old,new,'資料庫新增欄位')
    old="{'age','sex','pregnancy','pregnancy_weeks','lactation'}"
    if text.count(old)!=2:raise ValueError('基本資料儲存欄位版本不同')
    text=text.replace(old,"{'age','sex','pregnancy','pregnancy_weeks','lactation','height_cm','weight_kg'}")
    text=replace_once(text,"        values={k:v for k,v in values.items() if k in allowed}","        values={k:v for k,v in values.items() if k in allowed}\n        validate_anthropometrics(values)",'基本資料驗證')
    text=replace_once(text,'|是否哺乳|診斷|病史|過敏|實驗室|缺貨|替代|基本)', '|是否哺乳|身高|體重|体重|診斷|病史|過敏|實驗室|缺貨|替代|基本)','藥單中排除身高體重')
    return text


def asgi_patch(text):
    if '# anthropometrics-api-20261103' in text:return text
    anchor="    lactation:Literal['unknown','breastfeeding','not_breastfeeding','not_applicable']='unknown'"
    text=replace_once(text,anchor,anchor+"\n    height_cm:float|None=Field(default=None,gt=0,le=300)\n    weight_kg:float|None=Field(default=None,gt=0,le=600)",'PatientProfile')
    anchor="    values=body.model_dump(exclude={'name','history','allergies'})"
    text=replace_once(text,anchor,anchor+"\n    values={k:v for k,v in values.items() if k not in {'height_cm','weight_kg'} or k in body.model_fields_set}",'profile更新')
    return text+'\n# anthropometrics-api-20261103\n'


def remove_lactation_row(text):
    for var in ['patient','p']:
        text=text.replace(",el('p','',`哺乳狀態：${LACTATION_LABELS["+var+".lactation]||'未確認'}`)",'')
    return text


def governance_patch(text):
    if '# anthropometrics-summary-20261103' in text:return text
    # It is no longer a routine missing-information requirement. Explicitly
    # reported breastfeeding and its existing safety evaluation remain intact.
    line="    if patient.get('sex')!='男' and patient.get('lactation','unknown')=='unknown':missing.append('哺乳狀態')\n"
    text=replace_once(text,line,'','哺乳例行待填提示')
    tail="+'；哺乳狀態：'+{'unknown':'未確認','breastfeeding':'哺乳中','not_breastfeeding':'未哺乳','not_applicable':'不適用'}.get(patient.get('lactation'),'未確認')"
    text=replace_once(text,tail,'','摘要哺乳狀態')
    lines=text.splitlines(keepends=True)
    matches=[i for i,line in enumerate(lines) if "pharmacist.append('病人條件：年齡 '" in line]
    if len(matches)!=1:raise ValueError('藥師摘要位置不同')
    lines.insert(matches[0]+1,"    pharmacist.append('身高：'+(str(patient['height_cm'])+'公分' if patient.get('height_cm') is not None else '未提供')+'；體重：'+(str(patient['weight_kg'])+'公斤' if patient.get('weight_kg') is not None else '未提供'))\n")
    return ''.join(lines)+'\n# anthropometrics-summary-20261103\n'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='.');parser.add_argument('--check',action='store_true');args=parser.parse_args()
    root=Path(args.root).resolve();names=['workflow.py','asgi.py','governance.py','static/workspace.js','static/workflow.js']
    for n in names:
        if not(root/n).is_file():raise SystemExit('不是完整專案，缺少：'+n)
    original={n:(root/n).read_text(encoding='utf-8-sig') for n in names}
    payload={'workflow.py':workflow_patch(original['workflow.py']),'asgi.py':asgi_patch(original['asgi.py']),
             'governance.py':governance_patch(original['governance.py']),
             'static/workflow.js':remove_lactation_row(original['static/workflow.js']),
             'static/workspace.js':remove_lactation_row(original['static/workspace.js'])}
    if '// anthropometrics-ui-20261103' not in payload['static/workspace.js']:
        payload['static/workspace.js']+='\n'+UI
    for n,t in payload.items():
        if n.endswith('.py'):compile(t,n,'exec')
    if args.check:print('檢查通過；未修改檔案。');return
    backup=root/'runtime/code-backups'/('profile-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    for n,t in payload.items():
        if t==original[n]:continue
        dst=backup/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,dst);(root/n).write_text(t,encoding='utf-8')
    print('已更新5個程式檔案。請重啟自己的服務，身高與體重資料欄位會於啟動時新增；舊資料保留空值。')
    print('備份（有變更才建立）：'+str(backup))
    print('瀏覽器Ctrl+Shift+R後，可在對話補充「身高165公分、體重60公斤」。未變更藥物、模型或其他服務。')


if __name__=='__main__':main()
