"""Disclosed moda-agent compatibility adapter and research GraphRAG worker.

The upstream missing utils are not reconstructed as purported original code.
Only tool execution records are retained; never chain-of-thought.
"""
from pathlib import Path
import asyncio, importlib.util, json, os, subprocess, tempfile, time

ROOT=Path(__file__).resolve().parent

def graphrag(query,stop=None):
    python=os.environ.get('MEDHOP_PYTHON','')
    root=os.environ.get('MEDHOP_ROOT',str(ROOT/'data'/'medhop-worker'))
    if not python or not root:return {'status':'unavailable','reason':'尚未設定完整 MedHop GraphRAG worker；圖譜路徑檢索不等於完整 GraphRAG 模型推理。'}
    with tempfile.TemporaryDirectory(prefix='medhop-',dir=ROOT/'runtime') as folder:
        output=Path(folder)/'result.json';log=Path(folder)/'worker.log'
        env={**os.environ,'MEDLINK_RESULT_FILE':str(output),'PYTHONIOENCODING':'utf-8','GRAPHRAG_API_KEY':'local-only','MODEL_URL':os.environ.get('MODEL_URL','http://127.0.0.1:13305/api/v1')}
        with log.open('w',encoding='utf-8') as stream:
            proc=subprocess.Popen([python,str(ROOT/'vendor'/'medhop'/'medhop_query.py'),'--root',root,'--method','local',query],cwd=ROOT,stdout=stream,stderr=stream,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+150
            while proc.poll() is None:
                if (stop and stop.is_set()) or time.monotonic()>deadline:
                    proc.terminate();proc.wait(timeout=10)
                    return {'status':'cancelled' if stop and stop.is_set() else 'timeout','reason':'研究推理未完成，轉交藥師；不據此推論無交互作用。'}
                time.sleep(.2)
        if proc.returncode or not output.exists():
            diagnostic=log.read_text(encoding='utf-8',errors='replace').strip().splitlines()
            return {'status':'error','reason':'MedHop GraphRAG worker 未產生完整結果，保留原文檢索供人工確認。','worker_exit_code':proc.returncode,'result_file_created':output.exists(),'diagnostic':diagnostic[-1][:700] if diagnostic else 'worker returned no result file'}
        result=json.loads(output.read_text(encoding='utf-8'))
        return {'status':'completed','kind':'research_only','engine':'original MedHop GraphRAG local search','answer':result.get('answer',''),'sources':result.get('sources',[]),'scope':result.get('scope',{})}

async def run_agents(patient,assessment,corpus,model_url,model,emit,stop):
    """Router delegates to original moda AssistantAgent with read-only tools.

    Authoritative outputs remain source-bound deterministic assessment; model
    summaries do not grant delivery or modify patient medication records.
    """
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_agentchat.agents import AssistantAgent
    from autogen_core import CancellationToken
    spec=importlib.util.spec_from_file_location('original_moda_agent',ROOT/'upstream'/'moda-agent'/'agent.py')
    upstream=importlib.util.module_from_spec(spec);spec.loader.exec_module(upstream)
    client=OpenAIChatCompletionClient(model=model,base_url=model_url,api_key=os.environ.get('MODEL_API_KEY','local-only'),model_info={'vision':False,'function_calling':True,'json_output':False,'family':'unknown','structured_output':False},temperature=0,max_tokens=650,timeout=60)
    trace=[]; token=CancellationToken();finished=asyncio.Event()
    def event(tool,result):
        trace.append({'tool':tool,'result':result});emit(tool,result)
    async def read_patient_medications()->str:
        """Read current deidentified medication list, recorded doses and missing fields."""
        event('read_patient_medications',str(len(assessment['medications']))+' 項用藥')
        return json.dumps({'patient_context':{k:patient.get(k) for k in (('age','sex','history') if patient.get('sex')=='男' else ('age','sex','pregnancy','pregnancy_weeks','lactation','history'))},'medications':[{k:m.get(k) for k in ('name','dose','frequency','kind','acquisition','class')} for m in assessment['medications']]},ensure_ascii=False)
    async def retrieve_pharmacy_evidence()->str:
        """Retrieve original drug-label pair-specific quotations and pending items."""
        event('retrieve_pharmacy_evidence',str(len(assessment['pairs']))+' 組來源核對')
        return json.dumps({'pharmacy_rag':assessment.get('pharmacy_rag',{}),'retrieved_original_context':[{'id':s['id'],'text':s['text'],'file':s['file'],'page':s['page']} for s in assessment['sources'] if s['id'].startswith('R')][:6],'pairs':[{k:p.get(k) for k in ('id','drugs','status','quote','source_ids')} for p in assessment['pairs']],'limitations':'來源不足必須轉交藥師；不可自行補充劑量、替代藥或推論安全。'},ensure_ascii=False)
    async def retrieve_medhop_mechanisms()->str:
        """Read the source-supported MedHop results for these exact medication pairs."""
        rows=[{'id':p['id'],'drugs':p['drugs'],'research':p.get('medhop',{})} for p in assessment['pairs']]
        supported=[p for p in assessment['pairs'] if p.get('medhop',{}).get('status')=='mechanism_candidate']
        event('retrieve_medhop_mechanisms',f'{len(rows)} 組配對；{len(supported)} 組有待核對的研究路徑')
        if supported:
            left,right=[i['entity'] for i in supported[0]['medhop']['identities']]
            research=await asyncio.to_thread(graphrag,left+' Candidates: '+right,stop)
            research['pair_id']=supported[0]['id']
            research['scope_note']='進階 GraphRAG 僅針對第一組有逐邊支持原文的配對；其他配對保留完整結構化檢索結果。'
        else:research={'status':'not_run_no_supported_pair','reason':'沒有已精確對應且逐邊具原文支持的配對，未以模型猜測機轉；請核對結構化配對結果。'}
        assessment['medhop_research']=research
        ids={sid for p in assessment['pairs'] for sid in p.get('medhop',{}).get('source_ids',[])}
        evidence=[];budget=6500;omitted=0
        for item in assessment['sources']:
            if item['id'] not in ids:continue
            text=item.get('full_text',item['text'])
            if len(text)>budget:omitted+=1;continue
            evidence.append({'id':item['id'],'text':text});budget-=len(text)
        research_summary={k:research[k] for k in ('status','answer','reason','pair_id','scope_note') if k in research}
        payload={'pairs':rows,'evidence':evidence,'omitted_whole_source_units':omitted,'research':research_summary,'clinical_proof':False,'all_results_in_saved_assessment':True}
        encoded=json.dumps(payload,ensure_ascii=False,default=str)
        if len(encoded)>14000:
            # Preserve valid JSON and disclose summarisation; do not truncate a
            # quoted source midway and accidentally remove a negative qualifier.
            payload={'pairs':[{'id':p['id'],'drugs':p['drugs'],'status':p.get('medhop',{}).get('status'),'source_ids':p.get('medhop',{}).get('source_ids',[])} for p in assessment['pairs']],'evidence':evidence,'omitted_whole_source_units':omitted,'clinical_proof':False,'detail_note':'完整研究原文與路徑保留於檢查結果，工具此處回傳狀態摘要與預算內的完整文字單元。'}
        return json.dumps(payload,ensure_ascii=False,default=str)
    specialist=upstream.Agent(client).build_agent(
        system_prompt='你是藥事來源核對代理人。先呼叫 read_patient_medications，再呼叫 retrieve_pharmacy_evidence，並必須呼叫 retrieve_medhop_mechanisms 讀取同組藥物的研究結果。只能使用工具原文；不得自行新增醫學知識、劑量或替代藥。遇風險或來源不足，停止自動用藥判定並轉交藥師。所有輸出標示待藥師確認。不得輸出思考過程。',
        memory=[],tools=[read_patient_medications,retrieve_pharmacy_evidence,retrieve_medhop_mechanisms],reflect_on_tool_use=False)
    async def consult_pharmacy_agent()->str:
        """Delegate this medication-safety task to the source-grounded moda pharmacy agent."""
        event('consult_pharmacy_agent','已委派原 moda-agent AssistantAgent')
        await specialist.run(task='核對已提供的多重用藥，透過工具讀取病歷及仿單，必要時檢索 MedHop。只回傳待藥師確認事項。 /no_think',cancellation_token=token)
        # AutoGen 0.6.1 executes one tool batch per run. Continue only missing
        # read-only stages; the model must actually call each advertised tool.
        for required in ('read_patient_medications','retrieve_pharmacy_evidence','retrieve_medhop_mechanisms'):
            if not any(t['tool']==required for t in trace):
                await specialist.run(task='尚未取得 '+required+' 的工具結果，請呼叫此工具，無資料即轉交藥師。 /no_think',cancellation_token=token)
        # Specialist model prose is not admitted to the clinical report.
        return json.dumps({'status':'handoff','executed_tools':[r['tool'] for r in trace],'label':'待藥師確認','medical_recommendations_accepted':False},ensure_ascii=False)
    router=AssistantAgent('pharmacist_router',client,tools=[consult_pharmacy_agent],system_message='你是統一藥師入口。將藥單安全核對委派給 consult_pharmacy_agent。只能使用這個工具；不可直接給醫療建議。待藥師確認。',reflect_on_tool_use=False)
    async def cancellation_watcher():
        while not finished.is_set():
            if stop.is_set():token.cancel();return
            await asyncio.sleep(.2)
    watcher=asyncio.create_task(cancellation_watcher())
    try:
        await asyncio.wait_for(router.run(task='請委派藥事代理人處理目前藥單的來源核對。 /no_think',cancellation_token=token),timeout=120)
        delegated=any(t['tool']=='consult_pharmacy_agent' for t in trace)
        required={'read_patient_medications','retrieve_pharmacy_evidence','retrieve_medhop_mechanisms'}
        complete=delegated and required<={t['tool'] for t in trace}
        return {'status':'completed' if complete else 'incomplete_tool_routing','trace':trace,'upstream':'upstream/moda-agent/agent.py','compatibility_prompts':True,'original_28_checks':False}
    finally:
        finished.set();watcher.cancel();await client.close()
