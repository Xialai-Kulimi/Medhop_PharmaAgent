from pathlib import Path
import os,tempfile,sys,json,time,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 from workflow import merge_compatible_medications
 from agent_harness import seed,ask
 checks=[]
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   r=c.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  first=seed(c,'林怡君',['Glibenclamide'],history=['第二型糖尿病'],age=32,sex='女',pregnancy='pregnant',pregnancy_weeks=12)['patient'];mid=first['medications'][0]['id']
  run=ask(c,'Glibenclamide 是一天一次',[('update_medication',{'medication_id':mid,'frequency':'一天一次'})])
  active=[m for m in run['patient']['medications'] if m['status']=='active'];assert len(active)==1 and active[0]['id']==mid and active[0]['frequency']=='一天一次'
  assert len(run['alerts']['patient_alerts'])==1 and len(run['alerts']['patient_alert_sources'])==1
  detail=c.get('/api/medications/'+str(mid)+'/details').json();assert detail['id']==mid and detail['recorded_frequency']=='一天一次' and detail['population_checks']
  # Mentioning a drug the record already has must not add a second active row; the agent is told to update instead.
  for _ in range(2):
   again=ask(c,'目前仍服用 Glibenclamide，一天一次',[('add_medications',{'medications':[{'name':'Glibenclamide','frequency':'一天一次'}]})])
   assert 'update_medication' in again['results'][0]['error']
  assert len([m for m in again['patient']['medications'] if m['status']=='active'])==1
  checks+=['later_frequency_updates_the_original_id','repeated_messages_do_not_add_active_duplicates','one_source_and_one_pregnancy_alert','medication_detail_reflects_the_update']
  rid=post('/api/review',{'use_model':True})['id']
  for _ in range(100):
   job=c.get('/api/jobs/'+rid).json()
   if job['status']!='running':break
   time.sleep(.05)
  assert job['status']=='done' and len(job['assessment']['medications'])==1 and len(job['assessment']['population_checks'])==1 and not job['assessment']['duplicates']
  assert job['assessment']['agent']['status']=='disabled';checks.append('single_drug_does_not_invoke_pair_agent')
  b=ask(c,'Glibenclamide 改成 5mg',[('update_medication',{'medication_id':mid,'dose':'5mg'})])['patient']
  assert len([m for m in b['medications'] if m['status']=='active'])==1 and next(m for m in b['medications'] if m['id']==mid)['dose']=='5mg'
  assert c.get('/api/reports/'+rid).status_code==409
  # A second regimen of the same drug can still be entered from the record form; it stays a separate row.
  b=post('/api/medications/save',{'name':'Glibenclamide','dose':'10mg','frequency':'一天一次','kind':'current'})['patient']
  assert len([m for m in b['medications'] if m['status']=='active'])==2
  result=post('/api/patients/select',{'id':b['id']});assert len(result['patient_alerts'])==1
  checks+=['dose_change_keeps_one_row','separate_regimen_only_from_the_record_form','conflicting_regimens_still_have_one_population_warning','change_invalidates_old_report']
  # Reproduce a pre-fix active duplicate and exercise the upgrade merge directly.
  with asgi.core.STORE.connect() as db:
   db.execute("INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",(b['id'],'Glibenclamide','glibenclamide','','','current','legacy sparse duplicate','active','test','test'))
   db.execute("UPDATE patient_meds SET name_key='glibenclamide',status='stopped' WHERE patient_id=? AND dose='10mg'",(b['id'],))
   db.execute("UPDATE patient_meds SET name_key='glibenclamide' WHERE body='legacy sparse duplicate'")
   assert merge_compatible_medications(db,b['id'])==1
  checks.append('legacy_sparse_duplicates_can_be_repaired_without_deletion')
 gc.collect()
 result={'all_passed':True,'checks':checks,'isolation':'temporary database','live_records_created':0}
 (ROOT/'validation/medication-merge-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
