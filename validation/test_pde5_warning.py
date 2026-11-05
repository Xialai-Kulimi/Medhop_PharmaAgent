from pathlib import Path
import os,sys,tempfile,time,gc,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from label_interactions import LabelInteractions
labels=LabelInteractions(ROOT,json.loads((ROOT/'data/pharma_pages.json').read_text(encoding='utf-8')))
def patient(names):return {'medications':[{'name':n,'status':'active'} for n in names]}
assert len(labels.evaluate(patient(['Sildenafil','Tadalafil']))['alerts'])==1
for names in [['Sildenafil'],['Tadalafil'],['Sildenafil','Aspirin'],['Sildenafil','Vardenafil']]:assert not labels.evaluate(patient(names))['alerts']
stopped=patient(['Sildenafil','Tadalafil']);stopped['medications'][1]['status']='stopped';assert not labels.evaluate(stopped)['alerts']
with tempfile.TemporaryDirectory(dir=ROOT/"runtime",ignore_cleanup_errors=True) as folder:
 os.environ['MEDLINK_RUNTIME']=folder;os.environ['MEDLINK_REQUIRE_LOGIN']='0';os.environ['MODEL_URL']='http://127.0.0.1:1/api/v1'
 from fastapi.testclient import TestClient
 print("loading API",flush=True)
 import asgi
 print("API imported",flush=True)
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  print("API started",flush=True)
  response=c.post('/api/workflow',json={'message':'新病人研究測試甲，60歲男性。\n目前用藥：\nSildenafil 50mg PRN\nTadalafil 5mg QD'});assert response.status_code==200,response.text
  data=response.json();assert len(data['interaction_alerts'])==1,data
  assert '降低血壓' in data['interaction_alerts'][0]['message']
  assert data['interaction_alert_sources'][0]['url'].startswith('https://labeling.pfizer.com/')
  print("immediate alert passed",flush=True)
  job=c.post('/api/review',json={'use_model':False}).json()['id']
  for _ in range(300):
   result=c.get('/api/jobs/'+job).json()
   if result['status']!='running':break
   time.sleep(.1)
  assert result['status']=='done',result
  assert '降低血壓' in result['answer'],result
  assert result['assessment']['pairs'][0]['status']=='interaction'
  assert result['assessment']['interaction_alerts'][0]['source_ids']
  selected=c.post('/api/patients/select',json={'id':data['patient']['id']}).json()
  assert selected['interaction_alerts'] and selected['selected_review']
 gc.collect()
print('PASS: immediate warning, completed review, saved selection, original label link, single/stopped/unrelated negative controls')
