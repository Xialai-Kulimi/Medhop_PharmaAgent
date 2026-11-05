from pathlib import Path
import os,sys,tempfile,gc,json,zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  catalog=asgi.app.state.catalog
  for license in ['衛署藥製字第052301號','衛署藥製字第052530號','衛署藥製字第055272號']:
   p=catalog.current_product(license);assert p['藥品類別'] is None
   status=p['source_availability'];assert status['classification']['status']=='source_null' and status['dosage']['status']=='reference_only'
   assert status['label_status']=='not_matched' and not status['labels']
  known=catalog.current_product('衛署藥製字第004919號');assert any(x['file']=='A0049191G0.pdf' for x in known['source_availability']['labels'])
  products=c.get('/api/catalog',params={'q':'Atorvastatin'}).json()['products'];assert products and all('source_availability' in x for x in products)
  r=c.post('/api/workflow',json={'message':'新病人測試甲，68歲男性，目前服用 Clopidogrel 75mg、Aspirin 100mg、Atorvastatin 40mg，都是一天一次'}).json()
  meds=[m for m in r['patient']['medications'] if m['status']=='active'];assert [m['name'] for m in meds]==['Clopidogrel','Aspirin','Atorvastatin']
 gc.collect()
print('PASS: raw null/pointer distinguished, exact-license PDF links, unmatched product not substituted, catalog endpoint metadata, medication order preserved')
