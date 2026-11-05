"""Local privacy, report drafting, human approval and source catalog services.

No clinical dosage or interaction rules are supplied by this module.
"""
from pathlib import Path
import collections, datetime, hashlib, hmac, html, json, re, secrets, sqlite3
from cryptography.fernet import Fernet
from workflow import PatientStore, identity, stamp, key

PENDING = '待藥師確認'
FREQUENCIES = {'QD':'每天一次','BID':'每天兩次','TID':'每天三次','QID':'每天四次','HS':'睡前','PRN':'需要時使用；使用條件待確認','AC':'飯前','PC':'飯後'}

def mask_name(name):
    if not name or '○' in name:return name
    if re.search('[\u4e00-\u9fff]',name):return name[0]+'○'*(max(1,len(name)-1))
    return name[0]+'***'

def redact(text, name=''):
    if name:text=text.replace(name,mask_name(name))
    text=re.sub(r'(?i)\b[A-Z][12]\d{8}\b','[身分證已遮蔽]',text)
    text=re.sub(r'(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)','[電話已遮蔽]',text)
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[電子郵件已遮蔽]',text)
    text=re.sub(r'(?m)^(?:住址|地址|電話|聯絡電話)\s*[：:].*$','[聯絡資訊未蒐集]',text)
    return text

class PrivateStore(PatientStore):
    """Mask names in working records; exact lookup is HMAC, originals encrypted.

    Local key must be backed up separately and is never included in distribution.
    This is pseudonymisation, not an authentication system or full PHI detector.
    """
    def __init__(self,path):
        path=Path(path);kp=path.parent/'privacy.key'
        if not kp.exists():
            with kp.open('xb') as f:f.write(Fernet.generate_key())
        self.secret=kp.read_bytes();self.cipher=Fernet(self.secret)
        super().__init__(path)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS identity_vault(lookup TEXT,patient_id TEXT,encrypted TEXT,PRIMARY KEY(lookup,patient_id));
            CREATE TABLE IF NOT EXISTS private_archive(id INTEGER PRIMARY KEY,kind TEXT,encrypted TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,patient_id TEXT,event TEXT,actor TEXT,detail TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS medication_meta(med_id INTEGER PRIMARY KEY,acquisition TEXT,class TEXT,license TEXT,note TEXT);
            CREATE TABLE IF NOT EXISTS medication_identifiers(med_id INTEGER PRIMARY KEY,entity TEXT,source TEXT,note TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS approvals(review_id TEXT PRIMARY KEY,patient_id TEXT,version INTEGER,actor TEXT,report TEXT,resolutions TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS report_drafts(review_id TEXT PRIMARY KEY,patient_id TEXT,version INTEGER,pharmacist TEXT,patient TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS patient_queries(id TEXT PRIMARY KEY,patient_id TEXT,version INTEGER,result TEXT,created TEXT);
            ''')
        self.migrate_names()
    def digest(self,name):return hmac.new(self.secret,key(name).encode(),hashlib.sha256).hexdigest()
    def archive(self,kind,value):
        with self.connect() as db:db.execute('INSERT INTO private_archive(kind,encrypted,created) VALUES(?,?,?)',(kind,self.cipher.encrypt(value.encode()).decode(),stamp()))
    def migrate_names(self):
        with self.connect() as db:
            rows=db.execute('SELECT id,name FROM patients').fetchall()
            for row in rows:
                name=row['name']
                if not name or name==mask_name(name):continue
                db.execute('INSERT OR IGNORE INTO identity_vault VALUES(?,?,?)',(self.digest(name),row['id'],self.cipher.encrypt(name.encode()).decode()))
                db.execute('UPDATE patients SET name=? WHERE id=?',(mask_name(name),row['id']))
                # Preserve originals encrypted before changing the local working copy.
                for table,column in [('patient_events','body'),('patient_meds','body'),('patient_actions','note'),('patient_reviews','result')]:
                    for item in db.execute(f'SELECT id,{column} FROM {table} WHERE patient_id=?',(row['id'],)).fetchall():
                        original=item[column];clean=redact(original,name)
                        if clean!=original:
                            db.execute('INSERT INTO private_archive(kind,encrypted,created) VALUES(?,?,?)',(f'{table}:{item["id"]}',self.cipher.encrypt(original.encode()).decode(),stamp()))
                            db.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(clean,item['id']))
    def find_by_name(self,name):
        """Exact-name matches through the private HMAC index; names stay encrypted at rest."""
        with self.connect() as db:rows=db.execute('SELECT patient_id FROM identity_vault WHERE lookup=?',(self.digest(name),)).fetchall()
        found=[]
        for row in rows:
            p=self.get(row[0])
            if p:found.append({'id':p['id'],'name':p['name'],'age':p['age'],'sex':p['sex'],'updated':p['updated']})
        return found
    def create_patient(self,session,name,profile):
        """pharmacist-agent-20261105: structured creation; the working copy keeps a masked name, the original is encrypted."""
        with self.connect() as db:
            pid='P-'+secrets.token_hex(4).upper()
            while db.execute('SELECT 1 FROM patients WHERE id=?',(pid,)).fetchone():pid='P-'+secrets.token_hex(4).upper()
            db.execute('INSERT INTO patients(id,name,age,sex,created,updated,version) VALUES(?,?,?,?,?,?,0)',(pid,mask_name(name),profile.get('age'),profile.get('sex',''),stamp(),stamp()))
            if name!=mask_name(name):db.execute('INSERT OR IGNORE INTO identity_vault VALUES(?,?,?)',(self.digest(name),pid,self.cipher.encrypt(name.encode()).decode()))
        patient=self.select(session,pid)
        rest={k:v for k,v in profile.items() if k not in {'age','sex'}}
        return self.update_profile(session,rest) if rest else self.get(pid)
    def save_review(self,rid,patient,result):
        # Review text addresses the patient by name; at rest it keeps the masked name, like the patients table.
        name=patient.get('name') or ''
        if name and name!=mask_name(name):result=json.loads(json.dumps(result,ensure_ascii=False).replace(name,mask_name(name)))
        super().save_review(rid,patient,result)
    def review(self,rid):
        review=super().review(rid)
        name=((self.get(review['patient_id']) if review else None) or {}).get('name') or ''
        if name and name!=mask_name(name):review['result']=json.loads(json.dumps(review['result'],ensure_ascii=False).replace(mask_name(name),name))
        return review
    def get(self,pid):
        result=super().get(pid)
        if result:
            with self.connect() as db:
                meta={r['med_id']:dict(r) for r in db.execute('SELECT * FROM medication_meta')}
                identifiers={r['med_id']:dict(r) for r in db.execute('SELECT * FROM medication_identifiers')}
                result['queries']=[dict(r) for r in db.execute('SELECT id,version,created FROM patient_queries WHERE patient_id=? ORDER BY created DESC,rowid DESC',(pid,))]
            for med in result['medications']:
                ident=identifiers.get(med['id'],{})
                med.update(medhop_entity=ident.get('entity',''),medhop_identity_source=ident.get('source',''),medhop_identity_note=ident.get('note',''))
                med.update(meta.get(med['id'],{'acquisition':'self_purchased' if re.search(r'自己買|自行購買|自購|OTC|成藥',med['body'],re.I) else 'prescribed' if med['kind']=='new_order' or '處方' in med['body'] else 'unknown','class':'unknown','license':'','note':''}))
        if result:
            result['interventions'],result['audit_events']=self.intervention_history(pid,result['actions'])
        if result and getattr(self,'ingredient_lookup',None):
            for med in result['medications']:self.ingredient_lookup.enrich(med)
        if result:
            with self.connect() as db: names=db.execute('SELECT encrypted FROM identity_vault WHERE patient_id=?',(pid,)).fetchall()
            recovered=[]
            for row in names:
                try:recovered.append(self.cipher.decrypt(row[0].encode()).decode())
                except Exception:pass
            if len(set(recovered))==1:result['name']=recovered[0]
        return result
    def list(self,search=''):
        # patient-search-20261103: names stay encrypted at rest.
        query=key(search)
        with self.connect() as db:
            rows=db.execute('SELECT id,name,age,sex,updated FROM patients ORDER BY updated DESC,id').fetchall()
            vault=db.execute('SELECT patient_id,encrypted FROM identity_vault').fetchall()
        recovered=collections.defaultdict(set)
        for item in vault:
            try:recovered[item['patient_id']].add(self.cipher.decrypt(item['encrypted'].encode()).decode())
            except Exception:continue
        result=[]
        for row in rows:
            row=dict(row);names=recovered.get(row['id'],set())
            if len(names)==1:row['name']=next(iter(names))
            if not query or query in key(row['name'] or '') or query in key(row['id']):result.append(row)
            if len(result)>=40:break
        return result
    def audit(self,pid,event,actor,detail):
        # login-20261105: a signed-in session is recorded as the pharmacist's account.
        named=getattr(self,'actor_name',None);named=named(actor) if named else None
        actor=named if named else actor if not re.fullmatch(r'[A-Za-z0-9_-]{32}',actor) else 'session:'+hashlib.sha256(actor.encode()).hexdigest()[:12]
        with self.connect() as db:db.execute('INSERT INTO audit(patient_id,event,actor,detail,created) VALUES(?,?,?,?,?)',(pid,event,actor,json.dumps(detail,ensure_ascii=False),stamp()))
    def disposition(self,session,rid,action,note):
        p=self.active(session)
        if not p:raise ValueError('請先選擇此檢查所屬的病人')
        note=redact(note,p.get('name','')).strip()
        if action not in {'contact_prescriber','adjusted','reviewed'}:raise ValueError('請選擇處置方式')
        if len(note)<4:raise ValueError('請填寫實際處置或衛教內容')
        try:payload=json.loads(note)
        except (ValueError,TypeError):payload=None
        if isinstance(payload,dict) and payload.get('kind')=='counseling':
            if set(payload)-{'kind','record_id','advice','education','source_ids'}:raise ValueError('衛教紀錄包含不支援的欄位')
            if not isinstance(payload.get('record_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',payload['record_id']):raise ValueError('衛教紀錄識別碼不正確')
            for field in ['advice','education']:
                if not isinstance(payload.get(field),str) or len(payload[field])>1800:raise ValueError('建議與衛教內容須為1800字內文字')
            if not (payload['advice'].strip() or payload['education'].strip()):raise ValueError('請保留至少一項實際建議或衛教內容')
            if not isinstance(payload.get('source_ids'),list) or any(not isinstance(s,str) for s in payload['source_ids']):raise ValueError('衛教來源格式不正確')
        actor=session if not re.fullmatch(r'[A-Za-z0-9_-]{32}',session) else 'session:'+hashlib.sha256(session.encode()).hexdigest()[:12]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            selected=db.execute('SELECT patient_id FROM active_patient WHERE session=?',(session,)).fetchone()
            review=db.execute('SELECT patient_id,version,result FROM patient_reviews WHERE id=?',(rid,)).fetchone()
            current=db.execute('SELECT version FROM patients WHERE id=?',(p['id'],)).fetchone()
            if not selected or selected[0]!=p['id'] or not review or review['patient_id']!=p['id']:raise ValueError('請先選擇此檢查所屬的病人')
            if not current or current[0]!=review['version']:raise ValueError('用藥資料已變更，請重新檢查後再記錄處置')
            if db.execute('SELECT 1 FROM approvals WHERE review_id=?',(rid,)).fetchone():raise ValueError('已確認交付的版本不能修改處置；請重新檢查')
            if isinstance(payload,dict) and payload.get('kind')=='counseling':
                result=json.loads(review['result']);assessment=result.get('assessment') or {}
                valid_ids={s['id'] for s in assessment.get('sources',result.get('sources',[]))}
                if set(payload['source_ids'])-valid_ids:raise ValueError('衛教來源不屬於這次檢查')
                # A retried confirmation must not append a second history entry.
                existing=db.execute("SELECT detail FROM audit WHERE patient_id=? AND event='disposition'",(p['id'],)).fetchall()
                for row in existing:
                    try:
                        old_detail=json.loads(row['detail'])
                        old=json.loads(old_detail.get('note',''))
                    except (ValueError,TypeError):continue
                    if isinstance(old,dict) and old.get('record_id')==payload['record_id']:
                        if old!=payload or old_detail.get('review')!=rid:raise ValueError('同一衛教紀錄識別碼不能改寫不同內容或檢查')
                        return self.get(p['id'])
            created=stamp()
            db.execute('INSERT INTO patient_actions(patient_id,review_id,action,note,created) VALUES(?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET action=excluded.action,note=excluded.note,created=excluded.created',(p['id'],rid,action,note,created))
            # Latest action stays compatible with existing review gates. The audit is append-only.
            db.execute('INSERT INTO audit(patient_id,event,actor,detail,created) VALUES(?,?,?,?,?)',(p['id'],'disposition',actor,json.dumps({'review':rid,'action':action,'note':note},ensure_ascii=False),created))
            db.execute('UPDATE patients SET updated=? WHERE id=?',(created,p['id']))
        return self.get(p['id'])

    def intervention_history(self,pid,legacy_actions):
        with self.connect() as db:
            rows=[dict(row) for row in db.execute('SELECT * FROM audit WHERE patient_id=? ORDER BY id',(pid,))]
        interventions=[];seen=set()
        for row in rows:
            try:detail=json.loads(row['detail'])
            except (ValueError,TypeError):detail={}
            if not isinstance(detail,dict):detail={}
            row['detail']=detail
            if row['event'] not in {'disposition','confirmed_prescription_change'}:continue
            action='adjusted' if row['event']=='confirmed_prescription_change' else detail.get('action','reviewed')
            note=detail.get('note','');review=detail.get('review','')
            try:payload=json.loads(note)
            except (ValueError,TypeError):payload=None
            counseling=isinstance(payload,dict) and payload.get('kind')=='counseling'
            interventions.append({'id':'audit-'+str(row['id']),'review_id':review,'created':row['created'],
                                  'kind':'counseling' if counseling else 'prescription_change' if row['event']=='confirmed_prescription_change' else 'disposition',
                                  'action':action,'note':note,'advice':payload.get('advice','') if counseling else '',
                                  'education':payload.get('education','') if counseling else '',
                                  'source_ids':payload.get('source_ids',[]) if counseling else [],'record_id':payload.get('record_id','') if counseling else '', 'actor':'藥師紀錄'})
            seen.add((review,action,note))
        # Legacy databases may contain actions written before the audit was introduced.
        for row in legacy_actions:
            if (row['review_id'],row['action'],row['note']) in seen:continue
            # Confirmed prescription changes keep text in patient_events and note in audit.
            if any(i['kind']=='prescription_change' and i['review_id']==row['review_id'] and row['note'].startswith(i['note']) for i in interventions):continue
            try:payload=json.loads(row['note'])
            except (ValueError,TypeError):payload=None
            counseling=isinstance(payload,dict) and payload.get('kind')=='counseling'
            interventions.append({**row,'id':'legacy-'+str(row['id']),'kind':'counseling' if counseling else 'disposition',
                                  'advice':payload.get('advice','') if counseling else '', 'education':payload.get('education','') if counseling else '',
                                  'source_ids':payload.get('source_ids',[]) if counseling else [],'record_id':payload.get('record_id','') if counseling else '', 'actor':'既有紀錄'})
        interventions.sort(key=lambda item:(item['created'],int(str(item['id']).split('-')[-1])))
        return interventions,rows



class Catalog:
    def __init__(self,root,pages=None):
        folder=Path(root)/'data'/'government'
        self.meta=json.loads((folder/'provenance.json').read_text(encoding='utf-8'))
        self.rows=json.loads((folder/'tfda-products.json').read_text(encoding='utf-8'))
        pages=pages if pages is not None else json.loads((Path(root)/'data/pharma_pages.json').read_text(encoding='utf-8'))
        self.label_pages=collections.defaultdict(list)
        for page in pages:self.label_pages[page['file']].append(page)
        self.label_licenses=collections.defaultdict(list)
        for file,items in self.label_pages.items():
            text=''.join(re.sub(r'\s+','',__import__('unicodedata').normalize('NFKC',p['text'])) for p in items if p['page']==1)
            for license in re.findall(r'衛[署部]藥[製輸]字第?\d{4,8}號',text):self.label_licenses[license.replace('字第','字').replace('字','字第',1)].append(file)
        self.licenses=collections.defaultdict(list)
        for row in self.rows:self.licenses[row['許可證字號']].append(row)
    def current_product(self,license):
        today=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
        rows=[r for r in self.licenses.get(license,[]) if str(r['有效日期']).replace('/','-')>=today]
        if not rows:return None
        # Official exports can repeat a licence for ingredient/packaging rows.
        # Preserve all supplied values instead of taking an arbitrary last row.
        product={}
        for field in rows[0]:
            values=list(dict.fromkeys(str(r[field]) for r in rows if r.get(field) is not None))
            product[field]='\n'.join(values) if values else None
        product['source_row_count']=len(rows)
        product['source_availability']=self.source_availability(product)
        return product
    def source_availability(self,product):
        license=re.sub(r'\s+','',__import__('unicodedata').normalize('NFKC',product['許可證字號']))
        files=sorted(set(self.label_licenses.get(license,[])))
        refs=[]
        for file in files:
            pages=self.label_pages[file]
            refs.append({'file':file,'url':'/documents/'+file,'ocr':any(p.get('ocr') for p in pages),'pages':len(pages),'text_available':any(p['text'].strip() for p in pages)})
        category=product.get('藥品類別');dose=product.get('用法用量') or ''
        pointer=bool(dose and len(dose)<60 and re.search(r'仿單|說明書',dose) and not re.search(r'\d+\s*(mg|毫克|錠|顆|次)',dose,re.I))
        return {'classification':{'status':'provided' if category else 'source_null','message':'原始許可證資料有藥品類別值。' if category else '官方許可證快照的「藥品類別」為空值，非文字擷取失敗；處方／非處方分類仍待藥師核對。'},'dosage':{'status':'reference_only' if pointer else 'provided' if dose else 'source_empty','message':'許可證的用法用量原文僅指向仿單，未包含詳細劑量。' if pointer else '已提供許可證用法用量原文，仍需核對產品仿單。' if dose else '官方許可證快照未提供用法用量文字。'},'labels':refs,'label_status':'matched' if refs else 'not_matched','label_message':'已對應同許可證的本機原始仿單，可開啟核對；OCR 標記另列。' if refs else '本機現有仿單未對應到此許可證，可能未收錄或許可證尚未辨識；不能拿同成分其他產品仿單替代。','snapshot_date':self.meta['filter_date']}
    def lookup(self,query):
        q=key(query)
        if not q:return []
        fields=['許可證字號','中文品名','英文品名','主成分略述']
        candidates={r['許可證字號'] for r in self.rows if any(q in key(r.get(k) or '') for k in fields)}
        rows=[p for license in sorted(candidates) if (p:=self.current_product(license))]
        rows.sort(key=lambda r:(not any(q==key(r.get(k) or '') for k in fields[:3]),'原料' in str(r.get('用法用量') or ''),'詳見' in str(r.get('用法用量') or '') or not r.get('用法用量')))
        return [{**r,'match':'exact_product' if any(q==key(r.get(k) or '') for k in fields[:3]) else 'candidate_only','source_url':self.meta['url']} for r in rows[:30]]

def medication_checks(patient,catalog):
    result=[]
    for med in patient['medications']:
        if med['status']!='active':continue
        product=catalog.current_product(med.get('license',''))
        result.append({'id':med['id'],'name':med['name'],'recorded_dose':med.get('dose',''),'recorded_frequency':med.get('frequency',''),'state':'pharmacist_comparison_required','product':product,'source_url':catalog.meta['url'],'message':'已附實際產品的官方用法用量，請核對適應症、族群、實際服用量與頻次；未由數字片段推論劑量正確。' if product else '尚未核對實際產品許可證；不能用成分候選或其他產品的用法，判定本病人的劑量正確。'})
    return result

def drafts(patient,assessment):
    reasons=[]
    for check in assessment.get('population_checks',[]):
        if check['status'] in {'contraindicated','condition_unconfirmed'}:reasons.append({'id':check['id'],'text':check['message']})
    for pair in assessment['pairs']:
        if pair['status'] in {'avoid','interaction','review','insufficient'}:
            reasons.append({'id':pair['id'],'text':' × '.join(pair['drugs'])+'：'+{'avoid':'來源記載避免併用','interaction':'來源記載併用影響','review':'原文情境待確認','insufficient':'現有來源不足，無法判定'}[pair['status']]})
    for pair in assessment['pairs']:
        research=pair.get('medhop',{})
        if research.get('status') in {'mechanism_candidate','co_mention','same_entity'}:reasons.append({'id':'research-'+pair['id'],'text':' × '.join(pair['drugs'])+'：MedHop 研究連結與藥物身分待專業核對，不能直接當作臨床交互作用'})
    for med in assessment['medications']:
        if not med.get('dose') or not med.get('frequency'):reasons.append({'id':'med-'+str(med['id']),'text':med['name']+'：缺少劑量或頻次'})
        # A recorded strength is not the patient's administered dose.
        if med.get('dose') and not re.search(r'每次|一次|每回',med.get('body','')):reasons.append({'id':'intake-'+str(med['id']),'text':med['name']+'：請核對規格與每次實際服用量'})
        if med.get('class','unknown')=='unknown':reasons.append({'id':'class-'+str(med['id']),'text':med['name']+'：處方／非處方類別未確認'})
        if med.get('acquisition','unknown')=='unknown':reasons.append({'id':'acquisition-'+str(med['id']),'text':med['name']+'：取得方式未確認'})
    for i,d in enumerate(assessment.get('duplicates',[])):reasons.append({'id':'duplicate-'+str(i),'text':d['name']+'：多筆不同用法待確認'})
    for check in assessment.get('medication_checks',[]):reasons.append({'id':'dosage-'+str(check['id']),'text':check['name']+'：劑量與用法須依實際產品、適應症及病人條件核對'})
    missing=[]
    if patient.get('age') is None:missing.append('年齡')
    if patient.get('sex') in ('',None,'未確認'):missing.append('性別')
    if patient.get('sex')!='男' and patient.get('pregnancy','unknown')=='unknown':missing.append('懷孕狀態')
    if missing:reasons.append({'id':'patient-context','text':'病人基本資料待確認：'+'、'.join(missing)})
    if patient.get('sex')!='男' and (patient.get('pregnancy')=='pregnant' or patient.get('lactation')=='breastfeeding'):reasons.append({'id':'patient-special-population','text':'已回報懷孕或哺乳：請依實際產品原文與病人條件核對用藥；系統不以配對圖譜代替此項評估'})
    pharmacist=[PENDING+'｜藥師摘要',f'個案 {patient["id"]}｜用藥版本 {patient["version"]}',f'{len(assessment["medications"])} 項用藥；{len(assessment["pairs"])} 組配對。']
    if assessment.get('pharmacy_rag'):pharmacist.append('仿單檢索：'+assessment['pharmacy_rag']['method']+'；原文引用：'+', '.join(assessment['pharmacy_rag']['source_ids']))
    pharmacist.append('病人條件：年齡 '+(str(patient.get('age'))+'歲' if patient.get('age') is not None else '未提供')+'；性別 '+(patient.get('sex') or '未確認'))
    pharmacist.append('身高：'+(str(patient['height_cm'])+'公分' if patient.get('height_cm') is not None else '未提供')+'；體重：'+(str(patient['weight_kg'])+'公斤' if patient.get('weight_kg') is not None else '未提供'))
    if patient.get('sex')!='男':pharmacist.append('懷孕狀態：'+{'unknown':'未確認','pregnant':'懷孕中','not_pregnant':'未懷孕','not_applicable':'不適用'}.get(patient.get('pregnancy'),'未確認')+'；孕週：'+(str(patient['pregnancy_weeks']) if patient.get('pregnancy_weeks') is not None else '未提供'))
    for check in assessment.get('population_checks',[]):
        if check['status'] in {'contraindicated','condition_unconfirmed'}:pharmacist.append(check['message']+' 原文：'+check['quote']+' ['+']['.join(check['source_ids'])+']')
    if patient.get('history'):pharmacist.append('病史原文：'+'；'.join(patient['history']))
    pharmacist+=['[PENDING '+r['id']+'] '+r['text'] for r in reasons]
    pharmacist+=['以下引用僅支持各別來源情境；MedHop 為研究資料，不是病人臨床證據。']
    for check in assessment.get('medication_checks',[]):
        pharmacist.append(check['name']+'｜輸入規格／用量：'+(check['recorded_dose'] or '未提供')+'｜頻次：'+(check['recorded_frequency'] or '未提供')+'｜'+check['message'])
        if check['product']:
            pharmacist.append('官方許可證：'+check['product']['許可證字號']+'；用法用量原文：'+(check['product'].get('用法用量') or '官方未提供'))
    for pair in assessment['pairs']:
        if pair.get('quote'):pharmacist.append(' × '.join(pair['drugs'])+'：'+pair['quote']+' ['+']['.join(pair['source_ids'])+']')
    for pair in assessment['pairs']:
        research=pair.get('medhop')
        if not research:continue
        pharmacist.append(' × '.join(pair['drugs'])+'｜MedHop：'+research['message'])
        for item in research['identities']:
            pharmacist.append(item['input']+' → '+(item['entity'] or '未對應')+'；對應方式：'+item['method']+('；核對來源：'+item.get('identity_source','') if item['method']=='pharmacist_confirmed' else ''))
        for path in research['paths']:
            pharmacist.append('研究路徑：'+' ↔ '.join(path['nodes'])+' ['+']['.join(path['source_ids'])+']（原圖譜方向與原文需另外核對，非臨床判定）')
    guide=[PENDING+'｜給長者與照顧者的用藥說明草稿',f'個案代碼：{patient["id"]}','以下整理您提供的藥單；每次吃多少、如何使用，仍請藥師逐項核對。']
    for med in assessment['medications']:
        freq=med.get('frequency') or '次數尚未提供'
        freq=FREQUENCIES.get(freq.upper(),freq)
        guide.append(f'• {med["name"]}：記錄中的規格／用量為 {med.get("dose") or "尚未提供"}；{freq}。')
    guide+=['藥物能否一起使用、是否需要改藥，請先由藥師核對並說明。', '這份草稿不包含系統自行新增的劑量、替代藥或停藥指示。']
    if reasons:guide.append('有資料缺口或用藥疑慮，已停止自動交付，轉交藥師處理。')
    return {'state':'handoff' if reasons else 'pending_confirmation','label':PENDING,'reasons':reasons,'pharmacist':'\n'.join(pharmacist),'patient':'\n'.join(guide),'automatic_dispensing':False}

class ReviewGate:
    def __init__(self,store):self.store=store
    def bound(self,session,rid):
        review=self.store.review(rid);patient=self.store.active(session)
        if not review or not patient or patient['id']!=review['patient_id']:raise ValueError('請先選擇這筆檢查所屬病人')
        if patient['version']!=review['version']:raise ValueError('用藥資料已變更；請重新檢查，舊版報告不可交付')
        return patient,review
    def report(self,session,rid):
        p,r=self.bound(session,rid);result=drafts(p,r['result']['assessment'])
        with self.store.connect() as db:
            edit=db.execute('SELECT * FROM report_drafts WHERE review_id=?',(rid,)).fetchone()
            approval=db.execute('SELECT actor,created FROM approvals WHERE review_id=?',(rid,)).fetchone()
        if edit:result.update(pharmacist=edit['pharmacist'],patient=edit['patient'])
        result.update(review=rid,version=r['version'],approval=dict(approval) if approval else None)
        return result
    def save_draft(self,session,rid,pharmacist,patient_text):
        p,r=self.bound(session,rid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT version FROM patients WHERE id=?',(p['id'],)).fetchone()
            if not current or current[0]!=r['version']:raise ValueError('用藥版本已變更，請重新檢查')
            if db.execute('SELECT 1 FROM approvals WHERE review_id=?',(rid,)).fetchone():raise ValueError('此版已確認；修改內容須重新檢查並覆核')
            db.execute('INSERT OR REPLACE INTO report_drafts VALUES(?,?,?,?,?,?)',(rid,p['id'],p['version'],redact(pharmacist),redact(patient_text),stamp()))
        self.store.audit(p['id'],'report_draft_saved',session,{'review':rid})
        return self.report(session,rid)
    def approve(self,session,rid,actor,resolutions,acknowledged,overall_resolution="",reviewed_reason_ids=None):
        p,r=self.bound(session,rid);report=self.report(session,rid)
        if not acknowledged:raise ValueError('須由藥師確認已逐項核對，才能交付')
        if not actor.strip():raise ValueError('請填寫覆核藥師代碼')
        expected={reason['id'] for reason in report['reasons']}
        grouped=bool(overall_resolution.strip())
        if grouped:
            if len(overall_resolution.strip())<20:raise ValueError('請用整體紀錄說明實際核對依據與處理結果，至少20字')
            if set(reviewed_reason_ids or [])!=expected:raise ValueError('確認範圍與目前待核對清單不同，請重新開啟確認畫面')
            stored_resolutions={'mode':'overall','note':redact(overall_resolution.strip()),'covered_reasons':[{'id':reason['id'],'text':reason['text']} for reason in report['reasons']]}
        else:
            for reason in report['reasons']:
                if len(resolutions.get(reason['id'],'').strip())<4:raise ValueError('請填寫核對紀錄；可使用一份整體紀錄涵蓋所有待確認事項')
            stored_resolutions={k:redact(v) for k,v in resolutions.items()}

        with self.store.connect() as db:
            edit=db.execute('SELECT patient FROM report_drafts WHERE review_id=?',(rid,)).fetchone()
            if not edit:raise ValueError('請先由藥師核對並儲存雙版本報告草稿')
            if report['reasons'] and edit['patient']==drafts(p,r['result']['assessment'])['patient']:raise ValueError('尚有待處理事項；請先修訂白話說明，填入藥師確認後的實際內容')
            action=db.execute('SELECT action,note FROM patient_actions WHERE review_id=?',(rid,)).fetchone()
            if not action or action['action']=='contact_prescriber':raise ValueError('請先完成藥師處置；仍待聯繫確認時不得交付')
            # Atomically recheck the version under the write lock.
            db.execute('BEGIN IMMEDIATE')
            version=db.execute('SELECT version FROM patients WHERE id=?',(p['id'],)).fetchone()[0]
            if version!=r['version']:raise ValueError('用藥資料已變更，請重新檢查')
            active=db.execute('SELECT patient_id FROM active_patient WHERE session=?',(session,)).fetchone()
            if not active or active[0]!=p['id']:raise ValueError('目前選取病人已變更，請重新核對')
            current_draft=db.execute('SELECT pharmacist,patient FROM report_drafts WHERE review_id=?',(rid,)).fetchone()
            if not current_draft or current_draft['pharmacist']!=report['pharmacist'] or current_draft['patient']!=report['patient']:raise ValueError('草稿已更新，請重新核對後確認')
            if db.execute('SELECT 1 FROM approvals WHERE review_id=?',(rid,)).fetchone():raise ValueError('此版已確認交付')
            report['confirmed_by']=redact(actor.strip());report['confirmed_at']=stamp()
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',(rid,p['id'],p['version'],redact(actor.strip()),json.dumps(report,ensure_ascii=False),json.dumps(stored_resolutions,ensure_ascii=False),stamp()))
        self.store.audit(p['id'],'pharmacist_approval',actor,{'review':rid,'version':p['version'],'resolved':sorted(expected),'review_mode':'overall' if grouped else 'per_item'})
        return self.report(session,rid)
    def delivery(self,session,rid):
        p,r=self.bound(session,rid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT version FROM patients WHERE id=?',(p['id'],)).fetchone()
            active=db.execute('SELECT patient_id FROM active_patient WHERE session=?',(session,)).fetchone()
            if not current or current[0]!=r['version'] or not active or active[0]!=p['id']:raise ValueError('病人或用藥資料已變更，請重新核對')
            row=db.execute('SELECT * FROM approvals WHERE review_id=?',(rid,)).fetchone()
        if not row:raise ValueError('待藥師確認；尚未允許交付白話說明')
        report=json.loads(row['report']);text=report['patient']
        self.store.audit(p['id'],'patient_guide_delivered',row['actor'],{'review':rid,'version':p['version']})
        return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>藥你安心｜用藥說明</title><style>body{font:22px/1.8 sans-serif;max-width:850px;margin:40px auto;padding:24px}pre{white-space:pre-wrap;font:inherit}small{font-size:15px}</style><h1>藥你安心｜用藥說明</h1><p>系統原始輸出：待藥師確認<br>本份已由藥師 '+html.escape(row['actor'])+' 覆核交付（'+html.escape(row['created'])+'）。</p><pre>'+html.escape(text)+'</pre><small>藥師確認摘要與處理紀錄保留於藥局本機。用藥版本 '+str(p['version'])+'</small></html>'

# anthropometrics-summary-20261103
