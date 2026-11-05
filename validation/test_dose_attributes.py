from pathlib import Path
import sys,os,tempfile,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from workflow import medications,PatientStore,repair_medication_attributes,stamp
text='新病人陳志明，58歲男性，有第二型糖尿病，目前服用 Glibenclamide，一天一次，劑量75 毫克'
med=medications(text);assert len(med)==1 and med[0]['name']=='Glibenclamide' and med[0]['dose']=='75 毫克' and med[0]['frequency']=='一天一次'
for unit in ['mg','毫克']:
 r=medications('目前服用 Glibenclamide，劑量：5 '+unit+'，一天一次');assert len(r)==1 and r[0]['dose']=='5 '+unit and r[0]['frequency']=='一天一次'
r=medications('目前服用 Glibenclamide 5mg，一天一次，Aspirin 100mg，一天兩次');assert len(r)==2 and [m['frequency'] for m in r]==['一天一次','一天兩次']
assert not medications('劑量75 毫克')
assert len(medications('目前服用 Clopidogrel 75mg、Aspirin 100mg、Atorvastatin 40mg，都是一天一次'))==3
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  r=c.post('/api/workflow',json={'message':text});assert r.status_code==200,r.text
  p=r.json()['patient'];active=[m for m in p['medications'] if m['status']=='active'];assert len(active)==1 and active[0]['dose']=='75 毫克' and active[0]['frequency']=='一天一次'
  detail=c.get('/api/medications/'+str(active[0]['id'])+'/details').json();assert detail['recorded_dose']=='75 毫克' and detail['recorded_frequency']=='一天一次'
  # Recreate the exact old parser failure and verify conservative, reversible repair.
  with asgi.core.STORE.connect() as db:
   mid=active[0]['id'];created=active[0]['created']
   db.execute("UPDATE patient_meds SET dose='',frequency='' WHERE id=?",(mid,))
   db.execute('INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(p['id'],'劑量','劑量','75 毫克','','current','劑量75 毫克','active',created,stamp()))
   db.execute('UPDATE patient_events SET created=? WHERE patient_id=? AND body=?',(created,p['id'],asgi.redact(text,p['name'])))
   assert repair_medication_attributes(db,p['id'])
   assert db.execute("SELECT COUNT(*) FROM patient_meds WHERE patient_id=? AND status='active'",(p['id'],)).fetchone()[0]==1
   assert db.execute('SELECT dose,frequency FROM patient_meds WHERE id=?',(mid,)).fetchone()[:]==('75 毫克','一天一次')
   assert db.execute("SELECT COUNT(*) FROM patient_meds WHERE patient_id=? AND status='attribute'",(p['id'],)).fetchone()[0]==1
 gc.collect()
print('PASS: exact patient sentence, comma dose/frequency association, Chinese units, multiple-drug separation, attribute-only rejection, detail values, traceable legacy repair')
