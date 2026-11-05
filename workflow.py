"""Patient lookup, incremental medication history and source-bound pair review."""
from pathlib import Path
from contextlib import contextmanager
import collections, datetime, itertools, json, re, secrets, sqlite3, unicodedata

def stamp():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')

def canonical(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC',str(value))).strip()

def key(value):
    return canonical(value).casefold()


# anthropometrics-20261103
def anthropometrics(text):
    text=canonical(text)
    result={}
    patterns=[('height_cm',r'身高\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(公分|厘米|cm|公尺|m)(?![A-Za-z])'),
              ('weight_kg',r'(?:體重|体重)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(公斤|千克|kg)(?![A-Za-z])')]
    for field,pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match:
            value=float(match[1])
            if field=='height_cm' and match[2].lower() in {'公尺','m'}:value*=100
            result[field]=round(value,6)
    validate_anthropometrics(result)
    return result

def validate_anthropometrics(values):
    import math
    for field,maximum,label in [('height_cm',300,'身高（公分）'),('weight_kg',600,'體重（公斤）')]:
        value=values.get(field)
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<=maximum):
            raise ValueError(label+'數值無效，請核對單位與輸入；未自動推算。')

def identity(text):
    result={}
    pid=re.search(r'(?:病歷號(?:碼)?|病人編號|病歷編號)[ \t]*[：:=]?[ \t]*([A-Za-z0-9][A-Za-z0-9_-]{2,39})|(?<![A-Za-z])ID[ \t]*[：:=][ \t]*([A-Za-z0-9][A-Za-z0-9_-]{2,39})',text,re.I)
    if pid: result['id']=pid[1] or pid[2]
    for name in re.finditer(r'(?:姓名|患者|病人|病患)\s*[：:]?\s*([\u4e00-\u9fff○〇ＯO·]{2,12}|[A-Za-z][A-Za-z .\'-]{1,45})',text):
        value=name[1]
        value=re.split(r'(?:今年|目前|今天|最近|表示|回報|反映|男性|女性|服用|正在|有病|來拿|用藥|的病歷|查詢|資料|基本|說)',value)[0].strip()
        if value and not re.match(r'^(?:的|請|需|仍|已|尚未|這位|那位|同一|本人|狀態)',value) and value not in {'資料','基本資料','姓名','狀況','資訊','服藥','用藥','原文'}:
            result['name']=value
            break
    if 'name' not in result:
        bare=re.match(r'^\s*([\u4e00-\u9fff○〇·]{2,5})[，,\s]+(?=[男女]|\d+\s*歲)',text)
        if bare:result['name']=bare[1]
    if re.fullmatch(r'P-[A-F0-9]{8}',text.strip(),re.I):result['id']=text.strip().upper()
    age=re.search(r'(?:年齡\s*[：:]?\s*)?(\d{1,3})\s*歲',text)
    if not age:age=re.search(r'年齡\s*[：:]\s*(\d{1,3})(?!\d)',text)
    if age and 0<=int(age[1])<130: result['age']=int(age[1])
    sex=re.search(r'(?:性別\s*[：:]?\s*)?([男女])(?:性|生)?(?=[，,。\s\d]|$)',text)
    if sex: result['sex']=sex[1]
    pregnancy=re.search(r'(?:懷孕狀態|是否懷孕|懷孕)[ \t]*[：:][ \t]*(未懷孕|未孕|否|已懷孕|是|懷孕中|不適用|未知|不確定|未確認)',text)
    if pregnancy:
        result['pregnancy']={'未懷孕':'not_pregnant','未孕':'not_pregnant','否':'not_pregnant','已懷孕':'pregnant','是':'pregnant','懷孕中':'pregnant','不適用':'not_applicable'}.get(pregnancy[1],'unknown')
    elif re.search(r'未懷孕|沒有懷孕|未孕',text):result['pregnancy']='not_pregnant'
    elif re.search(r'(?:目前|已經|已)懷孕|懷孕中|懷孕\s*\d+\s*[週周]',text):result['pregnancy']='pregnant'
    weeks=re.search(r'(?:懷孕週數|孕週|懷孕)[ \t]*[：:]?[ \t]*(\d{1,2})\s*(?:週|周)?',text)
    if weeks and 0<=int(weeks[1])<=45:result['pregnancy_weeks']=int(weeks[1])
    lactation=re.search(r'(?:哺乳狀態|是否哺乳|哺乳)[ \t]*[：:][ \t]*(未哺乳|否|哺乳中|是|不適用|未知|不確定|未確認)',text)
    if lactation:result['lactation']={'未哺乳':'not_breastfeeding','否':'not_breastfeeding','哺乳中':'breastfeeding','是':'breastfeeding','不適用':'not_applicable'}.get(lactation[1],'unknown')
    if not lactation:
        if re.search(r'未哺乳|沒有哺乳|已停止哺乳',text):result['lactation']='not_breastfeeding'
        elif re.search(r'正在哺乳|目前哺乳|哺乳中',text):result['lactation']='breastfeeding'
    result.update(anthropometrics(text))
    return result

def narrative_history(text):
    facts=[]
    for sentence in re.split(r'[。；;\n]',text):
        sentence=re.split(r'目前(?:仍)?(?:吃|服用|用藥)|正在(?:吃|服用)|新增處方|今日處方',sentence,maxsplit=1)[0].strip(' ，,')
        explicit=re.match(r'^(?:病史|診斷)(?:[：:]\s*|有\s*)(.+)',sentence)
        if explicit:fact=explicit[1]
        elif re.search(r'支架|胃食道|患有|曾經|手術|(?:^|[，,])\s*有[^，,]+',sentence) and not identity(sentence).get('name'):fact=sentence
        else:continue
        if fact and fact not in facts:facts.append(fact)
    return facts

# record-edit-20261103: a pharmacist edit replaces the derived history; edit logs are not re-read as narrative.
HISTORY_EDIT_SOURCES={'藥師編輯病史與過敏史','匯入病史與過敏史'}
# pharmacist-agent-20261105: the agent writes history through tools; its raw input is kept but never re-parsed.
AGENT_INPUT_SOURCE='藥師對話原文'
RECORD_EDIT_SOURCES={'藥師編輯用藥','藥師新增用藥','藥師變更用藥狀態','藥師更正姓名','由 JSON 檔匯入','藥師更新基本資料',AGENT_INPUT_SOURCE}
def record_history(events):
    facts=[]
    for event in events:
        if event['source'] in HISTORY_EDIT_SOURCES:
            line=re.search(r'^病史[：:](.*)$',event['body'],re.M)
            if line:facts=[fact.strip() for fact in re.split(r'[；;]',line[1]) if fact.strip() and fact.strip()!='未記錄']
        elif event['source'] not in RECORD_EDIT_SOURCES:facts.extend(narrative_history(event['body']))
    return list(dict.fromkeys(facts))

FREQ=re.compile(r'(?:偏頭痛|頭痛|疼痛|症狀|痛|發作)(?:發作|出現|加劇)?(?:的時候|時)(?:才|再)?(?:有需要|需要|必要時|需要時)?(?:服用|使用|吃)|(?:有需要|需要|必要)才(?:服用|使用|吃)|按需(?:服用|使用|吃)?|需要時(?:服用)?|必要時(?:服用)?|持續貼用|貼片每\d+小時更換一次|\b(?:QD|BID|TID|QID|HS|OHS|PRN|Q\d+H|AC|PC)\b|(?:每[日天週周晚]|一天|一日)[^，,。；;\n]{0,16}',re.I)
ATTRIBUTE_NAMES={'劑量','用量','每次用量','服用劑量','每日劑量','頻次','服用次數'}
DOSE_VALUE=r'\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s*(?:mcg|mg|mL|毫克|微克|公克|g\b|錠|顆)(?:\s*/\s*(?:hour|hr|h|小時))?'
IGNORE={'qd','bid','tid','qid','hs','prn','ac','pc','mg','mcg','ml','kg','pci','cad','gerd','egfr','hba','alt','ast','inr','nkcc','medhop','drugbank','graphrag','cyp','sr'}

def medications(text):
    """Extract supplied names/doses only. Never turn a suggested alternative into current use."""
    found=[]
    section='current'
    text=re.sub(r'[，,；;。]\s*(?=新增處方|今日處方|已停用|已停止服用)', '\n', text)
    for line in text.splitlines():
        from label_interactions import compound_line
        line=compound_line(line.strip())
        if not line:continue
        if re.match(r'^(?:患者|姓名|病人|病患|病歷號(?:碼)?|病歷編號|ID[：:]|年齡|性別|懷孕|孕週|是否懷孕|哺乳|是否哺乳|身高|體重|体重|診斷|病史|過敏|實驗室|缺貨|替代|基本)',line):
            if not re.search(r'(?:服用|正在吃|目前吃|目前用藥|現在吃)',line):
                if re.match(r'^(?:替代|缺貨)',line):section='proposed'
                continue
            line=re.split(r'(?:服用|正在吃|目前吃|目前用藥[：:]?|現在吃)',line,maxsplit=1)[-1]
        if re.match(r'^(?:替代|缺貨|候選|建議|可替換|換藥方案)',line):section='proposed'
        if re.match(r'^(?:處方|目前用藥|長期用藥|今日處方|自述用藥|正在服用|現在服用|新增用藥)',line):section='current'
        if section=='proposed':continue
        if re.search(r'可以停|是否停|建議停|考慮停',line):continue
        if re.search(r'(?:可以|是否|能不能|想|考慮)(?:服用|吃|用)',line) and not re.search(r'目前(?:吃|服用)|正在(?:吃|服用)|已服用|自己買|自行服用|(?:今天|今日|醫師).{0,12}開',line):continue
        if re.search(r'可以(?:改|換)|是否(?:改|換)|建議(?:改|換)|考慮(?:改|換)|替代[：:]|缺貨[：:]',line):continue
        kind='reported' if re.search(r'自己買|自述|自行服|成藥|OTC',line,re.I) else 'new_order' if re.search(r'今天.*開|今日處方|新增|新開|醫師開',line) else 'current'
        stopped=bool(re.search(r'已停用|已停止服用|已不再服用',line))
        line_start=len(found)
        # Chinese list delimiters keep medication context apart from adjacent orders.
        for piece in re.split(r'[、，,；;\n]',line):
            attribute=re.fullmatch(r'\s*(?:劑量|用量|每次用量|服用劑量|每日劑量)\s*[：:]?\s*('+DOSE_VALUE+r')[。\s]*',piece,re.I)
            frequency_only=re.fullmatch(r'\s*(?:(?:頻次|服用次數)\s*[：:]?\s*)?('+FREQ.pattern+r')[。\s]*',piece,re.I)
            if frequency_only and re.search(r'[A-Za-z]{4,}',piece):frequency_only=None
            if attribute or frequency_only:
                if len(found)>line_start:
                    previous=found[-1];field='dose' if attribute else 'frequency';value=canonical((attribute or frequency_only)[1])
                    if not previous[field]:previous[field]=value;previous['text']+='，'+piece.strip()
                    elif field=='frequency' and previous[field].startswith('持續貼用') and value.startswith('貼片每') and value not in previous[field]:
                        previous[field]+='，'+value;previous['text']+='，'+piece.strip()
                continue
            clean=re.sub(r'[（(][^）)]*[）)]',' ',piece)
            clean=re.sub(r'[：:]',' ',clean)
            dose_matches=list(re.finditer(r"([A-Za-z][A-Za-z'/-]*(?:[ ]+[A-Za-z][A-Za-z'/-]*){0,2})\s*(\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s*(?:mcg|mg|mL|毫克|微克|公克|g\b|錠|顆)(?:\s*/\s*(?:hour|hr|h|小時))?)",clean,re.I))
            extracted=[]
            research_id=re.search(r'\b(DB\d{5})\b(?:\s+(\d+(?:\.\d+)?\s*(?:mcg|mg|mL|g\b)))?',clean,re.I)
            if research_id and (re.fullmatch(r'\s*DB\d{5}(?:\s+.*)?',clean,re.I) and not re.search(r'[？?]|如何|查詢|機轉',clean) or re.search(r'用藥|服用|處方|藥品',piece)):
                tail=clean[research_id.end():];frequency=FREQ.search(tail)
                extracted.append((research_id[1].upper(),canonical(research_id[2] or ''),frequency[0] if frequency and not (re.search(r'顆|錠',frequency[0]) and not re.search(r'次',frequency[0])) else ''))
            leading=re.match(r"\s*(\d+(?:\.\d+)?\s*(?:mcg|mg|mL|g\b))\s+([A-Za-z][A-Za-z'/-]*)",clean,re.I)
            if leading and not extracted:extracted.append((leading[2],canonical(leading[1]),''))
            for m in dose_matches:
                if leading or research_id and extracted:break
                name=m[1].strip()
                while name.split() and name.split()[0].casefold() in IGNORE:name=' '.join(name.split()[1:])
                if not name or key(name) in IGNORE or name in ATTRIBUTE_NAMES:continue
                tail=clean[m.end():]
                freq=FREQ.search(tail)
                extracted.append((name,canonical(m[2]),freq[0] if freq and not (re.search(r'顆|錠',freq[0]) and not re.search(r'次',freq[0])) else ''))
            if not extracted:
                # Accept a drug name without dosage, while excluding abbreviations and research IDs.
                for m in re.finditer(r"\b([A-Za-z][A-Za-z.'/-]*(?:[ ]+[A-Za-z][A-Za-z.'/-]*){0,3})\b",clean):
                    name=m[1]
                    if len(name)>=4 and key(name) not in IGNORE and not all(key(w) in IGNORE|{'am','pm'} for w in name.split()) and not re.search(r'(?:DB\d|CYP\d|[A-Z]\d{4,})',piece):
                        extracted.append((name,'',''))
                if not extracted:
                    m=re.search(r'(?:服用|吃|用藥[：:]|藥品[：:]|新增[：:]?)\s*([\u4e00-\u9fff]{2,12}?)\s*(\d+(?:\.\d+)?\s*(?:mg|毫克|錠|顆))',piece,re.I)
                    if m:extracted.append((m[1],canonical(m[2]),''))
                    else:
                        bare_cn=re.match(r'^\s*([\u4e00-\u9fff]{2,12}?)\s*(\d+(?:\.\d+)?\s*(?:mg|毫克|錠|顆))',piece,re.I)
                        if bare_cn and bare_cn[1] not in ATTRIBUTE_NAMES and not re.match(r'每|一天|一日|早|晚|飯|睡|需要|必要',bare_cn[1]):extracted.append((bare_cn[1],canonical(bare_cn[2]),''))
                        else:
                            named=re.search(r'(?:服用|吃|用藥[：:]|藥品[：:])\s*([\u4e00-\u9fff]{2,12}?)(?=每天|一天|一日|來吃|[。？?]|$)',piece)
                            if named and named[1] not in {'兩三顆','一兩顆','幾顆','成藥','藥物','止痛藥'}:extracted.append((named[1],'',''))
            for name,dose,freq in extracted:
                found.append({'name':name,'dose':dose,'frequency':freq,'kind':kind,'text':piece.strip(),'status':'stopped' if stopped else 'active'})
        line_freq=FREQ.search(line)
        if line_freq and re.search(r'顆|錠',line_freq[0]) and not re.search(r'次',line_freq[0]):line_freq=None
        if line_freq and (len(found)-line_start==1 or re.search(r'都是|均為|皆為|皆是',line)):
            for med in found[line_start:]:
                if not med['frequency']:med['frequency']=line_freq[0]
    unique={}
    for m in found:unique[(key(m['name']),key(m['dose']),key(m['frequency']),m['status'])]=m
    return list(unique.values())

def repair_medication_attributes(db,pid):
    """Repair only traceable legacy attribute rows; keep rows and source events."""
    rows=[dict(r) for r in db.execute("SELECT * FROM patient_meds WHERE patient_id=? AND status='active'",(pid,))]
    invalid=[r for r in rows if r['name'] in ATTRIBUTE_NAMES];changed=False
    if not invalid:return False
    events=[dict(r) for r in db.execute('SELECT * FROM patient_events WHERE patient_id=?',(pid,))]
    for bad in invalid:
        for event in events:
            if bad['body'] not in event['body'] or bad['created']!=event['created']:continue
            parsed=medications(event['body'])
            if len(parsed)!=1:continue
            desired=parsed[0]
            targets=[r for r in rows if r['name_key']==key(desired['name']) and r['created']==bad['created']]
            if len(targets)!=1:continue
            target=targets[0]
            if any(target[f] and desired[f] and key(target[f])!=key(desired[f]) for f in ['dose','frequency']):continue
            db.execute('UPDATE patient_meds SET dose=?,frequency=?,updated=? WHERE id=?',(target['dose'] or desired['dose'],target['frequency'] or desired['frequency'],stamp(),target['id']))
            db.execute("UPDATE patient_meds SET status='attribute',updated=? WHERE id=?",(stamp(),bad['id']))
            db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(pid,json.dumps({'repaired_medication_id':target['id'],'retired_attribute_id':bad['id'],'original_attribute':bad,'dose':desired['dose'],'frequency':desired['frequency']},ensure_ascii=False),'劑量頻次解析修正',stamp()))
            changed=True;break
    return changed

def merge_compatible_medications(db,pid):
    """Merge complementary same-drug entries; retain merged rows and original events."""
    rows=[dict(r) for r in db.execute("SELECT * FROM patient_meds WHERE patient_id=? AND status='active' ORDER BY id",(pid,))]
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    groups=collections.defaultdict(list)
    for row in rows:groups[(row['name_key'],row['kind'])].append(row)
    merged=0
    def norm(value):return re.sub(r'\s+','',key(value or ''))
    for group in groups.values():
        if len(group)<2:continue
        if any(len({norm(row[field]) for row in group if row[field]})>1 for field in ['dose','frequency']):continue
        metadata={}
        for table in ['medication_meta','medication_identifiers']:
            if table in tables:
                metadata[table]=[dict(r) for row in group for r in db.execute(f'SELECT * FROM {table} WHERE med_id=?',(row['id'],))]
        conflicts=False
        for table,fields in [('medication_meta',['acquisition','class','license']),('medication_identifiers',['entity'])]:
            for field in fields:
                if len({norm(row.get(field)) for row in metadata.get(table,[]) if row.get(field) not in (None,'','unknown')})>1:conflicts=True
        if conflicts:continue
        main=group[0];others=group[1:]
        dose=next((row['dose'] for row in group if row['dose']), '');frequency=next((row['frequency'] for row in group if row['frequency']), '')
        body='\n'.join(dict.fromkeys(row['body'] for row in group))
        confirmation='pending' if (dose!=main['dose'] or frequency!=main['frequency']) else main.get('confirmation','pending')
        db.execute('UPDATE patient_meds SET dose=?,frequency=?,body=?,confirmation=?,updated=? WHERE id=?',(dose,frequency,body,confirmation,stamp(),main['id']))
        for row in others:db.execute("UPDATE patient_meds SET status='merged',updated=? WHERE id=?",(stamp(),row['id']))
        for table,entries in metadata.items():
            if not entries:continue
            combined=dict(next((x for x in entries if x['med_id']==main['id']),entries[0]));combined['med_id']=main['id']
            for field in combined:
                if field!='med_id' and combined[field] in (None,'','unknown'):combined[field]=next((x[field] for x in entries if x.get(field) not in (None,'','unknown')),combined[field])
            columns=list(combined);db.execute('INSERT OR REPLACE INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(combined[c] for c in columns))
        db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(pid,json.dumps({'kept_medication_id':main['id'],'merged_medication_ids':[row['id'] for row in others],'dose':dose,'frequency':frequency},ensure_ascii=False),'同藥資訊補充合併',stamp()))
        merged+=len(others)
    return merged

class PatientStore:
    def __init__(self,path):
        self.path=Path(path)
        self.init()
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=20)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()
    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY,name TEXT,age INTEGER,sex TEXT,created TEXT,updated TEXT,version INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS patients_name ON patients(name);
            CREATE TABLE IF NOT EXISTS patient_events(id INTEGER PRIMARY KEY,patient_id TEXT,body TEXT,source TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS patient_meds(id INTEGER PRIMARY KEY,patient_id TEXT,name TEXT,name_key TEXT,dose TEXT,frequency TEXT,kind TEXT,body TEXT,status TEXT,created TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS active_patient(session TEXT PRIMARY KEY,patient_id TEXT);
            CREATE TABLE IF NOT EXISTS pending_patient(session TEXT PRIMARY KEY,body TEXT,source TEXT);
            CREATE TABLE IF NOT EXISTS patient_reviews(id TEXT PRIMARY KEY,patient_id TEXT,version INTEGER,result TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS patient_actions(id INTEGER PRIMARY KEY,patient_id TEXT,review_id TEXT UNIQUE,action TEXT,note TEXT,created TEXT);
            ''')
            columns={row[1] for row in db.execute('PRAGMA table_info(patients)')}
            for name,definition in [('pregnancy',"TEXT NOT NULL DEFAULT 'unknown'"),('pregnancy_weeks','INTEGER'),('lactation',"TEXT NOT NULL DEFAULT 'unknown'"),('height_cm','REAL'),('weight_kg','REAL')]:
                if name not in columns:db.execute(f'ALTER TABLE patients ADD COLUMN {name} {definition}')
        with self.connect() as db:
            if 'confirmation' not in {r[1] for r in db.execute('PRAGMA table_info(patient_meds)')}:db.execute("ALTER TABLE patient_meds ADD COLUMN confirmation TEXT NOT NULL DEFAULT 'pending'")
    def update_profile(self,session,values):
        patient=self.active(session)
        if not patient:raise ValueError('請先新增或選擇病人')
        allowed={'age','sex','pregnancy','pregnancy_weeks','lactation','height_cm','weight_kg'}
        values={k:v for k,v in values.items() if k in allowed}
        validate_anthropometrics(values)
        if values.get('pregnancy',patient.get('pregnancy'))!='pregnant':values['pregnancy_weeks']=None
        changes={k:v for k,v in values.items() if patient.get(k)!=v}
        if changes:
            with self.connect() as db:
                db.execute('UPDATE patients SET '+','.join(k+'=?' for k in changes)+',updated=?,version=version+1 WHERE id=?',(*changes.values(),stamp(),patient['id']))
                db.execute('INSERT INTO patient_events(patient_id,body,source,created) VALUES(?,?,?,?)',(patient['id'],json.dumps(changes,ensure_ascii=False),'藥師更新基本資料',stamp()))
        return self.get(patient['id'])
    def active(self,session):
        with self.connect() as db:
            row=db.execute('SELECT patient_id FROM active_patient WHERE session=?',(session,)).fetchone()
        return self.get(row[0]) if row else None
    def get(self,pid):
        with self.connect() as db:
            row=db.execute('SELECT * FROM patients WHERE id=?',(pid,)).fetchone()
            if not row:return None
            events=[dict(r) for r in db.execute('SELECT * FROM patient_events WHERE patient_id=? ORDER BY id',(pid,))]
            meds=[dict(r) for r in db.execute("SELECT * FROM patient_meds WHERE patient_id=? AND status!='removed' ORDER BY id",(pid,))]
            reviews=[dict(r) for r in db.execute('SELECT id,version,created FROM patient_reviews WHERE patient_id=? ORDER BY created DESC,rowid DESC',(pid,))]
            actions=[dict(r) for r in db.execute('SELECT * FROM patient_actions WHERE patient_id=? ORDER BY id DESC',(pid,))]
        return {**dict(row),'history':record_history(events),'events':events,'medications':meds,'reviews':reviews,'actions':actions}
    def select(self,session,pid):
        patient=self.get(pid)
        if not patient:raise ValueError('找不到這筆病歷')
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO active_patient VALUES(?,?)',(session,pid))
        return patient
    def reset(self,session):
        with self.connect() as db:
            db.execute('DELETE FROM active_patient WHERE session=?',(session,))
            db.execute('DELETE FROM pending_patient WHERE session=?',(session,))
    def list(self,search=''):
        with self.connect() as db:
            rows=db.execute('SELECT id,name,age,sex,updated FROM patients WHERE name LIKE ? OR id LIKE ? ORDER BY updated DESC LIMIT 40',('%'+search+'%','%'+search+'%')).fetchall()
        return [dict(r) for r in rows]
    def review(self,rid):
        with self.connect() as db:row=db.execute('SELECT * FROM patient_reviews WHERE id=?',(rid,)).fetchone()
        return {**dict(row),'result':json.loads(row['result'])} if row else None
    def save_review(self,rid,patient,result):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO patient_reviews VALUES(?,?,?,?,?)',(rid,patient['id'],patient['version'],json.dumps(result,ensure_ascii=False),stamp()))
    def disposition(self,session,rid,action,note):
        review=self.review(rid)
        patient=self.active(session)
        if not review or not patient or patient['id']!=review['patient_id']:raise ValueError('請先選擇此檢查所屬的病人')
        if patient['version']!=review['version']:raise ValueError('用藥資料已變更，請重新檢查後再記錄處置')
        if action not in {'contact_prescriber','adjusted','reviewed'}:raise ValueError('請選擇處置方式')
        if len(note.strip())<4:raise ValueError('請填寫已核對或已確認的處置內容')
        with self.connect() as db:db.execute('INSERT INTO patient_actions(patient_id,review_id,action,note,created) VALUES(?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET action=excluded.action,note=excluded.note,created=excluded.created',(patient['id'],rid,action,note.strip(),stamp()))
        return self.get(patient['id'])

def literal_positions(text,name):
    pattern=re.compile(r'(?<![A-Za-z])'+re.escape(name)+r'(?![A-Za-z])',re.I) if re.search('[A-Za-z]',name) else re.compile(re.escape(name))
    return list(pattern.finditer(text))

NEGATIVE_STATEMENT=re.compile(r'不會改變|不會影響|不影響|沒有影響|沒有(?:顯著|明顯)的?(?:影響|改變)|無顯著影響|未發現[^。]{0,15}交互作用|no (?:significant )?effect|did not[^.]{0,20}affect',re.I)
def directly_paired(quote,names):
    a,b=map(re.escape,names)
    # A list of drugs in a third product's label is not evidence of an A/B interaction.
    patterns=[]
    for first,second in [(a,b),(b,a)]:
        patterns += [first+r'[ ]*(?:與|和|跟|及|and|with)[ ]*'+second+r'.{0,14}(?:併用|合用|併服|合併|同時|coadmin|concomitant)',
                     first+r'.{0,12}(?:併用|合用|併服|合併使用|同時使用).{0,6}'+second,
                     r'(?:當|在)與?[ ]*'+first+r'.{0,8}(?:併用|合併|併服|合用).{0,14}'+second]
    return any(re.search(pattern,quote,re.I) for pattern in patterns)

# drug-knowledge-20261103: pages from pharmacist-saved drug documents override the PDF labels;
# pasted text is never used to classify a pair automatically.
SOURCE_LABEL_KEYS=('kind','title','origin','url','unverified','document')
def source_labels(page):return {k:page[k] for k in SOURCE_LABEL_KEYS if k in page}

def classify_literal(pair,sources):
    for source in sources:
        if source['id'] not in pair['source_ids'] or source.get('ocr') or source.get('unverified'):continue
        for sentence in re.split(r'(?<=[。；;])\s*',source['text']):
            if len(sentence)>550 or not all(literal_positions(sentence,n) for n in pair['drugs']) or not directly_paired(sentence,pair['drugs']):continue
            status=None
            if NEGATIVE_STATEMENT.search(sentence):status='no_effect_reported'
            elif re.search(r'避免|不建議|不應|禁忌|禁止',sentence):status='avoid'
            elif not re.search(r'沒有|不會|不增加|未增加|未見|無影響',sentence) and re.search(r'增加|降低|升高|減弱|增強|延長',sentence):status='interaction'
            if status:
                pair.update(status=status,quote=sentence,source_ids=[source['id']],action='contact_prescriber' if status in {'avoid','interaction'} else 'review_conditions',classification_method='literal_pair_statement')
                return

def pair_evidence(patient,pages):
    meds=[m for m in patient['medications'] if m['status']=='active']
    names=list({m['name_key']:m['name'] for m in reversed(meds)}.values())
    sources=[];source_map={};pairs=[]
    for a,b in itertools.combinations(names,2):
        candidates=[]
        for page in pages:
            text=canonical(page['text'])
            left,right=literal_positions(text,a),literal_positions(text,b)
            for first in left:
                nearest=min(right,key=lambda m:abs(m.start()-first.start()),default=None)
                if not nearest or abs(first.start()-nearest.start())>750:continue
                start=max(0,min(first.start(),nearest.start())-180)
                end=min(len(text),max(first.end(),nearest.end())+240)
                quote=text[start:end]
                if not re.search(r'交互|併用|合用|同時|影響|增加|降低|抑制|避免|禁忌|interaction|concomitant|effect|avoid',quote,re.I):continue
                score=abs(first.start()-nearest.start())+(80 if page.get('ocr') else 0)
                candidates.append((score,page,quote))
        candidates.sort(key=lambda r:(r[0],r[1]['file'],r[1]['page']))
        refs=[];seen=set()
        for _,page,quote in candidates:
            signature=(page['file'],page['page'],quote)
            if signature in seen:continue
            seen.add(signature)
            if signature not in source_map:
                ident='P'+str(len(sources)+1)
                source_map[signature]=ident
                sources.append({'id':ident,'kind':'藥事 Agent 仿單'+(' · OCR' if page.get('ocr') else ''),'title':f"{page['file']} · 第 {page['page']} 頁",'file':page['file'],'page':page['page'],'text':quote,'full_text':page['text'],'ocr':bool(page.get('ocr')),'origin':'moda-agent/data/drug_info/'+page['file'],'url':'/documents/'+page['file']+'#page='+str(page['page']),**source_labels(page)})
            refs.append(source_map[signature])
            if len(refs)==2:break
        pair={'id':f'pair-{len(pairs)+1}','drugs':[a,b],'status':'review' if refs else 'insufficient','source_ids':refs,'quote':'','action':'review_dispensing' if refs else 'supplement_sources'}
        classify_literal(pair,sources)
        pairs.append(pair)
    duplicates=[]
    for name,rows in itertools.groupby(sorted(meds,key=lambda m:m['name_key']),key=lambda m:m['name_key']):
        rows=list(rows)
        if len(rows)>1:duplicates.append({'name':rows[0]['name'],'entries':[m['body'] for m in rows]})
    return {'pairs':pairs,'duplicates':duplicates,'medications':meds,'sources':sources,'overall':'review_required','patient_id':patient['id'],'version':patient['version']}

def accept_classification(assessment,payload):
    """Only exact, pair-specific quotes can change a source candidate's classification."""
    sources={s['id']:s for s in assessment['sources']}
    rows={r.get('id'):r for r in payload.get('results',[]) if isinstance(r,dict)}
    for pair in assessment['pairs']:
        if pair['status']!='review':continue
        row=rows.get(pair['id'],{})
        source=sources.get(row.get('source_id'))
        quote=canonical(row.get('quote',''))
        status=row.get('status')
        if not source or source['id'] not in pair['source_ids'] or not quote:continue
        if quote not in canonical(source['text']):continue
        if not all(literal_positions(quote,n) for n in pair['drugs']):continue
        # OCR alone remains a candidate for manual review.
        if source.get('ocr'):continue
        if status in {'avoid','interaction'} and (NEGATIVE_STATEMENT.search(quote) or not directly_paired(quote,pair['drugs'])):continue
        if status=='avoid' and re.search(r'避免|禁忌|不可|禁止|不應|不得|不建議|avoid|contraindicat',quote,re.I):
            pair.update(status='avoid',quote=quote,source_ids=[source['id']],action='contact_prescriber')
        elif status=='no_effect_reported' and NEGATIVE_STATEMENT.search(quote) and directly_paired(quote,pair['drugs']):
            pair.update(status='no_effect_reported',quote=quote,source_ids=[source['id']],action='review_conditions')
        elif status=='interaction' and re.search(r'增加|降低|增強|減弱|延長|升高|抑制|increase|decrease|inhibit',quote,re.I):
            pair.update(status='interaction',quote=quote,source_ids=[source['id']],action='contact_prescriber')
    return assessment

# parser-frequency-fix-20261102

# safety-followup-identity-20261102

# symptom-prn-20261102
