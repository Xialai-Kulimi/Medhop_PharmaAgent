from pathlib import Path
import os,sys,tempfile,json,sqlite3
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));(ROOT/'runtime').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 from governance import drafts
 checks=[]
 with TestClient(asgi.app,base_url="http://127.0.0.1") as client:
  def post(path,body):
   r=client.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  assert client.get('/api/patients').json()['patients']==[]
  from agent_harness import ask
  missing=ask(client,'病歷號：TEST001',[('add_medications',{'medications':[{'name':'TEST001'}]})])
  assert '沒有選擇病人' in missing['results'][0]['error'] and missing['patient'] is None and not client.get('/api/patients').json()['patients']
  profile={'name':'測試甲','age':32,'sex':'女','pregnancy':'pregnant','pregnancy_weeks':12,'lactation':'not_breastfeeding','history':'隔離表單測試\ndiabetes','allergies':'未確認'}
  r=post('/api/patients/profile',profile);p=r['patient'];pid=p['id'];assert r['action']=='created' and pid.startswith('P-') and not p['medications']
  assert p['age']==32 and p['pregnancy']=='pregnant' and p['pregnancy_weeks']==12
  post('/api/patients/reset',{})
  again=post('/api/patients/profile',profile);assert again['action']=='ambiguous' and again['patient'] is None and [x['id'] for x in again['candidates']]==[pid]
  post('/api/patients/select',{'id':pid})
  assert len(client.get('/api/patients').json()['patients'])==1;checks.append('auto_id_same_name_asks_before_reuse_no_history_as_medication')
  p=ask(client,'目前用藥：\nAmlodipine\nTacrolimus',[('add_medications',{'medications':[{'name':'Amlodipine'},{'name':'Tacrolimus'}]})])['patient']
  asgi.core.STORE.save_review('profile-version',p,{'assessment':{'medications':p['medications'],'pairs':[]},'answer':'待藥師確認'})
  report=drafts(p,{'medications':p['medications'],'pairs':[]});assert '懷孕中' in report['pharmacist'] and any(r['id']=='patient-special-population' for r in report['reasons'])
  updated=post('/api/patients/profile',{'age':33,'sex':'女','pregnancy':'not_pregnant','lactation':'unknown'})['patient']
  assert updated['age']==33 and updated['pregnancy_weeks'] is None and updated['version']>p['version']
  assert client.get('/api/reports/profile-version').status_code==409;checks.append('demographic_change_invalidates_old_review_and_clears_old_weeks')
  bad=client.post('/api/patients/profile',json={'age':150});assert bad.status_code==422
  assert client.post('/api/patients/profile',json={'pregnancy':'unknown','pregnancy_weeks':10}).status_code==409
  checks.append('profile_range_validation_no_assumed_pregnancy')
  p=ask(client,'年齡：34歲\n性別：女\n懷孕狀態：懷孕中\n懷孕週數：16週\n哺乳狀態：否',[('update_patient_profile',{'age':34,'sex':'女','pregnancy':'pregnant','pregnancy_weeks':16,'lactation':'not_breastfeeding'})])['patient']
  assert p['age']==34 and p['pregnancy_weeks']==16 and len(p['medications'])==2
  checks.append('agent_demographics_persist_and_do_not_create_medications')
  post('/api/patients/reset',{})
  p=post('/api/patients/profile',{'name':'測試乙','age':0})['patient'];assert p['age']==0 and p['sex']=='未確認' and p['pregnancy']=='unknown'
  checks.append('new_patient_defaults_unknown_and_zero_age_preserved')
 result={'all_passed':True,'checks':checks,'isolated_runtime':True,'clinical_decisions_automated':False}
 (ROOT/'validation/patient-profile-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))

