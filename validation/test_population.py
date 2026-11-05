from pathlib import Path
import os,tempfile,sys,json,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 text='新病人林怡君，32歲女性，目前懷孕12週。\n病史有第二型糖尿病，目前仍服用 Glibenclamide'
 checks=[]
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   response=c.post(path,json=body);assert response.status_code==200,response.text;return response.json()
  r=post('/api/workflow',{'message':text});assert r['review_needed'] and r['patient']['name']=='林怡君'
  assert len(r['patient']['medications'])==1 and not r['patient']['medications'][0]['dose']
  assert len(r['patient_alerts'])==1 and r['patient_alerts'][0]['status']=='contraindicated'
  assert '不適合' in r['patient_alerts'][0]['message']
  src=r['patient_alert_sources'][0];assert src['file']=='A0047151G0.pdf' and src['page']==1 and src['text']=='孕婦或可能懷孕之婦女禁用本藥。'
  checks+=['exact_short_message_triggers_immediate_alert_without_brand_or_request','one_medication_no_dose_still_checked','original_label_quote_and_page_attached']
  rid=post('/api/review',{'use_model':True})['id']
  for _ in range(100):
   job=c.get('/api/jobs/'+rid).json()
   if job['status']!='running':break
   time.sleep(.05)
  assert job['status']=='done' and job['assessment']['overall']=='contact_prescriber'
  assert '不適合' in job['answer'] and job['assessment']['population_checks'][0]['status']=='contraindicated'
  assert not job['assessment']['pairs'] and job['assessment']['agent']['status']=='disabled'
  report=c.get('/api/reports/'+rid).json();assert any(r['id'].startswith('population-') for r in report['reasons'])
  assert '孕婦或可能懷孕之婦女禁用本藥。' in report['pharmacist']
  assert c.get('/api/reports/'+rid+'/delivery').status_code==409
  checks+=['single_medication_report_finishes_without_model_wait','pregnancy_contraindication_in_report_and_handoff','unapproved_delivery_blocked']
  detail=c.get('/api/medications/'+str(r['patient']['medications'][0]['id'])+'/details').json();assert detail['population_checks'][0]['quote']==src['text']
  changed=post('/api/workflow',{'message':'目前沒有懷孕'})
  assert not changed['patient_alerts'] and c.get('/api/reports/'+rid).status_code==409
  checks.append('condition_change_rechecks_and_invalidates_old_report')
  post('/api/patients/reset',{})
  unknown=post('/api/workflow',{'message':'新病人測試乙，32歲女性，目前服用 Glibenclamide'})
  assert not unknown['patient_alerts'] and unknown['population_checks'][0]['status']=='condition_unconfirmed'
  post('/api/patients/reset',{})
  other=post('/api/workflow',{'message':'新病人測試丙，32歲女性，目前懷孕12週。目前服用 UnknownDrug'})
  assert not other['patient_alerts'] and not other['population_checks']
  post('/api/patients/reset',{})
  delayed=post('/api/workflow',{'message':'新病人測試丁，32歲女性。目前服用 Glibenclamide'})
  delayed=post('/api/workflow',{'message':'目前懷孕12週'})
  assert delayed['patient_alerts'] and len(delayed['patient']['medications'])==1
  checks+=['unknown_pregnancy_not_asserted_as_pregnant','unindexed_drug_not_assumed_safe_or_contraindicated','later_pregnancy_input_rechecks_existing_single_medication']
 import gc;gc.collect()
 result={'all_passed':True,'checks':checks,'live_patients_created':0,'source_indexed_rules':len(asgi.app.state.population.rules),'coverage':'Only indexed original-label rules; does not establish universal pregnancy medication safety.'}
 (ROOT/'validation/population-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
