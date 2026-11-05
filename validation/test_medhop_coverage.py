from pathlib import Path
import tempfile,os,sys,gc,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  response=c.get('/api/medhop/coverage');assert response.status_code==200,response.text
  data=response.json();assert data['searchable_names']==658 and data['drug_typed_entities']==589 and len(data['examples'])==4
  assert c.get('/api/medhop/coverage',params={'q':'Lamotrigine'}).json()['matches']==[]
  assert c.get('/api/medhop/coverage',params={'q':'Carbamazepine'}).json()['matches']==[]
  row=c.get('/api/medhop/coverage',params={'q':'Phenytoin'}).json()['matches'][0];assert row['title']=='PHENYTOIN' and row['source_supported_edges']>0
 gc.collect()
print('PASS: coverage endpoint reflects actual local counts, paths and missing names')
