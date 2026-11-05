from pathlib import Path
import os,sys,tempfile,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   r=c.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  lookup=asgi.core.STORE.ingredient_lookup
  for name in ['BAKTAR','撲菌特錠','撲菌特錠400毫克']:
   result=lookup.resolve(name);assert result['status']=='resolved',(name,result)
   assert set(result['ingredients'])=={'TRIMETHOPRIM','SULFAMETHOXAZOLE'}
   assert result['candidates'][0]['license']=='衛署藥製字第004919號'
   assert not result['product_confirmed']
  assert lookup.resolve('不存在測試藥品XYZ')['status']=='not_found'
  # Conflicting source rows must remain ambiguous, never choose one arbitrarily.
  from ingredient_lookup import IngredientLookup
  from types import SimpleNamespace
  rows=[{'許可證字號':'a','中文品名':'測試藥','英文品名':'TestDrug','主成分略述':'AAA'},{'許可證字號':'b','中文品名':'測試藥','英文品名':'TestDrug','主成分略述':'BBB'}]
  fake=SimpleNamespace(rows=rows,meta={'url':'local','filter_date':'test'},current_product=lambda lic:next(r for r in rows if r['許可證字號']==lic))
  ambiguous=IngredientLookup(fake).resolve('TestDrug');assert ambiguous['status']=='ambiguous' and not ambiguous['ingredients']
  post('/api/workflow',{'message':'新病人測試甲，55歲男性。目前服用 Glibenclamide，一天一次'})
  r=post('/api/workflow',{'message':'今天醫師新增 BAKTAR，一天三餐飯後吃'})
  meds=[m for m in r['patient']['medications'] if m['status']=='active'];assert len(meds)==2
  med=next(m for m in meds if m['name']=='BAKTAR');assert len(med['components'])==2 and r['interaction_alerts']
  detail=c.get('/api/medications/'+str(med['id'])+'/details').json();assert detail['ingredient_lookup']['source_field']=='主成分略述'
  assert med['dose']=='' and med['frequency']=='一天三餐飯後吃'
  post('/api/patients/reset',{})
  r=post('/api/workflow',{'message':'新病人測試乙，55歲男性。目前服用撲菌特錠400毫克，一天一次'})
  meds=[m for m in r['patient']['medications'] if m['status']=='active'];assert len(meds)==1,meds
  assert set(meds[0]['components'])=={'TRIMETHOPRIM','SULFAMETHOXAZOLE'}
 gc.collect()
print('PASS: brand-only English/Chinese lookup, exact original ingredients/provenance, ambiguous and unknown gaps, no duplicate ingredients or invented dose, existing DDI alert')
