"""Python 3.14 asynchronous Uvicorn API with Pydantic request validation."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import asyncio, hashlib, json, os, re, secrets, sqlite3, threading
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
import server as core
from governance import PrivateStore, Catalog, ReviewGate, drafts, redact, medication_checks
from agent_service import run_agents, graphrag
from pair_integration import PairIntegration
from population_checks import PopulationChecks
from clinical_advice import ClinicalAdvice
import record_tools
import drug_knowledge
import account
# login-20261105: every /api/ call needs a signed-in pharmacist with a plan; validation scripts set MEDLINK_REQUIRE_LOGIN=0.
REQUIRE_LOGIN=os.environ.get('MEDLINK_REQUIRE_LOGIN','1')!='0'
OPEN_PATHS=('/api/auth/','/api/subscription/')

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Message(Input):
    message:str=Field(min_length=1,max_length=12000)
    example_id:str|None=None
class PatientProfile(Input):
    name:str=Field(default='',max_length=80,pattern=r'^[^\r\n]*$')
    age:int|None=Field(default=None,ge=0,le=129)
    sex:Literal['男','女','其他','未確認']='未確認'
    pregnancy:Literal['unknown','pregnant','not_pregnant','not_applicable']='unknown'
    pregnancy_weeks:int|None=Field(default=None,ge=0,le=45)
    lactation:Literal['unknown','breastfeeding','not_breastfeeding','not_applicable']='unknown'
    height_cm:float|None=Field(default=None,gt=0,le=300)
    weight_kg:float|None=Field(default=None,gt=0,le=600)
    history:str=Field(default='',max_length=3000)
    allergies:str=Field(default='',max_length=3000)
class Select(Input):id:str=Field(min_length=1,max_length=80)
class Review(Input):use_model:bool=True
class Disposition(Input):
    review:str
    action:Literal['contact_prescriber','adjusted','reviewed']
    note:str=Field(min_length=4,max_length=5000)
class Query(Input):
    question:str=Field(min_length=1,max_length=12000)
    mode:Literal['integrated','medhop','pharmacy']='integrated'
    role:Literal['pharmacist','doctor']='pharmacist'
    use_model:bool=True
class ReportEdit(Input):
    review:str
    pharmacist:str=Field(min_length=4,max_length=16000)
    patient:str=Field(min_length=4,max_length=16000)
class Approval(Input):
    review:str
    actor:str=Field(min_length=2,max_length=80)
    resolutions:dict[str,str]=Field(default_factory=dict)
    overall_resolution:str=Field(default="",max_length=8000)
    reviewed_reason_ids:list[str]=Field(default_factory=list)
    acknowledged:bool
class Medication(Input):
    id:int
    acquisition:Literal['prescribed','self_purchased','unknown']
    classification:Literal['rx','otc','unknown']
    license:str=Field(default='',max_length=100)
    note:str=Field(min_length=4,max_length=1000)
class ConfirmedChange(Input):
    review:str
    text:str=Field(min_length=4,max_length=4000)
    note:str=Field(min_length=4,max_length=2000)
    acknowledged:bool
    old_med_id:int|None=None
    replacement_name:str=Field(default='',max_length=100)
    replacement_dose:str=Field(default='',max_length=60)
    replacement_frequency:str=Field(default='',max_length=160)
class MedicationIdentity(Input):
    id:int
    entity:str=Field(default='',max_length=100)
    source:str=Field(default='',max_length=1000)
    note:str=Field(min_length=4,max_length=1000)
class Record(Input):
    text:str=Field(min_length=1,max_length=12000)
    source:str=Field(default='使用者輸入',max_length=1000)
class Note(Input):job:str
# record-tools-20261103: the exchange file is lenient about extra keys so other systems can add their own.
class FileItem(BaseModel):
    model_config=ConfigDict(extra='ignore')
class ExchangePatient(FileItem):
    id:str=Field(default='',max_length=80)
    name:str=Field(default='',max_length=80,pattern=r'^[^\r\n]*$')
    age:int|None=Field(default=None,ge=0,le=129)
    sex:Literal['男','女','其他','未確認','']=''
    pregnancy:Literal['unknown','pregnant','not_pregnant','not_applicable']='unknown'
    pregnancy_weeks:int|None=Field(default=None,ge=0,le=45)
    lactation:Literal['unknown','breastfeeding','not_breastfeeding','not_applicable']='unknown'
    height_cm:float|None=Field(default=None,gt=0,le=300)
    weight_kg:float|None=Field(default=None,gt=0,le=600)
    created:str=Field(default='',max_length=40)
class ExchangeMedication(FileItem):
    name:str=Field(min_length=1,max_length=100,pattern=r'^[^\r\n]*$')
    dose:str=Field(default='',max_length=60,pattern=r'^[^\r\n]*$')
    frequency:str=Field(default='',max_length=160,pattern=r'^[^\r\n]*$')
    kind:Literal['current','new_order','reported']='current'
    status:Literal['active','stopped']='active'
    confirmation:Literal['pending','confirmed']='pending'
    acquisition:Literal['prescribed','self_purchased','unknown']='unknown'
    classification:Literal['rx','otc','unknown']='unknown'
    license:str=Field(default='',max_length=100)
    text:str=Field(default='',max_length=4000)
    created:str=Field(default='',max_length=40)
class ExchangeEvent(FileItem):
    source:str=Field(default='',max_length=1000)
    body:str=Field(min_length=1,max_length=12000)
    created:str=Field(default='',max_length=40)
class ExchangeIntervention(FileItem):
    action:Literal['contact_prescriber','adjusted','reviewed']='reviewed'
    note:str=Field(min_length=1,max_length=5000)
    created:str=Field(default='',max_length=40)
class ExchangeRecord(FileItem):
    format:Literal['medhop-patient-record']
    format_version:Literal[1]
    exported_at:str=Field(default='',max_length=40)
    source_system:str=Field(default='',max_length=200)
    patient:ExchangePatient
    history:list[str]|None=Field(default=None,max_length=100)
    allergies:str|None=Field(default=None,max_length=3000)
    medications:list[ExchangeMedication]=Field(default_factory=list,max_length=200)
    events:list[ExchangeEvent]=Field(default_factory=list,max_length=500)
    interventions:list[ExchangeIntervention]=Field(default_factory=list,max_length=200)
class PatientImport(Input):
    file_name:str=Field(default='',max_length=200)
    record:ExchangeRecord
class PatientEdit(Input):
    name:str|None=Field(default=None,min_length=1,max_length=80,pattern=r'^[^\r\n]*$')
    age:int|None=Field(default=None,ge=0,le=129)
    sex:Literal['男','女','其他','未確認']|None=None
    pregnancy:Literal['unknown','pregnant','not_pregnant','not_applicable']|None=None
    pregnancy_weeks:int|None=Field(default=None,ge=0,le=45)
    lactation:Literal['unknown','breastfeeding','not_breastfeeding','not_applicable']|None=None
    height_cm:float|None=Field(default=None,gt=0,le=300)
    weight_kg:float|None=Field(default=None,gt=0,le=600)
    history:list[str]|None=Field(default=None,max_length=50)
    allergies:str|None=Field(default=None,max_length=3000)
class MedicationSave(Input):
    id:int|None=None
    name:str=Field(min_length=1,max_length=100,pattern=r'^[^\r\n]*$')
    dose:str=Field(default='',max_length=60,pattern=r'^[^\r\n]*$')
    frequency:str=Field(default='',max_length=160,pattern=r'^[^\r\n]*$')
    kind:Literal['current','new_order','reported']='current'
# drug-knowledge-20261103: pharmacist-confirmed drug documents.
class DrugMessage(Input):
    message:str=Field(min_length=1,max_length=45000)
    use_model:bool=True
class Login(Input):
    username:str=Field(min_length=1,max_length=40)
    password:str=Field(min_length=1,max_length=200)
class Checkout(Input):plan:str=Field(min_length=1,max_length=20)
class NoBody(Input):pass
class DrugLicense(Input):license:str=Field(min_length=6,max_length=40)
class DrugDocument(Input):
    id:str=Field(pattern=r'^D-[0-9a-f]{10}$')
    name:str|None=Field(default=None,max_length=80,pattern=r'^[^\r\n]*$')
class MedicationStatus(Input):
    id:int
    status:Literal['active','stopped','removed']
    note:str=Field(default='',max_length=500)

class GovernedReview(core.ReviewJob):
    def snapshot(self):
        result=super().snapshot()
        result['research']=self.assessment.get('medhop_research') if self.assessment else None
        return result
    def gather_research(self,result):
        indexed=app.state.label_interactions.apply(self.patient,result)
        self.sources=result['sources']
        if indexed['alerts']:
            self.use_model=False
            self.step('核對藥品併用與仿單警語','仿單來源',result='；'.join(a['message'] for a in indexed['alerts']))
        query=' '.join(m['name'] for m in result['medications'])
        self.coverage=core.CORPUS.coverage(query)
        self.step('以相同藥物配對準備 MedHop 檢索','MedHop',result='研究證據將依每組藥物核對，不使用其他藥物的路徑代替')
    def finish_assessment(self):
        if self.stop.is_set():return
        self.step('整合每組藥物的仿單與研究證據','藥單 × MedHop','running')
        self.assessment['sources']=self.sources
        app.state.integration.join(self.patient,self.assessment,self.stop)
        joined_paths=[];joined_edges={};joined_matches=set()
        for pair in self.assessment['pairs']:
            research=pair.get('medhop',{})
            joined_matches.update(i['entity'] for i in research.get('identities',[]) if i.get('entity'))
            for path in research.get('paths',[]):
                edges=[{'id':e['id'],'source':e['source'],'target':e['target'],'description':e['description'],'citations':e['source_ids']} for e in path['edges']]
                joined_paths.append({'nodes':path['nodes'],'edges':edges,'pair_id':pair['id']})
                for edge in edges:joined_edges[edge['id']]=edge
        with self.lock:self.paths,self.edges,self.matches=joined_paths,list(joined_edges.values()),sorted(joined_matches)
        if self.stop.is_set():return
        with self.lock:self.steps[-1].update(state='done',result=f"{len(self.assessment['pairs'])} 組藥物，分開保留仿單判讀與研究機轉")
        population=app.state.population.evaluate(self.patient)
        self.assessment['population_checks']=population['checks']
        self.sources.extend(population['sources'])
        self.step('核對病人條件與單藥禁忌','原始仿單',result=str(len(population['alerts']))+' 項來源警示')
        advice=app.state.clinical_advice.evaluate(self.patient,self.assessment)
        self.assessment['clinical_advice']=advice
        self.assessment['population_checks'].extend(advice['additional_population_checks'])
        population['alerts'].extend(advice['additional_population_checks'])
        self.assessment.setdefault('interaction_alerts',[]).extend(advice['interaction_alerts'])
        known={source['id'] for source in self.sources}
        self.sources.extend(source for source in advice['sources'] if source['id'] not in known)
        self.assessment['sources']=self.sources
        self.step('整理具體替代候選與個別化衛教','原仿單＋新增官方指引',result=str(len(advice['dispensing_suggestions']))+' 組候選；未自動更改用藥')
        from safety_alerts import apply_safety
        apply_safety(core.ROOT,core.CORPUS.pages,self.patient,self.assessment)
        self.assessment['medication_checks']=medication_checks(self.patient,app.state.catalog)
        self.assessment['agent']={'status':'disabled','original_28_checks':False}
        if self.use_model:
            agent_step=len(self.steps)
            self.step('藥師入口委派來源核對工具','AutoGen','running')
            try:
                def emit(tool,result):self.step(tool,'AutoGen 工具呼叫',result=result)
                with core.MODEL_LOCK:
                    result=asyncio.run(run_agents(self.patient,self.assessment,core.CORPUS,core.MODEL_URL,core.MODEL,emit,self.stop))
                self.assessment['agent']=result
                self.step('AutoGen 委派結果','本機',result='已執行工具' if result['status']=='completed' else '工具路由未完整執行，人工覆核')
            except Exception as exc:
                self.assessment['agent']={'status':'unavailable','reason':type(exc).__name__,'original_28_checks':False}
                self.step('AutoGen 未完成','本機',result='保留原文檢查，轉交藥師')
            finally:
                with self.lock:self.steps[agent_step].update(state='done',result='工具紀錄如下；待藥師確認')
        self.assessment['reports']=drafts(self.patient,self.assessment)
        self.assessment['workflow_state']=self.assessment['reports']['state']
        super().finish_assessment()
        alerts=population['alerts']+self.assessment.get('interaction_alerts',[])
        if alerts:
            self.assessment['overall']='contact_prescriber'
            self.answer='\n'.join(alert['message'] for alert in alerts)+'\n已附原始仿單引用；待藥師確認，不自動更換藥物或劑量。'
            core.STORE.save_review(self.id,self.patient,{'assessment':self.assessment,'answer':self.answer})
        core.STORE.audit(self.patient['id'],'review_completed',self.session,{'review':self.id,'version':self.patient['version'],'state':self.assessment['workflow_state'],'agent':self.assessment['agent']['status']})

class ResearchJob(core.Job):
    def run(self):
        super().run()
        patient=getattr(self,'case_patient',None)
        if patient and self.status=='done':
            result={'question':self.question,'answer':'待藥師確認\n'+self.answer,'sources':self.sources,'paths':self.paths,'research':getattr(self,'research',None)}
            with core.STORE.connect() as db:db.execute('INSERT OR REPLACE INTO patient_queries VALUES(?,?,?,?,?)',(self.id,patient['id'],patient['version'],json.dumps(result,ensure_ascii=False,default=str),core.now()))
            core.STORE.audit(patient['id'],'research_query_saved',self.session,{'query':self.id,'version':patient['version'],'mode':self.mode})
    def synthesize(self):
        if self.mode=='medhop':
            self.step('MedHop GraphRAG 研究推理','GraphRAG','running')
            while not core.MODEL_LOCK.acquire(timeout=.25):
                if self.stop.is_set():return
            try:result=graphrag(self.question,self.stop)
            finally:core.MODEL_LOCK.release()
            if result['status']=='completed':
                self.answer='待藥師確認｜MedHop 研究機轉，非臨床用藥判定\n\n'+result['answer']
                # GraphRAG uses its native [Data: Table (id)] citations.
                self.research=result;self.citation_status='research_requires_review'
                self.steps[-1].update(state='done',result='完整 GraphRAG 已執行；原文與原生來源 ID 隨附')
                return
            self.research=result
            self.steps[-1].update(state='done',result=result.get('reason','研究推理未完成'))
        super().synthesize()
    def snapshot(self):
        result=super().snapshot();result['research']=getattr(self,'research',None);return result

@asynccontextmanager
async def lifespan(app):
    core.MODEL_URL=os.environ.get('MODEL_URL',core.MODEL_URL)
    core.MODEL=os.environ.get('MODEL',core.MODEL)
    core.init_db();core.STORE=PrivateStore(core.DB)
    from workflow import merge_compatible_medications,repair_medication_attributes,stamp
    from label_interactions import repair_compounds,LabelInteractions
    with core.STORE.connect() as db:
        for row in db.execute('SELECT id FROM patients').fetchall():
            repaired=repair_compounds(db,row[0])
            repaired=repair_medication_attributes(db,row[0]) or repaired
            if merge_compatible_medications(db,row[0]) or repaired:db.execute('UPDATE patients SET version=version+1,updated=? WHERE id=?',(stamp(),row[0]))
    core.CORPUS=await asyncio.to_thread(core.Corpus)
    app.state.catalog=await asyncio.to_thread(Catalog,core.ROOT)
    from ingredient_lookup import IngredientLookup
    core.STORE.ingredient_lookup=IngredientLookup(app.state.catalog)
    app.state.gate=ReviewGate(core.STORE)
    app.state.integration=PairIntegration(core.CORPUS)
    app.state.population=PopulationChecks(core.ROOT,core.CORPUS.pages)
    app.state.clinical_advice=ClinicalAdvice(core.ROOT,core.CORPUS.pages)
    app.state.label_interactions=LabelInteractions(core.ROOT,core.CORPUS.pages)
    app.state.drug_library=drug_knowledge.DrugLibrary(core.STORE)
    app.state.accounts=account.Accounts(core.STORE);core.STORE.actor_name=app.state.accounts.actor
    drug_knowledge.attach(core.CORPUS,app.state.drug_library.saved())
    yield
    for job in list(core.JOBS.values()):job.stop.set()

app=FastAPI(title='藥你安心：藥事安全Agent',version='2.0.0',lifespan=lifespan)

@app.middleware('http')
async def local_session(request:Request,call_next):
    if request.headers.get('host','').split(':')[0] not in {'127.0.0.1','localhost','[::1]'}:
        return JSONResponse({'error':'僅允許本機主機名稱'},status_code=403)
    if request.method not in {'GET','HEAD','OPTIONS'}:
        if request.headers.get('origin') and request.headers['origin']!=str(request.base_url).rstrip('/'):
            return JSONResponse({'error':'拒絕跨來源寫入'},status_code=403)
        if request.headers.get('content-type','').split(';')[0]!='application/json':
            return JSONResponse({'error':'需要 JSON 格式'},status_code=415)
        if int(request.headers.get('content-length','0') or '0')>150000:
            return JSONResponse({'error':'資料超過大小上限'},status_code=413)
    sid=request.cookies.get('medlink_session','')
    request.state.sid=sid if re.fullmatch(r'[A-Za-z0-9_-]{32}',sid) else secrets.token_urlsafe(24)
    path=request.url.path;accounts=getattr(app.state,'accounts',None)
    username=accounts.username(request.state.sid) if REQUIRE_LOGIN and accounts and path.startswith('/api/') and not path.startswith(OPEN_PATHS) else ''
    if username is None:response=JSONResponse({'error':'請先登入'},status_code=401)
    elif username and not accounts.subscribed(username):response=JSONResponse({'error':'請先選擇方案'},status_code=402)
    else:response=await call_next(request)
    response.set_cookie('medlink_session',request.state.sid,httponly=True,samesite='strict')
    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
    return response

@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'error':str(exc)},status_code=409)

@app.get('/api/auth/status')
async def auth_status(request:Request):return {**app.state.accounts.status(request.state.sid),'require_login':REQUIRE_LOGIN}
def new_session(request):
    # A new session id at sign-in; the old browser session keeps no access.
    app.state.accounts.logout(request.state.sid);request.state.sid=secrets.token_urlsafe(24)
@app.post('/api/auth/login')
async def auth_login(body:Login,request:Request):
    new_session(request);app.state.accounts.login(request.state.sid,body.username.strip(),body.password)
    return app.state.accounts.status(request.state.sid)
@app.post('/api/auth/demo')
async def auth_demo(body:NoBody,request:Request):
    new_session(request);app.state.accounts.demo(request.state.sid)
    return app.state.accounts.status(request.state.sid)
@app.post('/api/auth/logout')
async def auth_logout(body:NoBody,request:Request):
    app.state.accounts.logout(request.state.sid);core.STORE.reset(request.state.sid)
    return app.state.accounts.status(request.state.sid)
@app.post('/api/subscription/checkout')
async def subscription_checkout(body:Checkout,request:Request):
    app.state.accounts.checkout(request.state.sid,body.plan)
    status=app.state.accounts.status(request.state.sid)
    core.STORE.audit(None,'subscription_changed',request.state.sid,{'plan':body.plan,'payment':'simulated'})
    return status

@app.get('/api/bootstrap')
async def bootstrap(request:Request):
    # A fresh page starts with no selected case; existing records remain searchable.
    core.STORE.reset(request.state.sid)
    return {'stats':core.CORPUS.stats(),'model':await asyncio.to_thread(core.model_status),'examples':core.CORPUS.examples,'record':core.read_record(request.state.sid),'repositories':core.CORPUS.manifest['repositories'],'extraction':core.CORPUS.manifest['extraction'],'workflow':{'patient':core.STORE.active(request.state.sid),'patients':core.STORE.list()},'government':app.state.catalog.meta,'limitations':['所有系統輸出待藥師確認；資料不足、高風險及無法判定即轉交藥師。','單藥病人條件警示只涵蓋 data/population_rules.json 已核對的原仿單規則；未命中不代表適合孕婦或其他族群。','原 moda-agent 缺少 utils；本版重用原 Agent 類別，搭配另行揭露的相容提示與唯讀工具，不代表原 28 項檢核全數完成。','MedHop 為 PubMed 研究資料；圖譜路徑與完整 GraphRAG 模型推理分別標示，均不等於病人臨床證據。','政府資料僅納入未註銷且未逾期產品；成分候選不能確認病人實際產品或法律分類。','本機完整姓名顯示與加密身分索引不取代藥局帳號權限、全碟加密與個資治理。'],'runtime':{'python':__import__('platform').python_version(),'api':'Uvicorn / FastAPI / Pydantic','hardware_verified':False}}

@app.get('/api/patients')
async def patients(request:Request,search:str=''):return {'patients':core.STORE.list(search[:80]),'patient':core.STORE.active(request.state.sid)}
def with_population(result):
    patient=result.get('patient')
    if not patient or result.get('action') in {'ambiguous','identity_conflict','need_identity'}:return result
    if result.get('steps') and result['steps'][0].get('title')=='辨識病人資料' and result.get('action') in {'created','found'}:result['steps'][0]['result']=patient.get('name') or patient['id']
    population=app.state.population.evaluate(patient)
    indexed=app.state.label_interactions.evaluate(patient)
    result['interaction_alerts']=indexed['alerts'];result['interaction_alert_sources']=indexed['sources']
    result['population_checks']=population['checks'];result['patient_alerts']=population['alerts'];result['patient_alert_sources']=population['sources']
    from safety_alerts import apply_safety
    safety={'population_checks':population['checks'],'interaction_alerts':indexed['alerts'],
            'sources':list(population['sources'])+list(indexed['sources'])}
    apply_safety(core.ROOT,core.CORPUS.pages,patient,safety)
    result['safety_alerts']=safety['safety_alerts']
    result['safety_alert_sources']=safety['sources']
    return result
@app.post('/api/workflow')
async def workflow(body:Message,request:Request):
    source='藥師輸入'
    example=next((e for e in core.CORPUS.examples if e['id']==body.example_id and e['text']==body.message),None)
    if example:source=example['source']
    result=await asyncio.to_thread(core.STORE.message,request.state.sid,body.message,source)
    return with_population(result)
@app.post('/api/patients/profile')
async def patient_profile(body:PatientProfile,request:Request):
    if body.pregnancy!='pregnant' and body.pregnancy_weeks is not None:raise ValueError('只有確認懷孕時才能填寫孕週')
    p=core.STORE.active(request.state.sid)
    if not p:
        if not body.name.strip():raise ValueError('初次建檔請填寫病人姓名，病歷號由系統產生')
        result=await asyncio.to_thread(core.STORE.message,request.state.sid,'姓名：'+body.name.strip()+(f'\n年齡：{body.age}歲' if body.age is not None else '')+(f'\n性別：{body.sex}' if body.sex in {'男','女'} else ''))
        if result['action'] in {'found','ambiguous','identity_conflict','need_identity'}:return result
    else:result={'action':'updated','steps':[],'message':'已更新病人基本資料；請依目前資料重新檢查。'}
    values=body.model_dump(exclude={'name','history','allergies'})
    values={k:v for k,v in values.items() if k not in {'height_cm','weight_kg'} or k in body.model_fields_set}
    p=await asyncio.to_thread(core.STORE.update_profile,request.state.sid,values)
    clinical='\n'.join(label+'：'+value.strip() for label,value in [('病史',body.history),('過敏史',body.allergies)] if value.strip())
    if clinical:
        with core.STORE.connect() as db:
            db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(p['id'],redact(clinical),'藥師填寫病史與過敏史',core.now()))
            db.execute('UPDATE patients SET version=version+1,updated=? WHERE id=?',(core.now(),p['id']))
        p=core.STORE.get(p['id'])
    core.STORE.audit(p['id'],'patient_profile_updated',request.state.sid,{'version':p['version']})
    return {**result,'patient':p,'review_needed':False}
@app.post('/api/patients/select')
async def select(body:Select,request:Request):
    patient=core.STORE.select(request.state.sid,body.id)
    reviews=patient.get('reviews',[])
    selected=next((review for review in reviews if review['version']==patient['version']),reviews[0] if reviews else None)
    review=core.STORE.review(selected['id']) if selected else None
    query=None
    with core.STORE.connect() as db:
        row=db.execute('SELECT * FROM patient_queries WHERE patient_id=? ORDER BY (version=?) DESC,created DESC,rowid DESC LIMIT 1',(patient['id'],patient['version'])).fetchone()
        if row:query={**dict(row),'result':json.loads(row['result'])}
    result={'action':'selected','patient':patient,'changed':False,'review_needed':not review and not query and any(m['status']=='active' for m in patient['medications']),'steps':[],'message':'已載入 '+(patient.get('name') or patient['id'])+' 的病歷。','selected_review':review,'selected_query':query}
    if review:result['selected_review']['is_current']=review['version']==patient['version']
    if query:result['selected_query']['is_current']=query['version']==patient['version']
    return with_population(result)
@app.post('/api/patients/reset')
async def reset(request:Request):core.STORE.reset(request.state.sid);return {'patient':None}

# record-tools-20261103: JSON exchange and pharmacist edits. Every edit bumps the record version.
def ensure_idle(sid):
    if any(j.session==sid and j.status=='running' for j in core.JOBS.values()):raise ValueError('請先停止目前檢查')
def edited(sid,patient,event,detail,message):
    core.STORE.audit(patient['id'],event,sid,{**detail,'version':patient['version']})
    return {'patient':patient,'message':message}
@app.get('/api/patients/{pid}/export')
async def export_patient(pid:str,request:Request):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',pid):raise ValueError('病歷號格式不正確')
    record=await asyncio.to_thread(record_tools.export_record,core.STORE,pid)
    core.STORE.audit(pid,'patient_exported',request.state.sid,{'version':record['patient']['version']})
    return JSONResponse(record,headers={'Content-Disposition':f'attachment; filename="{pid}.json"'})
@app.post('/api/patients/import')
async def import_patient(body:PatientImport,request:Request):
    ensure_idle(request.state.sid)
    record=body.record.model_dump()
    dropped=[m['name'] for m in record['medications'] if m['license'] and not app.state.catalog.current_product(m['license'])]
    for med in record['medications']:
        if med['name'] in dropped:med['license']=''
    patient=await asyncio.to_thread(record_tools.import_record,core.STORE,request.state.sid,record,body.file_name)
    message='已將 '+(body.file_name or '匯入檔案')+' 匯入為新病歷 '+patient['id']+(f"（原病歷號 {record['patient']['id']}）" if record['patient']['id'] else '')+'，未覆寫任何既有病歷。'+('以下藥品的許可證不在目前有效的官方快照中，已改為待核對：'+'、'.join(dropped) if dropped else '')
    return edited(request.state.sid,patient,'patient_imported',{'file':body.file_name,'source_patient':record['patient']['id']},message)
@app.post('/api/patients/edit')
async def edit_patient(body:PatientEdit,request:Request):
    ensure_idle(request.state.sid)
    patient,changed=await asyncio.to_thread(record_tools.edit_patient,core.STORE,request.state.sid,{k:getattr(body,k) for k in body.model_fields_set})
    if not changed:return {'patient':patient,'message':''}
    return edited(request.state.sid,patient,'patient_record_edited',{'fields':changed},'已更新'+'、'.join(changed)+'。')
@app.post('/api/medications/save')
async def save_medication(body:MedicationSave,request:Request):
    ensure_idle(request.state.sid)
    patient,message=await asyncio.to_thread(record_tools.save_medication,core.STORE,request.state.sid,body.id,body.name,body.dose,body.frequency,body.kind)
    if not message:return {'patient':patient,'message':''}
    return edited(request.state.sid,patient,'medication_edited',{'medication':body.id,'name':body.name},message+'。')
@app.post('/api/medications/status')
async def medication_status(body:MedicationStatus,request:Request):
    ensure_idle(request.state.sid)
    patient,message=await asyncio.to_thread(record_tools.set_medication_status,core.STORE,request.state.sid,body.id,body.status,body.note)
    return edited(request.state.sid,patient,'medication_status_changed',{'medication':body.id,'status':body.status},message+'。')

def drug_actor(sid):return app.state.accounts.actor(sid) or 'session:'+hashlib.sha256(sid.encode()).hexdigest()[:12]
@app.post('/api/drug-knowledge/run')
async def drug_knowledge_run(body:DrugMessage,request:Request):
    model=(core.MODEL_URL,core.MODEL) if body.use_model and (await asyncio.to_thread(core.model_status))['available'] else None
    return await asyncio.to_thread(drug_knowledge.run_request,body.message,app.state.drug_library,app.state.catalog,drug_actor(request.state.sid),model,core.MODEL_LOCK)
@app.post('/api/drug-knowledge/fetch')
async def drug_knowledge_fetch(body:DrugLicense,request:Request):
    document=await asyncio.to_thread(drug_knowledge.fetch_insert,body.license)
    staged=app.state.drug_library.stage(document,drug_actor(request.state.sid),body.license)
    return {'document':staged,'steps':[{'title':'取得食藥署電子仿單','by':'fetch_tfda_insert','result':f"{staged['name']} · 版本 {staged['version']} · 核准日 {staged['approved']}",'state':'done'}],'message':f"已暫存「{staged['name']}」的食藥署電子仿單，請核對後按「寫入藥品資料庫」。寫入前不會用於任何檢查。"}
@app.post('/api/drug-knowledge/save')
async def drug_knowledge_save(body:DrugDocument,request:Request):
    library=app.state.drug_library
    document,replaced=library.save(body.id,drug_actor(request.state.sid),body.name)
    pages=await asyncio.to_thread(drug_knowledge.attach,core.CORPUS,library.saved())
    core.STORE.audit(None,'drug_document_saved',request.state.sid,{'document':document['id'],'kind':document['kind'],'name':document['name'],'license':document['license'],'version':document['version'],'sha256':document['sha256'],'superseded':replaced})
    return {'document':{**document,'sections':[]},'retrieval_pages':pages,'message':f"已將「{document['name']}」寫入藥品資料庫並加入仿單檢索"+('，取代同一許可證的舊版本' if replaced else '')+'。既有檢查結果不會自動改寫，請重新檢查。'}
@app.post('/api/drug-knowledge/discard')
async def drug_knowledge_discard(body:DrugDocument,request:Request):
    app.state.drug_library.discard(body.id);return {'message':'已略過，這份資料不會寫入。'}
@app.get('/api/drug-knowledge')
async def drug_knowledge_list():return {'documents':app.state.drug_library.saved(False)}

def start_job(sid,job):
    with core.JOB_LOCK:
        if any(j.session==sid and j.status=='running' for j in core.JOBS.values()):raise ValueError('請先停止目前檢查')
        core.JOBS[job.id]=job
    threading.Thread(target=job.run,daemon=True).start()
    return {'id':job.id}
@app.post('/api/review')
async def review(body:Review,request:Request):
    p=core.STORE.active(request.state.sid)
    if not p:raise ValueError('請先輸入病人姓名或病歷號')
    count=len({m['name_key'] for m in p['medications'] if m['status']=='active'})
    if count<1:raise ValueError('請提供至少一項用藥')
    started=start_job(request.state.sid,GovernedReview(request.state.sid,p,body.use_model and count>1))
    app.state.accounts.record_check(request.state.sid)
    return started
@app.post('/api/query')
async def query(body:Query,request:Request):
    from workflow import identity
    question=redact(body.question,identity(body.question).get('name',''))
    job=ResearchJob(request.state.sid,question,body.mode,body.role,body.use_model)
    job.case_patient=core.STORE.active(request.state.sid)
    return start_job(request.state.sid,job)
@app.get('/api/jobs/{jid}')
async def job(jid:str,request:Request):
    j=core.JOBS.get(jid)
    if not j or j.session!=request.state.sid:return JSONResponse({'error':'找不到查詢'},status_code=404)
    return j.snapshot()
@app.post('/api/cancel')
async def cancel(body:Select,request:Request):
    j=core.JOBS.get(body.id)
    if not j or j.session!=request.state.sid:raise ValueError('找不到查詢')
    j.stop.set();j.status='cancelled'
    if j.response:threading.Thread(target=j.response.close,daemon=True).start()
    return {'status':'cancelling'}
@app.get('/api/reviews/{rid}')
async def past_review(rid:str,request:Request):
    r=core.STORE.review(rid);p=core.STORE.active(request.state.sid)
    if not r or not p or r['patient_id']!=p['id']:raise ValueError('請先選擇此病人')
    return r
@app.post('/api/disposition')
async def disposition(body:Disposition,request:Request):return {'patient':core.STORE.disposition(request.state.sid,body.review,body.action,body.note)}
@app.get('/api/reports/{rid}')
async def report(rid:str,request:Request):return app.state.gate.report(request.state.sid,rid)
@app.post('/api/reports/draft')
async def report_draft(body:ReportEdit,request:Request):return app.state.gate.save_draft(request.state.sid,body.review,body.pharmacist,body.patient)
@app.post('/api/reports/approve')
async def approve(body:Approval,request:Request):return app.state.gate.approve(request.state.sid,body.review,body.actor,body.resolutions,body.acknowledged,body.overall_resolution,body.reviewed_reason_ids)
@app.get('/api/reports/{rid}/delivery',response_class=HTMLResponse)
async def delivery(rid:str,request:Request):return app.state.gate.delivery(request.state.sid,rid)
@app.get('/api/catalog')
async def catalog(q:str=''):return {'meta':app.state.catalog.meta,'products':await asyncio.to_thread(app.state.catalog.lookup,q[:100])}
@app.post('/api/medications/classify')
async def classify(body:Medication,request:Request):
    p=core.STORE.active(request.state.sid)
    if not p or not any(m['id']==body.id and m['status']=='active' for m in p['medications']):raise ValueError('藥品不屬於目前病人')
    if body.license and not app.state.catalog.current_product(body.license):raise ValueError('此許可證未在目前未註銷且有效的官方快照中')
    with core.STORE.connect() as db:
        db.execute('INSERT OR REPLACE INTO medication_meta VALUES(?,?,?,?,?)',(body.id,body.acquisition,body.classification,body.license,redact(body.note)))
        db.execute('UPDATE patients SET version=version+1,updated=? WHERE id=?',(core.now(),p['id']))
    core.STORE.audit(p['id'],'medication_classification',request.state.sid,{'medication':body.id,'class':body.classification,'acquisition':body.acquisition,'license':body.license})
    return {'patient':core.STORE.active(request.state.sid),'message':'分類已更新；請重新檢查後覆核，舊版不可交付。'}
@app.get('/api/medications/{mid}/details')
async def drug_details(mid:int,request:Request):
    from drug_details import medication_detail
    patient=core.STORE.active(request.state.sid)
    med=next((m for m in (patient or {}).get('medications',[]) if m['id']==mid),None)
    if not med:raise ValueError('請先選擇此藥品所屬病人')
    if med['status']=='merged':
        active=[m for m in patient['medications'] if m['status']=='active' and m['name_key']==med['name_key'] and m['kind']==med['kind']]
        if len(active)!=1:raise ValueError('藥品已合併，請重新開啟目前藥單')
        med=active[0];mid=med['id']
    retrieved=await asyncio.to_thread(core.CORPUS.label_rag.retrieve,{'medications':[med],'sex':patient.get('sex'),'pregnancy':patient.get('pregnancy')})
    result=await asyncio.to_thread(medication_detail,med,app.state.catalog,retrieved['pages'])
    result['pharmacy_rag']={k:v for k,v in retrieved.items() if k!='pages'}
    if result.get('steps') and result['steps'][0].get('title')=='辨識病人資料' and result.get('action') in {'created','found'}:result['steps'][0]['result']=patient.get('name') or patient['id']
    population=app.state.population.evaluate(patient)
    result['population_checks']=[c for c in population['checks'] if mid in c.get('med_ids',[c['med_id']])]
    result['population_sources']=population['sources']
    return result

@app.post('/api/prescriptions/confirm-change')
async def confirm_change(body:ConfirmedChange,request:Request):
    from workflow import medications,identity,key,stamp
    if not body.acknowledged:raise ValueError('請確認已與醫師核對實際變更')
    if identity(body.text).get('name') or identity(body.text).get('id'):raise ValueError('處方變更不可切換病人')
    patient=core.STORE.active(request.state.sid)
    if not patient:raise ValueError('請先選擇病人')
    if body.old_med_id is not None:
        old=next((m for m in patient['medications'] if m['id']==body.old_med_id and m['status']=='active'),None)
        if not old:raise ValueError('原用藥不屬於目前病人的有效藥單')
        name=body.replacement_name.strip();dose=body.replacement_dose.strip();freq=body.replacement_frequency.strip()
        if not name or not freq or re.search(r'[\r\n\x00-\x1f]',name+dose+freq):raise ValueError('請完整填入單一藥名、用量與頻次，不可含換行或控制字元')
        if re.search(r'[：:;；，,、]',name):raise ValueError('藥名欄位只能填單一實際藥品名稱')
        amount=re.fullmatch(r'(\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?\s*(mg|mcg|g|mL|毫克|微克|公克|錠|顆|單位|units?|IU|U)',dose,re.I)
        if not amount or float(amount[1])<=0 or amount[2] and float(amount[2])<=0:raise ValueError('請填入正確且大於零的每次用量與單位')
        insulin=bool(re.search(r'insulin|胰島素',name,re.I))
        if insulin and not re.search(r'(?:單位|units?|IU|U)$',dose,re.I):raise ValueError('胰島素須使用醫師指定的單位，不可用mg換算')
        if name.casefold() in {'insulin','胰島素'}:raise ValueError('請填醫師指定的胰島素製劑，不可只寫類別')
        stopped=[{'id':old['id'],'name':old['name'],'status':'stopped'}]
        new=[{'name':name,'dose':dose,'frequency':freq,'kind':'new_order','status':'active','text':f'{name} {dose}，{freq}'}]
        change_text=f"已停用：{old['name']}\n新增處方：{name} {dose}，{freq}"
    else:
        meds=medications(body.text);stopped=[m for m in meds if m['status']=='stopped'];new=[m for m in meds if m['status']=='active' and m['kind']=='new_order']
        if not stopped or not new or len(stopped)+len(new)!=len(meds):raise ValueError('請明確分行填寫「已停用：原藥名」及「新增處方：藥名、規格、頻次」')
        if any(not m['dose'] or not m['frequency'] for m in new):raise ValueError('已確認的新處方須有實際規格／用量與頻次')
        change_text=body.text
    with core.STORE.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        selected=db.execute('SELECT patient_id FROM active_patient WHERE session=?',(request.state.sid,)).fetchone()
        rev=db.execute('SELECT patient_id,version FROM patient_reviews WHERE id=?',(body.review,)).fetchone()
        current=db.execute('SELECT version FROM patients WHERE id=?',(patient['id'],)).fetchone()
        if not selected or selected[0]!=patient['id'] or not rev or rev['patient_id']!=patient['id'] or rev['version']!=current[0]:raise ValueError('病歷或用藥版本已變更，請重新檢查後確認')
        if db.execute('SELECT 1 FROM approvals WHERE review_id=?',(body.review,)).fetchone():raise ValueError('已交付版本不可修改，請重新檢查')
        for med in stopped:
            if not db.execute("SELECT 1 FROM patient_meds WHERE patient_id=? AND name_key=? AND status='active'",(patient['id'],key(med['name']))).fetchone():raise ValueError('停用藥物不在目前藥單：'+med['name'])
        for med in stopped:
            if med.get('id') is not None:
                if not db.execute("UPDATE patient_meds SET status='stopped',updated=? WHERE patient_id=? AND id=? AND status='active'",(stamp(),patient['id'],med['id'])).rowcount:raise ValueError('原用藥已變更，請重新檢查')
            else:db.execute("UPDATE patient_meds SET status='stopped',updated=? WHERE patient_id=? AND name_key=? AND status='active'",(stamp(),patient['id'],key(med['name'])))
        for med in new:
            if db.execute("SELECT 1 FROM patient_meds WHERE patient_id=? AND name_key=? AND status='active'",(patient['id'],key(med['name']))).fetchone():raise ValueError('新增藥物已有有效紀錄，請先核對重複處方')
            db.execute('INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(patient['id'],med['name'],key(med['name']),med['dose'],med['frequency'],'new_order',redact(med['text']),'active',stamp(),stamp()))
        for med in new:db.execute("UPDATE patient_meds SET confirmation='confirmed' WHERE patient_id=? AND name_key=? AND status='active'",(patient['id'],key(med['name'])))
        db.execute('INSERT INTO patient_actions(patient_id,review_id,action,note,created) VALUES(?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET action=excluded.action,note=excluded.note,created=excluded.created',(patient['id'],body.review,'adjusted',redact(body.note)+'\n'+redact(change_text),stamp()))
        db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(patient['id'],redact(change_text),'藥師已確認處方變更',stamp()))
        db.execute('UPDATE patients SET version=version+1,updated=? WHERE id=?',(stamp(),patient['id']))
    core.STORE.audit(patient['id'],'confirmed_prescription_change',request.state.sid,{'review':body.review,'note':redact(body.note)})
    return {'patient':core.STORE.get(patient['id']),'message':'已記錄藥師確認的處方變更；請依新版本重新檢查。'}

@app.get('/api/medhop/entities')
async def research_entities(q:str=''):return {'entities':app.state.integration.candidates(q[:100]),'source':'data/medhop/entities.parquet','identity_verified':False}
@app.post('/api/medications/identify')
async def medication_identity(body:MedicationIdentity,request:Request):
    p=core.STORE.active(request.state.sid)
    if not p or not any(m['id']==body.id and m['status']=='active' for m in p['medications']):raise ValueError('藥品不屬於目前病人')
    entity=app.state.integration.drugs.get(core.canonical(body.entity).casefold()) if body.entity.strip() else ''
    if body.entity.strip() and not entity:raise ValueError('識別碼未精確對應至現有 MedHop 藥物實體；未自動猜測別名')
    if entity and len(body.source.strip())<4:raise ValueError('請填寫藥名與研究實體身分的實際核對來源')
    with core.STORE.connect() as db:
        db.execute('INSERT OR REPLACE INTO medication_identifiers VALUES(?,?,?,?,?)',(body.id,entity,redact(body.source),redact(body.note),core.now()))
        db.execute('UPDATE patients SET version=version+1,updated=? WHERE id=?',(core.now(),p['id']))
    core.STORE.audit(p['id'],'medhop_identity_confirmed',request.state.sid,{'medication':body.id,'entity':entity,'source':redact(body.source),'note':redact(body.note)})
    return {'patient':core.STORE.active(request.state.sid),'message':'研究身分已記錄；請重新檢查，舊版結果不可交付。'}
@app.get('/api/audit')
async def audit(request:Request):
    p=core.STORE.active(request.state.sid)
    if not p:return {'events':[]}
    with core.STORE.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM audit WHERE patient_id=? ORDER BY id DESC LIMIT 100',(p['id'],))]
    return {'events':rows}
@app.get('/api/history/{qid}')
async def history(qid:str,request:Request):
    p=core.STORE.active(request.state.sid)
    if not p:raise ValueError('請先選擇病人')
    with core.STORE.connect() as db:r=db.execute('SELECT * FROM patient_queries WHERE id=? AND patient_id=?',(qid,p['id'])).fetchone()
    if not r:raise ValueError('找不到此病人的研究查詢紀錄')
    return {**dict(r),'result':json.loads(r['result'])}
@app.get('/api/record')
async def record(request:Request):return core.read_record(request.state.sid)
@app.post('/api/record')
async def save_record(body:Record,request:Request):
    from workflow import identity
    text=redact(body.text,identity(body.text).get('name',''))
    with sqlite3.connect(core.DB) as db:db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',(request.state.sid,text,body.source,core.now()))
    return core.read_record(request.state.sid)
@app.post('/api/notes')
async def notes(body:Note,request:Request):
    j=core.JOBS.get(body.job)
    if not j or j.session!=request.state.sid or j.status!='done' or j.citation_status not in {'model_cited','extractive'}:raise ValueError('此查詢尚無可保存結果')
    record=core.read_record(request.state.sid)
    if not record['text'] or record['text']!=j.record['text']:raise ValueError('病歷已變更或尚未建立')
    with sqlite3.connect(core.DB) as db:db.execute('INSERT OR IGNORE INTO notes(session,job,body,sources,created) VALUES(?,?,?,?,?)',(request.state.sid,j.id,'待藥師確認\n'+j.answer,json.dumps(j.sources,ensure_ascii=False),core.now()))
    return core.read_record(request.state.sid)
@app.api_route('/documents/{name}',methods=['GET','HEAD'])
async def document(name:str):
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.pdf',name) or not (core.ROOT/'data'/'drug_info'/name).is_file():return JSONResponse({'error':'找不到檔案'},status_code=404)
    return FileResponse(core.ROOT/'data'/'drug_info'/name,media_type='application/pdf')
@app.get('/api/medhop/coverage')
async def medhop_coverage(q:str=''):
    integration=app.state.integration
    rows=integration.candidates(q[:100]) if q.strip() else []
    for row in rows:
        edges=core.CORPUS.graph.adjacency.get(row['title'],[])
        row['indexed_edges']=len(edges)
        row['source_supported_edges']=sum(bool(integration.supported_units(edge)) for edge in edges)
    examples=[]
    for left,right in [('DB00203','DB00820'),('SILDENAFIL','TADALAFIL'),('TADALAFIL','VARDENAFIL'),('FLUOXETINE','SUMATRIPTAN')]:
        paths=integration.paths(left,right,limit=1)
        if paths:examples.append({'drugs':[left,right],'nodes':paths[0]['nodes'],'scope':'每條邊均有原文支持；只是研究連結，不是臨床交互作用證明。'})
    return {'stats':core.CORPUS.stats(),'searchable_names':len(integration.drugs),'drug_typed_entities':sum(row.get('type')=='DRUG' for row in integration.entities.values()),'matches':rows,'examples':examples,'scope':'研究索引可能有同物異名、藥物類別、縮寫或抽取雜訊；檢索名稱數不等於不重複藥品數。'}

@app.api_route('/{name:path}',methods=['GET','HEAD'])
async def static(name:str=''):
    name=name or 'index.html'
    if name not in {'index.html','app.js','workflow.js','compliance.js','app.css','reference.css','logo.png','workspace.js','workspace.css','record-tools.js','record-tools.css','drug-knowledge.js','drug-knowledge.css','account.js','account.css'}:return JSONResponse({'error':'找不到頁面'},status_code=404)
    return FileResponse(core.ROOT/'static'/name)


# safety-alerts-backend-20261102

# anthropometrics-api-20261103
