from pathlib import Path
import os,sys,tempfile,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));(ROOT/'runtime').mkdir(exist_ok=True)
FIXTURE=(ROOT/'validation/fixtures/tfda-getdrugdoc-synthetic.xml').read_bytes()
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi,drug_knowledge as dk
 from workflow import pair_evidence
 checks=[]
 assert dk.license_code('衛署藥輸字第024959號')==('衛署藥輸字第024959號','02024959') and dk.license_code('衛部藥製字第5812號')[1]=='51005812'
 for bad in ['024959','衛署藥品字第024959號']:
  try:dk.license_code(bad);raise AssertionError(bad)
  except ValueError:pass
 checks.append('license_numbers_map_to_getdrugdoc_codes')
 doc=dk.parse_insert(FIXTURE,'衛署藥輸字第000001號')
 text={s['title']:s['text'] for s in doc['sections']}
 assert doc['version']=='2' and doc['approved']=='2026-01-05' and text['1.1 有效成分及含量']=='Zorbatide 10 mg'
 assert text['7 交互作用']=='Zorbatide與Quelmazole併用時，應避免併用。\n其他&說明' and doc['warning']=='驗證用警語'
 checks.append('insert_xml_decodes_entities_base64_and_stops_at_content')
 asgi.drug_knowledge.fetch_insert=lambda license,deadline=30:{**dk.parse_insert(FIXTURE,license),'source_url':'fixture'}
 def patient(*names):return {'id':'T','version':1,'medications':[{'id':i,'name':n,'name_key':n.casefold(),'status':'active'} for i,n in enumerate(names)]}
 def review(*names):
  retrieved=asgi.core.CORPUS.label_rag.retrieve(patient(*names));return retrieved,pair_evidence(patient(*names),retrieved['pages'])
 from agent_harness import ask
 def staged_by_agent(message,call):
  run=ask(client,message,[call]);docs=[a['document'] for a in run['actions'] if a['type']=='drug_document'];assert len(docs)==1,run;return {'document':docs[0]}
 fetch=('fetch_tfda_insert',{'license':'衛署藥輸字第000001號'})
 with TestClient(asgi.app,base_url="http://127.0.0.1") as client:
  def post(path,body):
   r=client.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  refused=ask(client,'幫我抓 Zorbatide 的仿單',[fetch]);assert '藥師提供' in refused['results'][0]['error'] and not refused['actions']
  staged=staged_by_agent('衛署藥輸字第000001號',fetch)
  assert staged['document']['status']=='staged' and staged['document']['kind']=='tfda'
  checks.append('agent_fetches_only_licences_the_pharmacist_wrote')
  assert not review('Zorbatide','Quelmazole')[0]['sources'] and client.get('/api/drug-knowledge').json()['documents']==[]
  checks.append('fetched_insert_is_staged_and_not_retrieved_before_confirmation')
  saved=post('/api/drug-knowledge/save',{'id':staged['document']['id']})
  retrieved,result=review('Zorbatide','Quelmazole');pair=result['pairs'][0]
  source=next(s for s in result['sources'] if s['id']==pair['source_ids'][0])
  assert pair['status']=='avoid' and source['document']==staged['document']['id'] and source['url'].startswith(dk.PAGE) and '版本 2' in source['origin']
  assert any(s.get('document')==staged['document']['id'] for s in retrieved['sources'])
  assert client.post('/api/drug-knowledge/save',json={'id':staged['document']['id']}).status_code==409
  again=post('/api/drug-knowledge/fetch',{'license':'衛署藥輸字第000001號'});assert post('/api/drug-knowledge/save',{'id':again['document']['id']})['message'].count('取代')==1
  assert [d['id'] for d in client.get('/api/drug-knowledge').json()['documents']]==[again['document']['id']]
  checks.append('confirmed_insert_enters_retrieval_with_tfda_version_and_replaces_older_copy')
  pasted=staged_by_agent('請更新以下藥品資料：\n藥品名稱：Velintra\n適應症\n驗證用\n用法用量\n每日一次\n交互作用\nVelintra與Quelmazole併用時應避免。',('stage_pasted_document',{'drug_name':'Velintra'}))
  assert pasted['document']['kind']=='pasted' and pasted['document']['name']=='Velintra'
  assert client.post('/api/drug-knowledge/save',json={'id':pasted['document']['id'],'name':''}).status_code==409
  post('/api/drug-knowledge/save',{'id':pasted['document']['id'],'name':'Velintra'})
  _,result=review('Velintra','Quelmazole');pair=result['pairs'][0];source=next(s for s in result['sources'] if s['id']==pair['source_ids'][0])
  assert pair['status']=='review' and source['unverified'] and source['url']==''
  checks.append('pasted_text_is_retrievable_but_never_auto_classifies_a_pair')
  skip=staged_by_agent('衛署藥輸字第000001號',fetch);post('/api/drug-knowledge/discard',{'id':skip['document']['id']})
  assert len(client.get('/api/drug-knowledge').json()['documents'])==2
  none=ask(client,'查 Zorbatide 的仿單',[('search_drug_products',{'query':'Zorbatide','show_to_pharmacist':True})]);assert none['results'][0]['products']==[] and not none['actions']
  assert client.post('/api/drug-knowledge/save',json={'id':skip['document']['id']},headers={'Origin':'http://example.invalid'}).status_code==403
  with asgi.core.STORE.connect() as db:assert db.execute("SELECT COUNT(*) FROM audit WHERE event='drug_document_saved'").fetchone()[0]==3
  checks.append('discard_audit_and_cross_origin_rules')
 result={'all_passed':True,'checks':checks,'isolated_runtime':True,'fixture':'synthetic drug names, TFDA GetDrugDoc format','clinical_decisions_automated':False}
 (ROOT/'validation/drug-knowledge-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
