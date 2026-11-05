from pathlib import Path
import tempfile,os,sys,json,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 from workflow import medications
 from governance import drafts
 checks=[]
 with TestClient(asgi.app,base_url='http://127.0.0.1') as client:
  def post(path,body):
   response=client.post(path,json=body);assert response.status_code==200,response.text;return response.json()
  for name in ['workspace.js','workspace.css']:assert client.get('/'+name).status_code==200
  html=client.get('/').text;assert '新病人王小明' not in html and '描述病人狀況或用藥情形' in html
  p=post('/api/workflow',{'message':'新病人介面測試甲，68歲男性。五月裝過心臟支架，有胃食道逆流，目前吃 Clopidogrel 75mg、Aspirin 100mg、Atorvastatin 40mg，都是一天一次。'})['patient']
  detail=client.get('/api/medications/'+str(p['medications'][0]['id'])+'/details').json()
  assert not detail['product_confirmed'] and detail['candidates']
  assert all(d['contraindications'] is None for d in detail['candidates'])
  assert any(d['dose_paragraphs'] and d['dosage_available'] for d in detail['candidates'])
  assert any(d['適應症'] for d in detail['candidates']);checks+=['test_sentence_removed_from_placeholder','original_snapshot_fields_restored','segmented_dose_originals_and_explicit_missing_contraindications','unconfirmed_candidates_never_treated_as_patient_product']
  rid=post('/api/review',{'use_model':False})['id']
  for _ in range(100):
   job=client.get('/api/jobs/'+rid).json()
   if job['status']!='running':break
   time.sleep(.1)
  assert job['status']=='done',job
  report=client.get('/api/reports/'+rid).json();assert '懷孕狀態' not in report['pharmacist'] and '哺乳狀態' not in report['pharmacist']
  assert not any('懷孕' in reason['text'] or '哺乳' in reason['text'] for reason in report['reasons']);checks.append('male_report_has_no_pregnancy_lactation_prompt')
  p=post('/api/workflow',{'message':'今天腸胃科開了 Omeprazole 20mg，一天一次飯前吃，可以直接配嗎？'})['patient'];assert p['medications'][-1]['kind']=='new_order'
  rid=post('/api/review',{'use_model':False})['id']
  for _ in range(100):
   j=client.get('/api/jobs/'+rid).json()
   if j['status']!='running':break
   time.sleep(.1)
  assert j['status']=='done'
  body={'review':rid,'text':'已停用：Omeprazole\n新增處方：Pantoprazole 40mg，一天一次飯前','note':'隔離流程驗證，非實際醫療處置','acknowledged':False}
  assert client.post('/api/prescriptions/confirm-change',json=body).status_code==409
  bad={**body,'acknowledged':True,'text':'已停用：UnknownDrug\n新增處方：Pantoprazole 40mg，一天一次飯前'}
  assert client.post('/api/prescriptions/confirm-change',json=bad).status_code==409
  assert any(m['name']=='Omeprazole' and m['status']=='active' for m in client.get('/api/patients').json()['patient']['medications'])
  updated=post('/api/prescriptions/confirm-change',{**body,'acknowledged':True})['patient']
  assert any(m['name']=='Omeprazole' and m['status']=='stopped' for m in updated['medications'])
  assert any(m['name']=='Pantoprazole' and m['confirmation']=='confirmed' and m['status']=='active' for m in updated['medications'])
  assert client.get('/api/reports/'+rid).status_code==409
  assert client.post('/api/prescriptions/confirm-change',json={**body,'acknowledged':True}).status_code==409
  checks+=['actual_confirmed_change_requires_acknowledgment','unknown_stop_rolls_back','confirmed_change_preserves_stopped_and_new_prescriptions','old_report_and_repeat_change_blocked']
  p=post('/api/workflow',{'message':'他最近膝蓋痛，自己買了 Ibuprofen 來吃，一天兩三顆。'})['patient'];m=p['medications'][-1];assert m['name']=='Ibuprofen' and m['kind']=='reported' and not m['frequency'] and not m['dose']
  checks.append('reported_tablet_count_not_invented_as_frequency_or_strength')
  first_patient_id=p['id']
  asgi.core.STORE.reset(client.cookies.get('medlink_session'))
  p=post('/api/workflow',{'message':'新病人介面測試乙，32歲女性。目前懷孕12週。'})['patient']
  report=drafts(p,{'pairs':[],'medications':[]});assert '懷孕中' in report['pharmacist'] and any('懷孕' in r['text'] for r in report['reasons'])
  checks.append('female_pregnancy_context_retained')
  assert p['id']!=first_patient_id
  assert not any('病歷號：' in event['body'] and p['id'] not in event['body'] for event in p['events'])
  checks.append('new_private_name_id_is_consistent_with_stored_input')
 result={'all_passed':True,'checks':checks,'isolated_database':True,'production_test_patients_created':0,'clinical_probabilities_invented':False,'substitute_doses_invented':False}
 (ROOT/'validation/workspace-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
