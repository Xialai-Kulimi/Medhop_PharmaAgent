from pathlib import Path
import os,sys,tempfile,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));(ROOT/'runtime').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='1'
 from fastapi.testclient import TestClient
 import asgi
 checks=[]
 with TestClient(asgi.app,base_url="http://127.0.0.1") as client:
  def post(path,body):
   r=client.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  assert client.get('/api/patients').status_code==401 and client.get('/api/bootstrap').status_code==401
  status=client.get('/api/auth/status').json();assert status['account'] is None and status['require_login'] and [p['id'] for p in status['plans']]==['trial','personal','pharmacy','hospital']
  assert client.post('/api/auth/login',json={'username':'lin','password':'wrong'}).status_code==409
  assert client.post('/api/auth/login',json={'username':'demo','password':'x'}).status_code==409
  checks.append('api_requires_sign_in_and_rejects_wrong_password')
  before=client.cookies.get('medlink_session')
  demo=post('/api/auth/demo',{});assert demo['account']['name']=='陳藥師' and demo['subscription']['plan']=='pharmacy'
  assert client.cookies.get('medlink_session')!=before and client.get('/api/patients').status_code==200
  post('/api/auth/logout',{});assert client.get('/api/patients').status_code==401
  checks.append('demo_button_signs_in_with_subscribed_plan_and_rotates_session')
  lin=post('/api/auth/login',{'username':'lin','password':'medhop2026'});assert lin['account']['name']=='林藥師' and lin['subscription'] is None
  assert client.get('/api/patients').status_code==402
  assert client.post('/api/subscription/checkout',json={'plan':'hospital'}).status_code==409
  paid=post('/api/subscription/checkout',{'plan':'personal'})['subscription']
  assert paid['plan']=='personal' and paid['checks_limit']==500 and '未實際扣款' in paid['payment']
  checks.append('unsubscribed_account_goes_through_simulated_checkout')
  from agent_harness import seed
  seed(client,'測試甲',[('Clopidogrel','75mg','一天一次')],age=68,sex='男')
  post('/api/review',{'use_model':False})
  assert client.get('/api/auth/status').json()['subscription']['checks_used']==1
  with asgi.core.STORE.connect() as db:actors={r[0] for r in db.execute('SELECT actor FROM audit')}
  assert any(a.startswith('pharmacist:lin（林藥師') for a in actors)
  checks.append('usage_and_audit_name_the_signed_in_pharmacist')
 result={'all_passed':True,'checks':checks,'isolated_runtime':True,'payment_data_collected':False}
 (ROOT/'validation/account-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
