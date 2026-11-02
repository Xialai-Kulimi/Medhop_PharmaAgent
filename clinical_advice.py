"""Curated, attributed clinical candidates. No LLM drug/dose guessing or live network."""
from pathlib import Path
from urllib.parse import urlparse
import json,hashlib,re,unicodedata

def norm(value):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value))).strip().casefold()

class ClinicalAdvice:
    def __init__(self,root,pages):
        self.root=Path(root);self.pages={(p['file'],p['page']):p for p in pages}
        raw=json.loads((self.root/'data/clinical_advice_sources.json').read_text(encoding='utf-8'))
        self.external={s['id']:s for s in raw['sources']}
        allowed={'diabetes.org','www.nice.org.uk','sps.nhs.uk','www.nhs.uk','www.cdc.gov','www.nhsinform.scot'}
        for s in self.external.values():
            if urlparse(s['url']).scheme!='https' or urlparse(s['url']).hostname not in allowed:raise ValueError('新增來源不是核對過的官方網址')
            if hashlib.sha256(s['quote'].encode()).hexdigest()!=s['excerpt_sha256']:raise ValueError('新增來源摘錄已改動，請重新核對')
        # Only these original-label excerpts are added, without changing original population_rules.json.
        self.local={}
        for sid,file,page,anchor,end in [
          ('G-LABEL-BAKTAR','A0049191G0.pdf',2,'4.2','5 警語'),
          ('G-LABEL-LOW','A0049191G0.pdf',4,'Sulfonylurea','Coumarin')]:
            row=self.pages[(file,page)];text=unicodedata.normalize('NFKC',row['text']);start=text.index(anchor);stop=text.index(end,start)
            self.local[sid]={'id':sid,'kind':'原有藥事Agent仿單 · 新增核對段落','title':f'{file} · 第{page}頁','file':file,'page':page,
              'text':text[start:stop].strip(),'full_text':row['text'],'ocr':bool(row.get('ocr')),'url':f'/documents/{file}#page={page}',
              'origin':'moda-agent/data/drug_info/'+file,'external':False}

    def evaluate(self,patient,assessment):
        meds=[m for m in patient.get('medications',[]) if m.get('status')=='active']
        by={norm(m['name']):m for m in meds}
        def find(*names):return next((by[norm(n)] for n in names if norm(n) in by),None)
        used={};actions=[];education=[];suggestions=[];checks=[];alerts=[]
        def use(*ids):
            for sid in ids:
                if sid in self.local:used[sid]=dict(self.local[sid]);continue
                s=self.external[sid]
                used[sid]={'id':sid,'kind':'新增官方指引／衛教','title':s['title'],'text':s['quote'],'full_text':s['quote'],
                  'url':s['url'],'origin':'新增官方來源 · 查核 '+s['verified_on'],'external':True,'verification':s['verification'],
                  'scope':s['scope'],'source_summary':s['summary'],'verified_on':s['verified_on'],'excerpt_sha256':s['excerpt_sha256']}
            return list(ids)
        def item(target,text,*ids):target.append({'text':text,'source_ids':use(*ids)})
        age=patient.get('age');adult=isinstance(age,(int,float)) and age>=18
        pregnant=patient.get('sex')!='男' and patient.get('pregnancy')=='pregnant'
        clearly_not_pregnant=patient.get('sex')=='男' or patient.get('pregnancy')=='not_pregnant'
        context=' '.join(patient.get('history',[]))+' '+' '.join(e.get('body','') for e in patient.get('events',[]))
        gly=find('Glibenclamide');baktar=find('BAKTAR')
        clop=find('Clopidogrel');ppi=find('Omeprazole','Esomeprazole');ibu=find('Ibuprofen')
        lithium=find('Lithium');diuretic=find('Furosemide','Hydrochlorothiazide')
        insulin=next((m for m in meds if re.search(r'\binsulin\b|胰島素',m['name'],re.I)),None)
        if pregnant and gly:
            week=f"{patient['pregnancy_weeks']}週" if patient.get('pregnancy_weeks') is not None else '週數待確認'
            item(actions,f'目前懷孕{week}且使用Glibenclamide：調劑前聯繫糖尿病／產科團隊，討論胰島素療程；依血糖紀錄與個人條件決定製劑、單位及頻次，不能沿用口服藥的mg數換算。','G-ADA')
            item(education,'孕期降血糖療程需由醫療團隊安排；帶上近期血糖、餐食與用藥紀錄，勿自行換算胰島素或把所有降血糖治療一起停掉。','G-ADA')
            if adult:
                ids=use('G-ADA','G-NICE')
                suggestions.append({'id':'pregnancy-insulin-'+str(gly['id']),'old_med_id':gly['id'],'old_name':gly['name'],
                  'title':'Glibenclamide → 胰島素療程候選','reason':'孕期糖尿病可與專科討論胰島素療程。若需基礎胰島素，NICE列NPH insulin為候選；是否還需餐時胰島素由醫師判定。',
                  'source_ids':ids,'alternatives':[{'name':'NPH insulin','dose':'','frequency':'','source_ids':ids,
                    'label':'NPH insulin（需基礎胰島素時的候選）','dose_basis':'胰島素單位、頻次及完整療程須由醫師指定；目前不提供固定劑量。'}],
                  'required_information':['空腹與餐後血糖紀錄','體重、腎功能與低血糖史','醫師選定的胰島素製劑、單位及頻次'],
                  'scope':'基礎型候選，不是整套胰島素療程或mg等效替換；NICE全文尚待藥師覆核。'})
        if pregnant and baktar:
            ids=use('G-LABEL-BAKTAR')
            message='BAKTAR原仿單將孕婦／可能懷孕者列為禁忌。先核對感染部位、培養／藥敏、過敏史與腎功能，再由開方醫師選擇抗菌藥；目前未提供感染資料，不能直接指定替代抗生素。'
            item(actions,message,*ids)
            checks.append({'id':'clinical-baktar-pregnancy','med_id':baktar['id'],'med_ids':[baktar['id']],'drug':baktar['name'],
              'condition':'pregnancy','status':'contraindicated','message':message,'quote':self.local[ids[0]]['text'],
              'source_ids':ids,'rule_id':'clinical-baktar-pregnancy','pregnancy_weeks':patient.get('pregnancy_weeks'),
              'action':'contact_prescriber','scope':'原有仿單新增核對段落；仍須核對實際產品。'})
        if pregnant and insulin and not gly:
            item(actions,'目前孕期已記錄胰島素：核對醫師指定的製劑、單位與注射時段，依血糖紀錄由團隊調整；不要把更換紀錄當作完整療程已驗證。','G-ADA')
        if gly or insulin:
            if baktar:item(actions,'BAKTAR與Glibenclamide併用有增強降血糖及低血糖的仿單記載：核對最近血糖、進食狀況與低血糖史，與開方團隊確認監測及處置計畫。','G-LABEL-LOW','G-LOW')
            if adult:
                item(education,'血糖低於70 mg/dL且清醒、可安全吞嚥時，可先補充15克快速吸收醣，15分鐘後再量；仍偏低時依低血糖處理計畫重複。若低於55 mg/dL、症狀嚴重或反應異常，應立即尋求緊急醫療，不能只靠補糖。','G-LOW')
            else:item(education,'兒童／年齡未確認者的低血糖補糖量需由醫療團隊制定；不要直接套用成人15克處理量。','G-LOW')
            item(education,'若意識不清、昏倒或無法安全吞嚥，不要餵食或灌含糖飲料，立即呼叫緊急醫療協助。','G-EMERG')
        if clop and ppi and adult and not pregnant:
            ids=use('G-PPI','G-PANT');hepatic=bool(re.search(r'肝功能不全|肝硬化|嚴重肝|肝衰竭',context))
            reflux=bool(re.search(r'胃食道|胃酸逆流|火燒心|acid reflux|GERD',context,re.I))
            usual=adult and clearly_not_pregnant and (patient.get('sex')=='男' or patient.get('lactation')=='not_breastfeeding') and reflux and not hepatic
            candidates=[{'name':'Pantoprazole','label':'Pantoprazole','dose':'20mg' if usual else '',
              'frequency':'一天一次飯前一小時' if usual else '', 'source_ids':ids,
              'dose_basis':'成人逆流的一般參考用量，非Omeprazole等效換算；肝功能、病情與實際產品需覆核。' if usual else '未確認成人適應症／特殊族群條件，須由醫師指定劑量與頻次。'},
              {'name':'Lansoprazole','label':'Lansoprazole','dose':'','frequency':'','source_ids':use('G-PPI'),'dose_basis':'來源支持藥名候選，這份規則不提供個人劑量。'},
              {'name':'Rabeprazole','label':'Rabeprazole','dose':'','frequency':'','source_ids':use('G-PPI'),'dose_basis':'來源支持藥名候選，這份規則不提供個人劑量。'}]
            suggestions.append({'id':'clop-ppi-'+str(ppi['id']),'old_med_id':ppi['id'],'old_name':ppi['name'],
              'title':ppi['name']+' → Pantoprazole／其他PPI候選',
              'reason':'NHS SPS建議：需PPI治療且使用Clopidogrel時，避免Omeprazole／Esomeprazole，可考慮Pantoprazole、Lansoprazole或Rabeprazole。',
              'source_ids':ids,'alternatives':candidates,'required_information':['PPI適應症與療程','肝功能、特殊族群及實際產品'],
              'scope':'指引候選，不代表全部合併用藥安全；候選劑量仍待醫師／藥師確認。'})
            item(actions,f'核對{ppi["name"]}的適應症與療程；如仍需抑酸，與開方醫師討論Pantoprazole等候選，不以自行停掉Clopidogrel處理交互作用。','G-PPI','G-STOP')
            item(education,'請讓藥師知道是否另買Omeprazole或Esomeprazole，不要把原本藥物與新PPI一起重複服用。若確認改用Pantoprazole，依醫囑服用，通常飯前約一小時，錠劑整顆吞服。','G-PPI','G-PANT')
            alerts.append({'id':'clinical-clop-ppi','status':'interaction','message':'Clopidogrel與'+ppi['name']+'：新增NHS SPS來源建議避免併用並評估其他PPI。','source_ids':use('G-PPI'),'quote':self.external['G-PPI']['quote'],'origin':'新增官方指引'})
        if clop and ibu:
            ids=use('G-CLOP','G-PARA')
            # The name is supported; never guess a paediatric or low-weight dose.
            suggestions.append({'id':'clop-ibuprofen-'+str(ibu['id']),'old_med_id':ibu['id'],'old_name':ibu['name'],
              'title':'Ibuprofen → Acetaminophen（Paracetamol）候選',
              'reason':'NHS指出Ibuprofen與Clopidogrel併用增加出血機會；止痛可與醫師討論Paracetamol。需另核對體重、肝功能、其他含同成分藥與疼痛原因。',
              'source_ids':ids,'alternatives':[{'name':'Acetaminophen','label':'Acetaminophen（Paracetamol）','dose':'','frequency':'',
                'source_ids':ids,'dose_basis':'依體重、肝功能、疼痛及其他用藥指定用量，不把成人最大量當個人建議。'}],
              'required_information':['體重','肝功能及飲酒狀況','疼痛原因與其他含Acetaminophen藥品'],
              'scope':'候選不是已確認可用處方；兒童需另核對兒科來源。'})
            if not adult or pregnant:suggestions.pop() # No adult analgesic candidate extrapolation to children/pregnancy.
            item(actions,'核對是否自行加用Ibuprofen及實際用量，與開方醫師確認止痛方案；Clopidogrel不可因自行購買止痛藥而任意停掉。','G-CLOP','G-STOP')
            item(education,'不要自行再加Ibuprofen或止痛用Aspirin；若需止痛，先由藥師核對候選。使用Acetaminophen時先檢查感冒藥是否也含同成分，避免重複；低體重或肝病者需另核對用量。','G-CLOP','G-PARA')
        if clop and (ibu or find('Aspirin','Warfarin')):
            item(education,'出現黑色柏油便、血便、吐血或咖啡渣樣嘔吐物，應立即就醫並帶上藥袋。','G-BLEED')
        if lithium and diuretic:
            item(actions,'Lithium與利尿劑併用：核對近期鋰濃度、腎功能與電解質、脫水情況及實際製劑；由開方團隊安排更密切監測，勿任意換品牌或加量。','G-LI')
            item(education,'持續腹瀉、嘔吐、明顯手抖、走路不穩或意識改變可能是鋰中毒；疑似中毒時暫緩服用下一劑並立即就醫。平時維持合適飲水，避免突然大幅改變鹽分攝取，勿自行購買NSAID止痛藥。','G-LI')
        if not actions:
            for pair in assessment.get('pairs',[]):
                if pair.get('status') not in {'avoid','interaction'}:continue
                refs=[s for s in assessment.get('sources',[]) if s['id'] in pair.get('source_ids',[])]
                for source in refs:used[source['id']]=source
                actions.append({'text':' × '.join(pair['drugs'])+'：'+re.sub(r'\s+',' ',pair.get('quote') or '來源記載併用影響')[:280]+'；核對這一組的實際處方與適用條件，再與開方醫師確認處置。','source_ids':[s['id'] for s in refs]})
            if not actions:
                missing=[m['name']+'：'+('/'.join(x for x,v in [('每次用量',m.get('dose')),('頻次',m.get('frequency'))] if not v)) for m in meds if not m.get('dose') or not m.get('frequency')]
                actions.append({'text':'請補充 '+'；'.join(missing)+'，再核對對應仿單。' if missing else '本次未命中已核對的具體替代規則；請逐對檢視 '+'、'.join(' × '.join(p['drugs']) for p in assessment.get('pairs',[]))+' 的來源與缺口，不推定整張處方安全。','source_ids':[]})
        if not education:
            for m in meds[:3]:education.append({'text':m['name']+'：藥袋回報用量 '+(m.get('dose') or '尚未提供')+'，頻次 '+(m.get('frequency') or '尚未提供')+'。這是回報紀錄，交付前仍須逐項核對實際服法。','source_ids':[]})
        return {'version':'2026-11-02','patient_id':patient['id'],'patient_version':patient['version'],
          'dispensing_suggestions':suggestions,'pharmacist_actions':actions,'patient_education':education,'sources':list(used.values()),
          'additional_population_checks':checks,'interaction_alerts':alerts,
          'scope':'明確條件與來源規則；不是全藥物建議引擎。候選不更改病歷，僅由藥師確認實際方案後寫入。'}
