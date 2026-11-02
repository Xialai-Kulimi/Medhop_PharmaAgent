"""Combine optional profile fields, folded progress, stable reading and patient search."""
from datetime import datetime, timezone
from pathlib import Path
import argparse, ast, importlib.util, shutil

SEARCH='''    def list(self,search=''):
        # patient-search-20261103: names stay encrypted at rest.
        query=key(search)
        with self.connect() as db:
            rows=db.execute('SELECT id,name,age,sex,updated FROM patients ORDER BY updated DESC,id').fetchall()
            vault=db.execute('SELECT patient_id,encrypted FROM identity_vault').fetchall()
        recovered=collections.defaultdict(set)
        for item in vault:
            try:recovered[item['patient_id']].add(self.cipher.decrypt(item['encrypted'].encode()).decode())
            except Exception:continue
        result=[]
        for row in rows:
            row=dict(row);names=recovered.get(row['id'],set())
            if len(names)==1:row['name']=next(iter(names))
            if not query or query in key(row['name'] or '') or query in key(row['id']):result.append(row)
            if len(result)>=40:break
        return result
'''

JS='''
// workspace-reading-search-20261103
const compactPreviousSteps=renderSteps;
renderSteps=function(target,steps){
  compactPreviousSteps(target,steps||[]);
  const fold=()=>{
    if(!target.isConnected)return;
    let details=target.parentElement;
    if(!details?.classList.contains('progress-details')){
      details=el('details','progress-details');
      const summary=el('summary');target.before(details);details.append(summary,target);
    }
    details.querySelector('summary').textContent='處理流程 · '+(steps||[]).length+' 個步驟（點選查看）';
  };
  if(target.isConnected)fold();else queueMicrotask(fold);
};
let inputReadingAnchor=null,inputReadingFollow=false,readingFrame=0;
function alignInputReading(){
  if(!inputReadingFollow||!inputReadingAnchor?.isConnected)return;
  const viewport=$('#log');
  viewport.scrollTop+=inputReadingAnchor.getBoundingClientRect().top-viewport.getBoundingClientRect().top-12;
}
// Existing callers cannot jump to the bottom while an answer grows.
scrollEnd=function(){if(inputReadingFollow)alignInputReading();};
const readingPreviousUserTurn=userTurn;
userTurn=function(text){
  inputReadingFollow=false;readingPreviousUserTurn(text);
  inputReadingAnchor=log.lastElementChild;inputReadingFollow=true;alignInputReading();
};
new MutationObserver(()=>{
  if(readingFrame||!inputReadingFollow)return;
  readingFrame=requestAnimationFrame(()=>{readingFrame=0;alignInputReading();});
}).observe(log,{childList:true,subtree:true,characterData:true});
for(const event of ['wheel','touchmove','pointerdown'])$('#log').addEventListener(event,()=>{inputReadingFollow=false;},{passive:true});
$('#log').addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].includes(e.key))inputReadingFollow=false;});
const readingPreviousSelect=selectExistingPatient;
selectExistingPatient=async function(id){
  inputReadingAnchor=null;inputReadingFollow=false;
  await readingPreviousSelect(id);
};
const searchablePreviousChoices=renderPatientChoices;
renderPatientChoices=function(){
  searchablePreviousChoices();
  const query=$('#patientSearch').value.trim(),picker=$('#patientPicker');
  let results=picker.querySelector('.patient-search-results');
  if(!results){results=el('div','patient-search-results');results.setAttribute('aria-live','polite');picker.append(results);}
  results.replaceChildren();results.hidden=!query;
  if(!query)return;
  if(!workflowPatients.length){
    $('#patientSelect').options[0].textContent='找不到相符病歷';
    results.append(el('p','muted','找不到相符病歷；請確認姓名、病歷號，以及目前使用的病歷資料夾。'));return;
  }
  results.append(el('p','muted','找到 '+workflowPatients.length+' 筆病歷，請點選要載入的病人：'));
  for(const p of workflowPatients.slice(0,8)){
    const choice=button((p.name||'姓名未提供')+' · '+p.id+(p.age!=null?' · '+p.age+'歲':''),()=>selectExistingPatient(p.id),'patient-search-choice');
    choice.dataset.patientId=p.id;results.append(choice);
  }
  if(workflowPatients.length>8)results.append(el('p','muted','其餘結果可從上方選單查看，或輸入更完整的姓名。'));
};
let patientSearchSequence=0;
refreshPatients=async function(){
  const sequence=++patientSearchSequence,query=$('#patientSearch').value;
  try{
    const result=await api('/api/patients?search='+encodeURIComponent(query));
    if(sequence!==patientSearchSequence||query!==$('#patientSearch').value)return;
    workflowPatients=result.patients||[];renderPatientChoices();
  }catch(error){if(sequence===patientSearchSequence)toast(error.message);}
};
'''

CSS='''
/* workspace-reading-search-20261103 */
#log{overflow-anchor:none}
.progress-details{margin:10px 0 16px;border:1px solid #dbe6ef;border-radius:8px;padding:10px 13px;color:#55758d}
.progress-details>summary{cursor:pointer;font-size:12px;font-weight:600}
.progress-details>.steps{margin:15px 0 0}
.patient-search-results{display:flex;flex-direction:column;gap:6px;margin-top:6px;max-height:230px;overflow:auto}
.patient-search-results[hidden]{display:none}
.patient-search-results>p{margin:3px 0}
.patient-search-choice{font:inherit;font-size:12px;text-align:left;line-height:1.7;padding:7px 10px;border:1px solid #c5d9e9;border-radius:7px;color:#315f83;background:#f6faff;cursor:pointer;overflow-wrap:anywhere}
.patient-search-choice:hover,.patient-search-choice:focus-visible{background:#e8f3fc;border-color:#538cb8}
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='.');parser.add_argument('--check',action='store_true');args=parser.parse_args()
    root=Path(args.root).resolve();bundle=Path(__file__).resolve().parent
    spec=importlib.util.spec_from_file_location('profile_fix',bundle/'apply_profile_fix.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    names=['workflow.py','asgi.py','governance.py','static/workflow.js','static/workspace.js','static/workspace.css']
    for name in names:
        if not(root/name).is_file():raise SystemExit('不是完整專案，缺少：'+name)
    original={name:(root/name).read_text(encoding='utf-8-sig') for name in names};payload=dict(original)
    payload['workflow.py']=helper.workflow_patch(payload['workflow.py']);payload['asgi.py']=helper.asgi_patch(payload['asgi.py'])
    payload['governance.py']=helper.governance_patch(payload['governance.py'])
    for name in ['static/workflow.js','static/workspace.js']:payload[name]=helper.remove_lactation_row(payload[name])
    if '// anthropometrics-ui-20261103' not in payload['static/workspace.js']:payload['static/workspace.js']+='\n'+helper.UI
    text=payload['governance.py']
    if '# patient-search-20261103' not in text:
        tree=ast.parse(text);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PrivateStore')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='list')
        lines=text.splitlines(keepends=True);lines[method.lineno-1:method.end_lineno]=[SEARCH];payload['governance.py']=''.join(lines)
    if '// workspace-reading-search-20261103' not in payload['static/workspace.js']:payload['static/workspace.js']+='\n'+JS
    if '/* workspace-reading-search-20261103 */' not in payload['static/workspace.css']:payload['static/workspace.css']+='\n'+CSS
    for name,text in payload.items():
        if name.endswith('.py'):compile(text,name,'exec')
    if args.check:print('檢查通過；未修改檔案。');return
    backup=root/'runtime/code-backups'/('workspace-profile-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    for name,text in payload.items():
        if text==original[name]:continue
        dst=backup/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,dst);(root/name).write_text(text,encoding='utf-8')
    print('已更新身高體重、哺乳文字、流程收合、閱讀位置與病歷搜尋。')
    print('備份（有變更才建立）：'+str(backup))
    print('請只重啟自己的服務，保留原MEDLINK_RUNTIME設定，再Ctrl+Shift+R。未刪除病歷或修改其他服務。')


if __name__=='__main__':main()
