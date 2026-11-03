"""Pharmacist record tools: JSON exchange and direct edits from the record panel.

An edit never rewrites what was recorded before: the original rows stay, a
patient_event states what changed, and the record version increases so checks
made on an earlier version are shown as outdated.
"""
import datetime, json, re, secrets
from governance import mask_name, redact
from workflow import key, stamp

FORMAT='medhop-patient-record'
SYSTEM='藥你安心：藥事安全Agent'
PROFILE=('age','sex','pregnancy','pregnancy_weeks','lactation','height_cm','weight_kg')
KINDS={'current':'目前用藥','new_order':'新增處方','reported':'自述／自行購買'}
STATUS_LABELS={'stopped':'已停用','removed':'已刪除（輸入錯誤）','active':'恢復使用'}
ALLERGY=re.compile(r'過敏史[：:]\s*([^\n。]+)')

def allergies(events):
    found=''
    for event in events:
        match=ALLERGY.search(event['body'] or '')
        if match:found=match[1].strip()
    return '' if found=='未記錄' else found

def fact_text(value):
    return re.sub(r'\s+',' ',re.sub(r'[；;]','、',value or '')).strip()

def allergy_text(value):
    return re.sub(r'\s+',' ',re.sub(r'[\r\n。]+','，',value or '')).strip(' ，')

def moment(value):
    try:parsed=datetime.datetime.fromisoformat(str(value))
    except ValueError:return stamp()
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    return parsed.isoformat(timespec='seconds')

def log(db,pid,body,source):
    db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(pid,body,source,stamp()))
    db.execute('UPDATE patients SET version=version+1,updated=? WHERE id=?',(stamp(),pid))

def describe(med):
    return ' · '.join([med['name'],med['dose'] or '規格未提供',med['frequency'] or '頻次未提供',KINDS.get(med['kind'],'目前用藥')])

def active_patient(store,session):
    patient=store.active(session)
    if not patient:raise ValueError('請先選擇病人')
    return patient

def export_record(store,pid):
    p=store.get(pid)
    if not p:raise ValueError('找不到這筆病歷')
    meds=[{'name':m['name'],'dose':m['dose'] or '','frequency':m['frequency'] or '','kind':m['kind'] if m['kind'] in KINDS else 'current','status':m['status'],
           'confirmation':m.get('confirmation') or 'pending','acquisition':m.get('acquisition') or 'unknown','classification':m.get('class') or 'unknown',
           'license':m.get('license') or '','text':m['body'] or '','created':m['created'],'updated':m['updated']}
          for m in p['medications'] if m['status'] in {'active','stopped'}]
    interventions=[{'action':i['action'] if i.get('action') in {'contact_prescriber','adjusted','reviewed'} else 'reviewed','note':i['note'],'created':i['created']}
                   for i in p.get('interventions',[]) if i.get('note')]
    return {'format':FORMAT,'format_version':1,'exported_at':stamp(),'source_system':SYSTEM,
            'patient':{k:p.get(k) for k in ('id','name',*PROFILE,'created','updated','version')},
            'history':p['history'],'allergies':allergies(p['events']),'medications':meds,
            'events':[{'source':e['source'] or '','body':e['body'],'created':e['created']} for e in p['events'] if e['body']],
            'interventions':interventions}

def import_record(store,session,record,file_name=''):
    """Always creates a new patient; an imported file never overwrites an existing record."""
    patient=record['patient'];name=(patient.get('name') or '').strip()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        pid='P-'+secrets.token_hex(4).upper()
        while db.execute('SELECT 1 FROM patients WHERE id=?',(pid,)).fetchone():pid='P-'+secrets.token_hex(4).upper()
        weeks=patient.get('pregnancy_weeks') if patient.get('pregnancy')=='pregnant' else None
        db.execute('INSERT INTO patients(id,name,age,sex,created,updated,version,pregnancy,pregnancy_weeks,lactation,height_cm,weight_kg) VALUES(?,?,?,?,?,?,0,?,?,?,?,?)',
                   (pid,mask_name(name),patient.get('age'),patient.get('sex') or '',moment(patient.get('created')),stamp(),patient.get('pregnancy') or 'unknown',weeks,patient.get('lactation') or 'unknown',patient.get('height_cm'),patient.get('weight_kg')))
        if name and name!=mask_name(name):
            db.execute('INSERT OR IGNORE INTO identity_vault VALUES(?,?,?)',(store.digest(name),pid,store.cipher.encrypt(name.encode()).decode()))
        for event in sorted(record['events'],key=lambda e:moment(e.get('created'))):
            db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(pid,redact(event['body'],name),event.get('source') or '匯入紀錄',moment(event.get('created'))))
        for med in record['medications']:
            text=redact(med.get('text') or ' '.join(v for v in (med['name'],med['dose'],med['frequency']) if v),name)
            row=db.execute('INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated,confirmation) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (pid,med['name'],key(med['name']),med['dose'],med['frequency'],med['kind'],text,med['status'],moment(med.get('created')),stamp(),med['confirmation']))
            if med['acquisition']!='unknown' or med['classification']!='unknown' or med['license']:
                db.execute('INSERT OR REPLACE INTO medication_meta VALUES(?,?,?,?,?)',(row.lastrowid,med['acquisition'],med['classification'],med['license'],'由 JSON 檔匯入'))
        for item in record['interventions']:
            db.execute('INSERT INTO patient_actions(patient_id,review_id,action,note,created) VALUES(?,?,?,?,?)',(pid,None,item['action'],redact(item['note'],name),moment(item.get('created'))))
        # The file's declared history and allergies win over whatever its free-text events imply.
        declared=[]
        if record.get('history') is not None:declared.append('病史：'+('；'.join(f for f in map(fact_text,record['history']) if f) or '未記錄'))
        if record.get('allergies') is not None:declared.append('過敏史：'+(allergy_text(record['allergies']) or '未記錄'))
        if declared:db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(pid,redact('\n'.join(declared),name),'匯入病史與過敏史',stamp()))
        origin=[('匯入檔案',file_name or '未命名'),('來源系統',record.get('source_system') or '未標示'),('原病歷號',patient.get('id') or '未標示'),('匯出時間',record.get('exported_at') or '未標示')]
        log(db,pid,redact('\n'.join(label+'：'+value for label,value in origin),name),'由 JSON 檔匯入')
        db.execute('INSERT OR REPLACE INTO active_patient VALUES(?,?)',(session,pid))
    return store.get(pid)

def edit_patient(store,session,changes):
    p=active_patient(store,session);edited=[]
    name=(changes.get('name') or '').strip()
    if name and name!=(p.get('name') or ''):
        if p.get('name') and p['name']!=mask_name(p['name']):store.archive('rename:'+p['id'],p['name'])
        with store.connect() as db:
            db.execute('DELETE FROM identity_vault WHERE patient_id=?',(p['id'],))
            if name!=mask_name(name):db.execute('INSERT OR IGNORE INTO identity_vault VALUES(?,?,?)',(store.digest(name),p['id'],store.cipher.encrypt(name.encode()).decode()))
            db.execute('UPDATE patients SET name=? WHERE id=?',(mask_name(name),p['id']))
            log(db,p['id'],'病人姓名已更正；更正前的姓名加密保存於本機。','藥師更正姓名')
        edited.append('姓名')
    profile={k:v for k,v in changes.items() if k in PROFILE}
    if profile:
        version=store.get(p['id'])['version']
        if store.update_profile(session,profile)['version']!=version:edited.append('基本資料')
    current=store.get(p['id']);lines=[]
    if changes.get('history') is not None:
        facts=[f for f in map(fact_text,changes['history']) if f]
        if facts!=current['history']:lines.append('病史：'+('；'.join(facts) or '未記錄'))
    if changes.get('allergies') is not None:
        allergy=allergy_text(changes['allergies'])
        if allergy!=allergies(current['events']):lines.append('過敏史：'+(allergy or '未記錄'))
    if lines:
        with store.connect() as db:log(db,p['id'],redact('\n'.join(lines),current.get('name','')),'藥師編輯病史與過敏史')
        edited.append('病史與過敏史')
    return store.get(p['id']),edited

def save_medication(store,session,med_id,name,dose,frequency,kind):
    p=active_patient(store,session);pid=p['id']
    name,dose,frequency=(re.sub(r'\s+',' ',value or '').strip() for value in (name,dose,frequency))
    if not name:raise ValueError('請填寫藥名')
    after={'name':name,'dose':dose,'frequency':frequency,'kind':kind};message=''
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=None
        if med_id is not None:
            old=db.execute("SELECT * FROM patient_meds WHERE id=? AND patient_id=? AND status IN ('active','stopped')",(med_id,pid)).fetchone()
            if not old:raise ValueError('藥品不屬於目前病人的藥單，請重新開啟病歷')
        if (not old or name!=old['name']) and re.search(r'[：:;；，,、]',name):raise ValueError('藥名欄位只能填單一藥品名稱')
        if not old:
            if db.execute("SELECT 1 FROM patient_meds WHERE patient_id=? AND name_key=? AND dose=? AND frequency=? AND status='active'",(pid,key(name),dose,frequency)).fetchone():raise ValueError('目前藥單已有相同的藥品、規格與頻次')
            db.execute('INSERT INTO patient_meds(patient_id,name,name_key,dose,frequency,kind,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (pid,name,key(name),dose,frequency,kind,redact('藥師新增：'+describe(after),p.get('name','')),'active',stamp(),stamp()))
            log(db,pid,json.dumps({'新增用藥':describe(after)},ensure_ascii=False),'藥師新增用藥')
            message='已新增 '+name
        elif {k:old[k] or '' for k in after}!=after:
            change={'修改前':describe({k:old[k] or '' for k in after}),'修改後':describe(after)}
            if key(name)!=old['name_key']:
                # Product and research-identity checks were made for the previous drug name.
                for table,label in [('medication_meta','原產品／分類核對'),('medication_identifiers','原 MedHop 識別碼核對')]:
                    row=db.execute(f'SELECT * FROM {table} WHERE med_id=?',(med_id,)).fetchone()
                    if row:change['已清除'+label]=dict(row);db.execute(f'DELETE FROM {table} WHERE med_id=?',(med_id,))
            db.execute('UPDATE patient_meds SET name=?,name_key=?,dose=?,frequency=?,kind=?,updated=? WHERE id=?',(name,key(name),dose,frequency,kind,stamp(),med_id))
            log(db,pid,json.dumps(change,ensure_ascii=False),'藥師編輯用藥')
            message='已更新 '+name
    return store.get(pid),message

def set_medication_status(store,session,med_id,status,note=''):
    p=active_patient(store,session);pid=p['id']
    allowed={'stopped':{'active'},'removed':{'active','stopped'},'active':{'stopped'}}[status]
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM patient_meds WHERE id=? AND patient_id=?',(med_id,pid)).fetchone()
        if not old or old['status'] not in allowed:raise ValueError('藥品狀態已變更，請重新開啟病歷')
        if status=='active' and db.execute("SELECT 1 FROM patient_meds WHERE patient_id=? AND name_key=? AND status='active' AND id!=?",(pid,old['name_key'],med_id)).fetchone():raise ValueError('目前藥單已有同名的有效用藥，請先核對是否重複')
        db.execute('UPDATE patient_meds SET status=?,updated=? WHERE id=?',(status,stamp(),med_id))
        change={'藥品':describe(dict(old)),'變更':STATUS_LABELS[status]}
        if note.strip():change['原因']=redact(note.strip(),p.get('name',''))
        log(db,pid,json.dumps(change,ensure_ascii=False),'藥師變更用藥狀態')
    return store.get(pid),old['name']+' '+STATUS_LABELS[status]
