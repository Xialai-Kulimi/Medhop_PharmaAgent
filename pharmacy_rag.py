"""BM25 retrieval of original pharmacy label chunks, with auditable RAG context."""
import re,hashlib
from workflow import canonical,literal_positions,source_labels

def searchable(text):
    # Deterministic Chinese bigrams alongside native Latin identifiers; no LLM aliases.
    text=canonical(text)
    grams=[]
    for phrase in re.findall(r'[\u4e00-\u9fff]+',text):
        grams.extend('zh'+''.join(format(ord(c),'x') for c in phrase[i:i+2]) for i in range(len(phrase)-1))
    return text+' '+' '.join(grams)

class PharmacyRAG:
    def __init__(self,corpus):
        from medhop_query import LexicalIndex
        self.corpus=corpus;self.index=LexicalIndex(searchable(c['text']) for c in corpus.chunks)
        self.first={p['file']:p for p in corpus.pages if p['page']==1}
        self.page_map={(p['file'],p['page']):p for p in corpus.pages}
    def retrieve(self,patient):
        meds=[m for m in patient.get('medications',[]) if m['status']=='active']
        sources=[];queries=[];seen={};selected_pages=set()
        names=' '.join(m['name'] for m in meds)
        for med in meds:
            ingredient=med.get('ingredient_lookup',{})
            components=ingredient.get('ingredients',[]) if ingredient.get('status')=='resolved' else []
            candidates=ingredient.get('candidates',[])
            licenses={re.sub(r'\s+','',canonical(c['license'])) for c in candidates}
            terms=[med['name']]+components
            files=[]
            for file,page in self.first.items():
                first=canonical(page['text']);compact=re.sub(r'\s+','',first)
                if any(lic in compact for lic in licenses) or any(literal_positions(first[:1400],n) for n in terms):files.append(file)
            condition=' 懷孕 孕婦 禁忌' if patient.get('sex')!='男' and patient.get('pregnancy')=='pregnant' else ''
            pediatric=isinstance(patient.get('age'),int) and patient['age']<18
            if pediatric:condition+=' 兒童 小兒 青少年 年齡 禁忌'
            query=' '.join(terms)+' '+names+' 交互作用 併用 注意事項 用法 用量'+condition
            scores=self.index.scores(searchable(query));primary=self.index.scores(searchable(' '.join(terms)))
            scores=[score+2*anchor for score,anchor in zip(scores,primary)]
            # Rank only within the existing document scope. Co-medication mentions are
            # retrieval candidates, never a clinical interaction or product confirmation.
            def priority(i):
                c=self.corpus.chunks[i]
                page=self.page_map[(c['file'],c['page'])]
                text=canonical(page['text'])
                co=sum(bool(literal_positions(text,m['name'])) for m in meds if m['name']!=med['name'])
                population=bool(pediatric and re.search(r'兒童|小兒|青少年|新生兒|低出生體重|早產兒',text))
                population=population or bool(patient.get('pregnancy')=='pregnant' and re.search(r'懷孕|孕婦|妊娠',text))
                return (-co,-int(population),-scores[i],i)
            ranked=sorted((i for i,c in enumerate(self.corpus.chunks) if c['file'] in files and scores[i]>0),key=priority)
            hits=[];page_seen=set()
            for i in ranked:
                chunk=self.corpus.chunks[i];signature=(chunk['file'],chunk['page'])
                if signature in page_seen:continue
                page_seen.add(signature);selected_pages.add(signature)
                sig=(chunk['file'],chunk['page'],chunk['offset'])
                sid=seen.get(sig)
                if sid is None:
                    sid='R'+str(len(sources)+1);seen[sig]=sid
                    sources.append({'id':sid,'kind':'藥事仿單 RAG · BM25'+(' · OCR，需核對 PDF' if chunk.get('ocr') else ''),'title':chunk['file']+' · 第 '+str(chunk['page'])+' 頁','file':chunk['file'],'page':chunk['page'],'text':chunk['text'],'full_text':self.page_map[signature]['text'],'ocr':bool(chunk.get('ocr')),'origin':'moda-agent/data/drug_info/'+chunk['file'],'url':'/documents/'+chunk['file']+'#page='+str(chunk['page']),'retrieval':{'method':'BM25','chunk_offset':chunk['offset'],'score':round(scores[i],6),'sha256':hashlib.sha256(chunk['text'].encode()).hexdigest()},**source_labels(chunk)})
                hits.append(sid)
                if len(hits)>=8:break
            queries.append({'medication_id':med.get('id'),'drug':med['name'],'query':query,'document_scope':files,'source_ids':hits,'status':'retrieved' if hits else 'insufficient','ingredients_from_database':components})
        return {'method':'BM25 original-label RAG','index':'data/pharma_pages.json → 1600-character overlapping chunks','status':'retrieved' if sources else 'insufficient','queries':queries,'source_ids':[s['id'] for s in sources],'sources':sources,'pages':[self.page_map[k] for k in sorted(selected_pages)],'generation':'source_bound_pair_classification_and_extracts','model_status':'not_run','scope':'只使用檢索原文作為藥單核對上下文；檢索命中不等於交互作用成立，無命中不代表安全。'}
