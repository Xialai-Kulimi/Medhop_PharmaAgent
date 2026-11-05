from pathlib import Path
import os,sys,tempfile,time,gc,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 os.environ['MEDLINK_RUNTIME']=folder
 os.environ['MEDLINK_REQUIRE_LOGIN']='0'
 from fastapi.testclient import TestClient
 import asgi
 with TestClient(asgi.app,base_url='http://127.0.0.1') as c:
  def post(path,body):
   r=c.post(path,json=body);assert r.status_code==200,r.text;return r.json()
  p=post('/api/workflow',{'message':'新病人測試甲，58歲男性。目前服用 Glibenclamide，一天一次'})['patient']
  rag=asgi.core.CORPUS.label_rag.retrieve(p)
  assert rag['sources'] and rag['method']=='BM25 original-label RAG'
  for s in rag['sources']:
   page=next(p for p in asgi.core.CORPUS.pages if p['file']==s['file'] and p['page']==s['page'])
   assert s['text'] in page['text'] and s['retrieval']['score']>0 and s['retrieval']['chunk_offset']>=0
  detail=c.get('/api/medications/'+str(p['medications'][0]['id'])+'/details').json()
  assert detail['pharmacy_rag']['sources'] and detail['pharmacy_rag']['status']=='retrieved'
  post('/api/workflow',{'message':'今天醫師新增 BAKTAR，一天三餐飯後吃'})
  jobid=post('/api/review',{'use_model':False})['id']
  for _ in range(150):
   job=c.get('/api/jobs/'+jobid).json()
   if job['status']!='running':break
   time.sleep(.1)
  assert job['status']=='done',job
  ass=job['assessment'];assert ass['pharmacy_rag']['source_ids'] and ass['pairs'][0]['status']=='interaction'
  assert any('RAG' in step.get('by','') for step in job['steps'])
  assert ass['pharmacy_rag']['model_status']=='not_run'
  assert any(s['id'].startswith('R') for s in job['sources'])
  report=c.get('/api/reports/'+jobid).json();assert 'BM25' in report['pharmacist']
  assert c.get('/api/reports/'+jobid+'/delivery').status_code==409
  unknown={**p,'medications':[{**p['medications'][0],'name':'UnknownDrugZXQ','ingredient_lookup':{}}]}
  gap=asgi.core.CORPUS.label_rag.retrieve(unknown);assert not gap['sources'] and gap['status']=='insufficient'
  # The workflow must use the retriever's pages, not silently scan the full corpus.
  original=asgi.core.CORPUS.label_rag.retrieve
  asgi.core.CORPUS.label_rag.retrieve=lambda p:{'method':'BM25 original-label RAG','index':'test empty retrieval','sources':[],'pages':[],'source_ids':[],'queries':[],'status':'insufficient','generation':'source_bound','model_status':'not_run','scope':'test'}
  post('/api/patients/reset',{})
  post('/api/workflow',{'message':'新病人測試乙，58歲男性。目前服用 Warfarin 5mg，一天一次，Aspirin 100mg，一天一次'})
  rid=post('/api/review',{'use_model':False})['id']
  for _ in range(150):
   empty=c.get('/api/jobs/'+rid).json()
   if empty['status']!='running':break
   time.sleep(.1)
  assert empty['status']=='done' and not any(s['id'].startswith(('P','R')) for s in empty['sources'])
  assert empty['assessment']['pairs'][0]['status']=='insufficient'
  asgi.core.CORPUS.label_rag.retrieve=original
 gc.collect()
result={'all_passed':True,'checks':['real_BM25_original_chunks_and_offsets','verbatim_quote_page_traceability','medication_details_use_RAG','review_retrieval_context_and_existing_label_warning','model_not_run_honestly_reported','pharmacist_report_lists_RAG_sources','unknown_drug_no_borrowed_sources','empty_retrieval_does_not_scan_full_corpus','unapproved_delivery_blocked'],'live_patients_created':0}
(ROOT/'validation/pharmacy-rag-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
