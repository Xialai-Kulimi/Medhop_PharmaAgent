from pathlib import Path
import os,tempfile,sys,json,time,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 from workflow import merge_compatible_medications
 checks=[]
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   r=c.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  text='新病人林怡君，32歲女性，目前懷孕12週。病史有第二型糖尿病，目前仍服用 Glibenclamide'
  first=post('/api/workflow',{'message':text})['patient'];mid=first['medications'][0]['id']
  result=post('/api/workflow',{'message':text+'，一天一次'})
  active=[m for m in result['patient']['medications'] if m['status']=='active'];assert len(active)==1 and active[0]['id']==mid and active[0]['frequency']=='一天一次'
  assert len(result['patient_alerts'])==1 and len(result['patient_alert_sources'])==1
  assert any(m['status']=='merged' for m in result['patient']['medications'])
  old_mid=next(m['id'] for m in result['patient']['medications'] if m['status']=='merged')
  detail=c.get('/api/medications/'+str(old_mid)+'/details').json();assert detail['id']==mid and detail['recorded_frequency']=='一天一次' and detail['population_checks']
  for _ in range(2):result=post('/api/workflow',{'message':text+'，一天一次'})
  assert len([m for m in result['patient']['medications'] if m['status']=='active'])==1
  checks+=['missing_frequency_then_known_frequency_merges_into_original_id','repeated_messages_do_not_add_active_duplicates','one_source_and_one_pregnancy_alert','original_entries_retained_as_merged_history','old_medication_detail_resolves_to_current_record']
  rid=post('/api/review',{'use_model':True})['id']
  for _ in range(100):
   job=c.get('/api/jobs/'+rid).json()
   if job['status']!='running':break
   time.sleep(.05)
  assert job['status']=='done' and len(job['assessment']['medications'])==1 and len(job['assessment']['population_checks'])==1 and not job['assessment']['duplicates']
  assert job['assessment']['agent']['status']=='disabled';checks.append('merged_single_drug_does_not_invoke_pair_agent')
  a=post('/api/workflow',{'message':'目前服用 Glibenclamide 5mg，一天一次'})['patient']
  assert len([m for m in a['medications'] if m['status']=='active'])==1
  b=post('/api/workflow',{'message':'目前服用 Glibenclamide 10mg，一天一次'})['patient']
  assert len([m for m in b['medications'] if m['status']=='active'])==2
  assert c.get('/api/reports/'+rid).status_code==409
  result=post('/api/workflow',{'message':'重新檢查'});assert len(result['patient_alerts'])==1
  checks+=['missing_dose_supplemented_without_duplicate','conflicting_explicit_doses_not_silently_overwritten','conflicting_regimens_still_have_one_population_warning','change_invalidates_old_report']
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
