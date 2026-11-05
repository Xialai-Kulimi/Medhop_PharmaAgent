from pathlib import Path
import os,sys,tempfile,time,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   r=c.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  def review():
   rid=post('/api/review',{'use_model':False})['id']
   for _ in range(200):
    job=c.get('/api/jobs/'+rid).json()
    if job['status']!='running':break
    time.sleep(.05)
   assert job['status']=='done',job
   return rid
  from agent_harness import seed,ask
  a=seed(c,'測試甲',[('Glibenclamide','','一天一次'),('BAKTAR','800/160mg','一天兩次','new_order')],age=58,sex='男')['patient'];ra=review()
  post('/api/patients/reset',{})
  b=seed(c,'測試乙',[('Sildenafil','50mg','PRN'),('Tadalafil','5mg','QD')],age=60,sex='男')['patient'];rb=review()
  loaded=post('/api/patients/select',{'id':a['id']})
  assert loaded['selected_review']['id']==ra and loaded['selected_review']['is_current']
  assert loaded['selected_review']['patient_id']==a['id'] and not loaded['review_needed'] and not loaded['changed']
  ass=loaded['selected_review']['result']['assessment'];assert ass['pharmacy_rag']['source_ids'] and any(s['id'].startswith('L') for s in ass['sources'])
  assert c.get('/api/reviews/'+rb).status_code==409
  with asgi.core.STORE.connect() as db:
   before=db.execute('SELECT COUNT(*) FROM patient_reviews').fetchone()[0];events=db.execute('SELECT COUNT(*) FROM patient_events').fetchone()[0]
  again=post('/api/patients/select',{'id':a['id']});assert again['selected_review']['id']==ra
  with asgi.core.STORE.connect() as db:
   assert db.execute('SELECT COUNT(*) FROM patient_reviews').fetchone()[0]==before
   assert db.execute('SELECT COUNT(*) FROM patient_events').fetchone()[0]==events
  gid=next(m['id'] for m in a['medications'] if m['name']=='Glibenclamide')
  ask(c,'Glibenclamide 是 5mg，一天一次',[('update_medication',{'medication_id':gid,'dose':'5mg'})])
  stale=post('/api/patients/select',{'id':a['id']});assert stale['selected_review']['id']==ra and not stale['selected_review']['is_current']
  assert c.get('/api/reports/'+ra).status_code==409
  current=review();assert post('/api/patients/select',{'id':a['id']})['selected_review']['id']==current
  # Research queries remain restricted to the selected patient and retain source data.
  import json
  with asgi.core.STORE.connect() as db:
   p=asgi.core.STORE.get(a['id'])
   db.execute('INSERT INTO patient_queries VALUES(?,?,?,?,?)',('q-a',a['id'],p['version'],json.dumps({'question':'isolated query','answer':'isolated answer','sources':[],'paths':[]}), '2026-10-28'))
  selected=post('/api/patients/select',{'id':a['id']});assert selected['selected_query']['id']=='q-a'
  selected=post('/api/patients/select',{'id':b['id']});assert selected['selected_review']['id']==rb and selected['selected_query'] is None
  post('/api/patients/reset',{})
  p=seed(c,'測試丙',[('Aspirin','100mg','一天一次')],age=40,sex='男')['patient']
  selected=post('/api/patients/select',{'id':p['id']});assert selected['selected_review'] is None and selected['review_needed']
 gc.collect()
print('PASS: patient-matched saved review/sources, no rerun or clinical mutation, stale version preserved and blocked, current review preferred, research ownership, never-reviewed case fallback')
