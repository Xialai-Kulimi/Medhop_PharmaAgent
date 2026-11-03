"""Source-preserving medication descriptions; no invented clinical summaries."""
import re
from workflow import literal_positions

def paragraphs(text):
    return [part.strip() for part in re.split(r'(?<=[。；;])\s*|\n+',text or '') if part.strip()]

def product_detail(product):
    dose=str(product.get('用法用量') or '').strip()
    missing=not dose or (len(dose)<50 and bool(re.search(r'仿單|說明書',dose)) and not re.search(r'\d+\s*(?:mg|毫克|錠|顆|次)',dose,re.I))
    return {**product,'dose_paragraphs':paragraphs(dose),'dosage_available':not missing and '原料' not in dose,'contraindications':None,'contraindications_note':'這份官方許可證快照沒有禁忌欄位；請開啟實際產品仿單核對。','source_kind':'政府許可證快照','dose_missing_reason':'原始欄位僅指向仿單，未包含完整用法用量。' if missing else ''}

def medication_detail(med,catalog,pages):
    product=catalog.current_product(med.get('license',''))
    candidates=catalog.lookup(med['name']) if not product else []
    products=[x for x in candidates if '原料' not in str(x.get('用法用量') or '')]
    references=[]
    for page in pages:
        if page['page']==1 and literal_positions(page['text'][:900],med['name']):
            references.append({'file':page['file'],'page':1,'url':page.get('url','/documents/'+page['file']),'title':page.get('title',page['file']),'label':page.get('kind','原藥事仿單'),'note':'首頁出現該藥名，仍須核對實際產品；不等於本病人的產品已確認。'})
    return {'ingredient_lookup':med.get('ingredient_lookup',{}),'id':med['id'],'name':med['name'],'recorded_dose':med.get('dose',''),'recorded_frequency':med.get('frequency',''),'reported_text':med.get('body',''),'product':product_detail(product) if product else None,'candidates':[product_detail(x) for x in products[:6]],'candidate_count':len(candidates),'pharmacy_references':references[:6],'product_confirmed':bool(product),'source_url':catalog.meta['url'],'snapshot_date':catalog.meta['filter_date'],'missing_reason':'藥單只有藥名／成分，尚未核對實際產品。候選資料不能直接套用為病人處方。' if not product else '未提供的欄位仍須核對實際仿單。'}
