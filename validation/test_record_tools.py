from pathlib import Path
import os,sys,tempfile,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));(ROOT/'runtime').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 from fastapi.testclient import TestClient
 import asgi
 checks=[]
 with TestClient(asgi.app,base_url="http://127.0.0.1") as client:
  def post(path,body):
   r=client.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  p=post('/api/workflow',{'message':'新病人測試甲，68 歲男性。五月裝過心臟支架，有胃食道逆流，目前吃 Clopidogrel 75mg、Aspirin 100mg，都是一天一次。\n過敏史：Penicillin'})['patient'];pid=p['id']
  asgi.core.STORE.save_review('record-edit-version',p,{'assessment':{'medications':p['medications'],'pairs':[]},'answer':'待藥師確認'})
  e=post('/api/patients/edit',{'history':['心臟支架置放','胃食道逆流'],'allergies':'Penicillin（皮疹）'})
  assert e['patient']['history']==['心臟支架置放','胃食道逆流'] and e['patient']['version']>p['version']
  assert client.get('/api/reports/record-edit-version').status_code==409
  assert post('/api/patients/edit',{'history':['心臟支架置放','胃食道逆流']})['message']==''
  later=post('/api/workflow',{'message':'病史：高血壓'})['patient'];assert later['history']==['心臟支架置放','胃食道逆流','高血壓']
  checks.append('history_edit_replaces_derived_history_and_invalidates_old_review')
  aspirin=next(m for m in later['medications'] if m['name']=='Aspirin')
  r=post('/api/medications/save',{'id':aspirin['id'],'name':'Aspirin','dose':'81 mg','frequency':'每天一次','kind':'current'})
  assert next(m for m in r['patient']['medications'] if m['id']==aspirin['id'])['dose']=='81 mg'
  r=post('/api/medications/save',{'name':'Pantoprazole','dose':'40 mg','frequency':'每天一次','kind':'new_order'});assert any(m['name']=='Pantoprazole' for m in r['patient']['medications'])
  assert client.post('/api/medications/save',json={'name':'Pantoprazole','dose':'40 mg','frequency':'每天一次','kind':'new_order'}).status_code==409
  assert client.post('/api/medications/save',json={'name':'A、B','kind':'current'}).status_code==409
  for status in ['stopped','active','removed']:post('/api/medications/status',{'id':aspirin['id'],'status':status})
  p=client.get('/api/patients').json()['patient'];assert all(m['id']!=aspirin['id'] for m in p['medications'])
  assert client.post('/api/medications/status',json={'id':aspirin['id'],'status':'active'}).status_code==409
  sources=[e['source'] for e in p['events']];assert {'藥師編輯病史與過敏史','藥師編輯用藥','藥師新增用藥','藥師變更用藥狀態'}<=set(sources)
  checks.append('medication_edit_add_stop_remove_logged_with_original_rows_kept')
  r=post('/api/patients/edit',{'name':'測試乙','age':69});assert r['patient']['name']=='測試乙' and r['patient']['age']==69
  names=[x['id'] for x in client.get('/api/patients?search=測試乙').json()['patients']];assert names==[pid] and not client.get('/api/patients?search=測試甲').json()['patients']
  checks.append('rename_updates_encrypted_identity_lookup')
  exported=client.get('/api/patients/'+pid+'/export');assert exported.status_code==200 and pid+'.json' in exported.headers['content-disposition']
  record=exported.json();assert record['format']=='medhop-patient-record' and record['patient']['name']=='測試乙'
  imported=post('/api/patients/import',{'file_name':pid+'.json','record':record})['patient']
  assert imported['id']!=pid and imported['history']==p['history'] and imported['name']=='測試乙'
  same=lambda x:sorted((m['name'],m['dose'],m['frequency'],m['kind'],m['status']) for m in x['medications'])
  assert same(imported)==same(client.get('/api/patients/'+pid+'/export').json()) and len(client.get('/api/patients').json()['patients'])==2
  checks.append('export_import_round_trip_creates_new_patient_without_overwrite')
  assert client.post('/api/patients/import',json={'file_name':'x.json','record':{'format':'other','format_version':1,'patient':{}}}).status_code==422
  assert client.post('/api/patients/edit',json={'age':1},headers={'Origin':'http://example.invalid'}).status_code==403
  checks.append('foreign_files_and_cross_origin_writes_rejected')
 result={'all_passed':True,'checks':checks,'isolated_runtime':True,'clinical_decisions_automated':False}
 (ROOT/'validation/record-tools-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
