from pathlib import Path
import sys,os,tempfile,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from workflow import medications,repair_medication_attributes,stamp
# Doses and frequencies now arrive as separate tool arguments. medications() only reads legacy events, for the repair below.
text='新病人陳志明，58歲男性，有第二型糖尿病，目前服用 Glibenclamide，一天一次，劑量75 毫克'
med=medications(text);assert len(med)==1 and med[0]['name']=='Glibenclamide' and med[0]['dose']=='75 毫克' and med[0]['frequency']=='一天一次'
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 from agent_harness import seed
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  p=seed(c,'陳志明',[('Glibenclamide','75 毫克','一天一次')],history=['第二型糖尿病'],age=58,sex='男')['patient']
  active=[m for m in p['medications'] if m['status']=='active'];assert len(active)==1 and active[0]['dose']=='75 毫克' and active[0]['frequency']=='一天一次'
  detail=c.get('/api/medications/'+str(active[0]['id'])+'/details').json();assert detail['recorded_dose']=='75 毫克' and detail['recorded_frequency']=='一天一次'
  # Recreate the exact old parser failure and verify conservative, reversible repair.
  with asgi.core.STORE.connect() as db:
   mid=active[0]['id'];created=active[0]['created']
   db.execute("UPDATE patient_meds SET dose='',frequency='' WHERE id=?",(mid,))
   db.execute('INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(p['id'],'劑量','劑量','75 毫克','','current','劑量75 毫克','active',created,stamp()))
   db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(p['id'],asgi.redact(text,p['name']),'藥師輸入',created))
   assert repair_medication_attributes(db,p['id'])
   assert db.execute("SELECT COUNT(*) FROM patient_meds WHERE patient_id=? AND status='active'",(p['id'],)).fetchone()[0]==1
   assert db.execute('SELECT dose,frequency FROM patient_meds WHERE id=?',(mid,)).fetchone()[:]==('75 毫克','一天一次')
   assert db.execute("SELECT COUNT(*) FROM patient_meds WHERE patient_id=? AND status='attribute'",(p['id'],)).fetchone()[0]==1
 gc.collect()
print('PASS: structured dose and frequency kept as entered, detail values, traceable legacy repair')
