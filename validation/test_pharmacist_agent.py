from pathlib import Path
import os,sys,tempfile,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));(ROOT/'runtime').mkdir(exist_ok=True)
# The model is scripted (agent_harness), so this checks the agent loop and every guard on its tools, not model quality.
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi,pharmacist_agent
 from agent_harness import ask,seed,wait
 model=pharmacist_agent.call_model
 checks=[]
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  patients=lambda:c.get('/api/patients').json()['patients']
  r=ask(c,'新病人，68歲男性，目前吃 Aspirin',[('create_patient',{'name':'王小華','age':68,'sex':'男'})])
  assert '姓名必須是藥師在對話中寫過的原文' in r['results'][0]['error'] and not patients() and r['patient'] is None
  checks.append('a_name_the_pharmacist_did_not_write_is_refused')
  r=ask(c,'先記一下：他每天早晚擦 Hydroquinone',[('create_patient',{'name':'林小美'})]);assert '姓名必須是藥師在對話中寫過的原文' in r['results'][0]['error']
  r=ask(c,'他叫林小美，30歲女性',[('create_patient',{'name':'林小美','age':30,'sex':'女'}),('add_medications',{'medications':[{'name':'Hydroquinone','frequency':'每天早晚'},{'name':'Tretinoin'}]})])
  assert r['patient']['name']=='林小美' and [m['name'] for m in r['patient']['medications']]==['Hydroquinone'] and any('Tretinoin' in e for e in r['results'][1]['errors'])
  r=ask(c,'再加一次',[('add_medications',{'medications':[{'name':'Tretinoin'}]}),('add_medications',{'medications':[{'name':'Tretinoin'}]})])
  assert '停止重試' in r['results'][1]['error'] and '停止重試' not in r['results'][0]['error']
  r=ask(c,'他從來不擦防曬，每天晨跑',[('update_history',{'add_history':['从不擦防晒']}),('update_history',{'add_history':['從來不擦防曬','每天晨跑']})])
  assert '不要改寫' in r['results'][0]['error'] and r['patient']['history']==['從來不擦防曬','每天晨跑']
  c.post('/api/patients/reset',json={})
  checks+=['names_and_drugs_from_earlier_turns_are_accepted','repeated_tool_errors_tell_the_model_to_stop','history_keeps_the_pharmacists_words']
  text='新病人王小明，68歲男性，電話 0912345678。有高血壓，對 Penicillin 過敏。目前吃 Warfarin 5mg 一天一次、Aspirin 100mg 一天一次。'
  r=ask(c,text,[('search_patients',{'query':'王小明'}),('create_patient',{'name':'王小明','age':68,'sex':'男'}),
                ('update_history',{'add_history':['高血壓'],'add_allergies':['Penicillin']}),
                ('add_medications',{'medications':[{'name':'Warfarin','dose':'5mg','frequency':'一天一次'},{'name':'Aspirin','dose':'100mg','frequency':'一天一次'},{'name':'Ibuprofen'}]}),
                ('run_medication_review',{})])
  p=r['patient'];assert p['name']=='王小明' and p['age']==68 and p['history']==['高血壓'] and [m['name'] for m in p['medications']]==['Warfarin','Aspirin']
  assert r['results'][0]['patients']==[] and any('Ibuprofen' in e for e in r['results'][3]['errors'])
  assert [s['title'] for s in r['steps']][:3]==['藥師 Agent 判斷要做的事','查詢病歷','建立病歷'] and r['steps'][0]['result']=='呼叫 5 個工具'
  review=next(a for a in r['actions'] if a['type']=='review')['job_result'];assert review['status']=='done' and review['assessment']['pairs']
  assert r['alerts'] is not None and 'population_checks' in r['alerts']
  checks+=['one_message_creates_patient_history_medications_and_starts_review','drugs_not_in_the_message_are_refused_individually']
  events=[e for e in p['events'] if e['source']=='藥師對話原文'];assert len(events)==1 and '0912345678' not in events[0]['body'] and '王小明' not in events[0]['body']
  raw=b''.join(f.read_bytes() for f in Path(folder).glob('records.sqlite3*'));assert '王小明'.encode() not in raw and b'0912345678' not in raw
  checks.append('original_words_kept_once_with_name_and_phone_redacted')
  c.post('/api/patients/reset',json={})
  r=ask(c,'新病人王小明，30歲男性',[('create_patient',{'name':'王小明','age':30,'sex':'男'})])
  assert 'confirmed_new_person' in r['results'][0]['error'] and [x['id'] for x in r['patient_candidates']]==[p['id']] and len(patients())==2
  checks.append('same_name_shows_candidates_instead_of_merging_or_duplicating')
  c.post('/api/patients/select',json={'id':p['id']});before=asgi.core.STORE.get(p['id'])
  r=ask(c,'他可以吃 Ibuprofen 嗎？',[('check_drug_combination',{'drugs':['Ibuprofen']})])
  after=asgi.core.STORE.get(p['id']);assert after['version']==before['version'] and len(after['medications'])==2 and len(after['reviews'])==len(before['reviews'])
  quick=next(a for a in r['actions'] if a['type']=='quick_check');assert quick['with_patient'] and quick['job_result']['status']=='done' and r['results'][0]['record_changed'] is False
  assert len(r['results'][0]['pairs'])==3 and r['alerts'] is None
  r=ask(c,'Warfarin 和 Aspirin 可以一起吃嗎？',[('check_drug_combination',{'drugs':['Warfarin','Aspirin'],'include_current_patient':False})])
  assert not next(a for a in r['actions'] if a['type']=='quick_check')['with_patient'] and asgi.core.STORE.get(p['id'])['version']==before['version']
  checks.append('combination_questions_check_without_writing_the_record')
  other=seed(c,'測試乙',[('Metformin','500mg','一天兩次')],age=50,sex='女')['patient']
  r=ask(c,'把 Warfarin 停掉',[('set_medication_status',{'medication_id':p['medications'][0]['id'],'status':'stopped'})])
  assert 'error' in r['results'][0] and all(m['status']=='active' for m in asgi.core.STORE.get(p['id'])['medications'])
  r=ask(c,'測試乙的 Metformin 是 500mg 一天兩次',[('add_medications',{'medications':[{'name':'Metformin','dose':'500mg','frequency':'一天兩次'}]})])
  assert 'update_medication' in r['results'][0]['error'] and len(asgi.core.STORE.get(other['id'])['medications'])==1
  checks+=['writes_only_reach_the_current_patient','existing_drug_is_updated_not_added_twice']
  r=ask(c,'剛剛那位呢？',reply='好的')
  assert any(m['role']=='user' and 'Metformin' in m['content'] for m in r['model_messages'][1:-1]) and r['model_messages'][0]['role']=='system' and '測試乙' in r['model_messages'][0]['content']
  checks.append('recent_turns_and_current_patient_reach_the_model')
  r=ask(c,'幫我抓保栓通的仿單',[('fetch_tfda_insert',{'license':'衛署藥輸字第024959號'})])
  assert '藥師提供' in r['results'][0]['error'] and not r['actions'];checks.append('licence_must_come_from_the_pharmacist')
  r=ask(c,'你好',[('drop_database',{}),('answer_without_tools',{'reason':'打招呼'})],reply='你好，請問要處理哪位病人？[P3]')
  assert '沒有 drop_database' in r['results'][0]['error'] and r['reply'].startswith('你好，請問要處理哪位病人？') and '[P3]' not in r['reply']
  checks+=['unknown_tools_are_reported_back','citations_must_match_sources_found_in_this_message']
  pharmacist_agent.call_model=model;asgi.core.MODEL_URL='http://127.0.0.1:9/v1'
  run=wait(c,'/api/agent/'+c.post('/api/agent',json={'message':'新病人王大明'}).json()['id'])
  assert run['status']=='error' and '無法連線' in run['error'] and len(patients())==3
  checks.append('without_a_model_nothing_is_guessed_and_the_error_is_shown')
 result={'all_passed':True,'checks':checks,'isolated_runtime':True,'model':'scripted tool calls; no model server'}
 (ROOT/'validation/pharmacist-agent-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
