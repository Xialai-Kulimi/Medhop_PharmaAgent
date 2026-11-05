from pathlib import Path
import os,tempfile,sys,json,time,gc,sqlite3
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from workflow import stamp
from label_interactions import repair_compounds
text='今天醫師新增撲菌特錠 BAKTAR，成分是 Sulfamethoxazole 與 Trimethoprim，一天三餐飯後吃'
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   r=c.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  from agent_harness import seed,ask
  seed(c,'測試甲',[('Glibenclamide','','一天一次')],history=['第二型糖尿病'],age=32,sex='女',pregnancy='pregnant',pregnancy_weeks=12)
  add=('add_medications',{'medications':[{'name':'BAKTAR','frequency':'一天三餐飯後吃','kind':'new_order'}]})
  run=ask(c,text,[add]);result={'patient':run['patient'],**run['alerts']}
  active=[m for m in result['patient']['medications'] if m['status']=='active']
  assert len(active)==2,active
  assert len(result['interaction_alerts'])==1 and len(result['patient_alerts'])==1
  assert result['interaction_alert_sources'][0]['page']==4 and 'Glibenclamide' in result['interaction_alert_sources'][0]['text']
  again=ask(c,text,[add]);assert 'update_medication' in again['results'][0]['error']
  assert len([m for m in again['patient']['medications'] if m['status']=='active'])==2
  jobid=post('/api/review',{'use_model':True})['id']
  for _ in range(300):
   job=c.get('/api/jobs/'+jobid).json()
   if job['status']!='running':break
   time.sleep(.1)
  assert job['status']=='done',job
  assert len(job['assessment']['pairs'])==1 and job['assessment']['pairs'][0]['status']=='interaction'
  assert '低血糖' in job['answer'] and '孕婦禁用' in job['answer']
  assert job['assessment']['agent']['status']=='disabled'
  report=c.get('/api/reports/'+jobid).json()
  assert '低' in report['pharmacist'] and c.get('/api/reports/'+jobid+'/delivery').status_code==409
  # Product-owned rule must not be applied to a standalone ingredient.
  for name in ['Trimethoprim','Sulfamethoxazole']:
   p={**result['patient'],'medications':[active[0],{**active[1],'name':name}]}
   assert not asgi.app.state.label_interactions.evaluate(p)['alerts']
  # Legacy split components are retired only with matching explicit source event and timestamp.
  store=asgi.core.STORE;pid=result['patient']['id']
  with store.connect() as db:
   parent=next(m for m in active if m['name']=='BAKTAR')
   db.execute('UPDATE patient_meds SET body=?,frequency=? WHERE id=?',('今天醫師新增撲菌特錠 BAKTAR','',parent['id']))
   db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(pid,text,'藥師輸入',parent['created']))
   for name in ['Sulfamethoxazole','Trimethoprim']:
    body='成分是 Sulfamethoxazole 與 Trimethoprim'
    db.execute('INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(pid,name,name.lower(),'','','new_order',body,'active',parent['created'],stamp()))
   assert repair_compounds(db,pid)
   assert db.execute("SELECT COUNT(*) FROM patient_meds WHERE patient_id=? AND status='component'",(pid,)).fetchone()[0]==2
   assert db.execute('SELECT frequency FROM patient_meds WHERE id=?',(parent['id'],)).fetchone()[0]=='一天三餐飯後吃'
 gc.collect()
print('PASS: compound entry, immediate dual alerts, model-independent report, original page citation, repeat merge, ingredient isolation, legacy repair, delivery gate')
