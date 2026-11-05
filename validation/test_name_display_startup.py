from pathlib import Path
import tempfile,os,sys,json,sqlite3
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 from agent_harness import seed,ask
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  asgi.core.model_status=lambda:{'status':'not_checked_in_isolated_test'}
  assert c.get('/api/patients').json()['patients']==[]
  p=seed(c,'王小明',age=68,sex='男')['patient'];assert p['name']=='王小明'
  assert c.get('/api/patients').json()['patients'][0]['name']=='王小明'
  b=c.get('/api/bootstrap').json();assert b['workflow']['patient'] is None and len(b['workflow']['patients'])==1
  assert c.get('/api/patients').json()['patient'] is None
  # After a reload no patient is selected, so a medication-only message cannot land on the previous patient.
  need=ask(c,'目前服用 Aspirin 100mg，一天一次',[('add_medications',{'medications':[{'name':'Aspirin','dose':'100mg','frequency':'一天一次'}]})])
  assert need['patient'] is None and '沒有選擇病人' in need['results'][0]['error'] and not asgi.core.STORE.get(p['id'])['medications']
  p2=seed(c,'王大明',age=55,sex='男')['patient'];assert p2['name']=='王大明' and p2['id']!=p['id']
  c.post('/api/patients/reset',json={})
  same=ask(c,'病人王小明，68歲男性',[('search_patients',{'query':'王小明'}),('select_patient',{'patient_id':p['id']})])
  assert [x['id'] for x in same['results'][0]['patients']]==[p['id']] and same['patient']['id']==p['id']
  with sqlite3.connect(Path(folder)/'records.sqlite3') as db:assert db.execute('SELECT name FROM patients WHERE id=?',(p['id'],)).fetchone()[0]=='王○○'
  db.close()
  html=c.get('/').text;assert '<title>藥你安心：藥事安全Agent</title>' in html
  assert "meta('MedHop Agent')" not in c.get('/workspace.js').text
 result={'all_passed':True,'full_names_in_patient_panel_choices_and_create_response':True,'names_still_encrypted_at_rest':True,'new_page_clears_active_case':True,'medication_only_after_reload_does_not_update_previous_patient':True,'existing_records_remain_searchable':True,'same_mask_different_real_names_stay_separate':True,'system_name':'藥你安心：藥事安全Agent','isolated_test_only':True}
 (ROOT/'validation/name-display-startup-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
