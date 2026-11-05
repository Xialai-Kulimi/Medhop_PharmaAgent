"""Pharmacist-confirmed drug documents: TFDA electronic package inserts and pasted label text.

The TFDA client follows the public GetDrugDoc interface (我國藥品仿單電子格式規範).
The licence prefix table and the VALUE decoding (HTML unescape, base64 when
encode="1", block-tag text extraction) follow shin13/opentaimed, MIT licence,
see licenses/opentaimed-LICENSE. Nothing enters retrieval until a pharmacist
saves the staged document; pasted text stays labelled as not officially verified.
"""
import asyncio,base64,datetime,hashlib,html,json,os,re,secrets,time,types
import urllib.error,urllib.parse,urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

# 許可證字號前綴 → GetDrugDoc 8 碼代碼的前 2 碼（opentaimed sources/license_code.py）。
LICENSE_PREFIX={
    '衛署藥製字':'01','衛署藥輸字':'02','衛署成製字':'03','衛署菌疫製字':'09','衛署菌疫輸字':'10',
    '衛署成輸字':'19','衛署罕藥輸字':'20','衛署罕藥製字':'21','衛署罕菌疫輸字':'22','衛署罕菌疫製字':'23','衛署藥陸輸字':'41',
    '衛部藥製字':'51','衛部藥輸字':'52','衛部成製字':'53','衛部菌疫製字':'59','衛部菌疫輸字':'60',
    '衛部成輸字':'69','衛部罕藥輸字':'70','衛部罕藥製字':'71','衛部罕菌疫輸字':'72','衛部罕菌疫製字':'73','衛部藥陸輸字':'91',
    '內衛藥製字':'12','內衛藥輸字':'13','內衛成製字':'14','內衛菌疫製字':'15','內衛菌疫輸字':'16'}
LICENSE_RE=re.compile(r'((?:衛署|衛部|內衛)[一-鿿]{2,5}字)\s*第?\s*(\d{4,6})\s*號?')
API='https://mcp.fda.gov.tw/Serv/Query.asmx/GetDrugDoc'
PAGE='https://mcp.fda.gov.tw/im_detail_1/'
AGENT='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'
HEADINGS=('適應症','用法用量','用法及用量','禁忌','警語','注意事項','交互作用','副作用','不良反應','特殊族群','懷孕','授乳','過量','藥理','藥物動力學','臨床','儲存','包裝','性狀','成分')
KIND_LABELS={'tfda':'食藥署電子仿單 · 藥師確認寫入','pasted':'藥師貼上藥品資料 · 未經官方核對'}

def now():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')

def license_code(text):
    match=LICENSE_RE.search(text or '')
    if not match:raise ValueError('請提供完整許可證字號，例如：衛署藥輸字第024959號')
    prefix=LICENSE_PREFIX.get(match[1])
    if not prefix:raise ValueError('許可證字號前綴「'+match[1]+'」不在食藥署電子仿單代碼表內')
    return match[1]+'第'+match[2].zfill(6)+'號',prefix+match[2].zfill(6)

def page_url(license):return PAGE+urllib.parse.quote(license)

class BlockText(HTMLParser):
    BLOCKS={'p','div','br','li','tr','h1','h2','h3','h4','h5','h6','table','ul','ol'}
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.BLOCKS:self.parts.append('\n')
        elif tag in {'td','th'}:self.parts.append('\t')
    def handle_endtag(self,tag):
        if tag in self.BLOCKS:self.parts.append('\n')
    def handle_data(self,data):self.parts.append(data)

def html_text(raw):
    parser=BlockText();parser.feed(raw);parser.close()
    lines=(re.sub(r'[ \t　\xa0]+',' ',line).strip() for line in ''.join(parser.parts).splitlines())
    return '\n'.join(line for line in lines if line)

def value_text(node):
    raw=node.text or ''
    if node.get('encode')=='1':raw=base64.b64decode(raw).decode('utf-8','replace')
    if '&lt;' in raw:raw=html.unescape(raw)
    return html_text(raw)

def parse_insert(data,license):
    text=data.decode('utf-8','replace')
    if re.search(r'<!DOCTYPE|<!ENTITY',text,re.I):raise ValueError('食藥署回應格式不符，未寫入任何資料')
    # Only the text sections are needed; images and factory data follow </CONTENT>.
    end=text.find('</CONTENT>')
    if end>=0:text=text[:end+10]+'</DOCUMENT></ROOTDOCUMENT>'
    try:root=ET.fromstring(text)
    except ET.ParseError:raise ValueError('食藥署回應不完整，請稍後再試，或改由藥師貼上仿單內容')
    doc=root.find('DOCUMENT')
    if doc is None or doc.find('INFO') is None:raise ValueError('食藥署電子仿單平台查無 '+license+' 的電子仿單；可改由藥師貼上仿單內容')
    info={child.tag:(child.text or '').strip() for child in doc.find('INFO')}
    sections=[]
    def walk(node,level):
        for section in node.findall('SECTION'):
            title=' '.join(x for x in ((section.findtext('NO') or '').strip(),(section.findtext('TITLE') or '').strip()) if x)
            body='\n'.join(t for t in (value_text(v) for v in section.findall('VALUE') if v.get('type','text')=='text') if t)
            sections.append({'title':title or '未命名段落','level':int(section.get('LEVEL') or level),'text':body})
            walk(section,level+1)
    content=doc.find('CONTENT')
    if content is not None:walk(content,1)
    def block(tag):
        node=doc.find(tag)
        if node is None:return ''
        values=[value_text(v) for v in node.iter('VALUE')]
        return '\n'.join(v for v in values if v) or html_text(html.unescape(''.join(node.itertext())))
    if not any(s['text'] for s in sections):raise ValueError('這份電子仿單沒有可讀取的文字段落（可能僅有圖片），請改由藥師貼上內容')
    return {'kind':'tfda','name':info.get('CNAME',''),'name_en':info.get('ENAME',''),'license':info.get('SNO') or license,
            'version':info.get('VERSION',''),'approved':info.get('VDATE',''),'category':info.get('DTYPE',''),
            'warning':block('WARNING'),'sections':sections,'page_url':page_url(info.get('SNO') or license)}

def fetch_insert(license,deadline=30):
    license,code=license_code(license)
    url=API+'?'+urllib.parse.urlencode({'license':code,'s_code':'','startdate':'','enddate':''})
    data=bytearray();stop=time.monotonic()+deadline
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':AGENT}),timeout=15) as response:
            while True:
                block=response.read(65536)
                if not block:break
                data+=block
                if b'</CONTENT>' in data[-len(block)-16:]:break
                if time.monotonic()>stop:raise ValueError('食藥署電子仿單平台回應逾時，請稍後再試，或改由藥師貼上仿單內容')
                if len(data)>12_000_000:raise ValueError('食藥署回應過大，未寫入任何資料')
    except (urllib.error.URLError,TimeoutError,OSError) as error:
        raise ValueError('無法連線食藥署電子仿單平台（'+type(error).__name__+'），可稍後再試或改由藥師貼上仿單內容')
    document=parse_insert(bytes(data),license)
    document['source_url']=url
    return document

def heading(line):
    bare=re.sub(r'^[#\s【\[（(]*(?:\d+(?:\.\d+)*[.、)）]?|[一二三四五六七八九十]+[、.])?\s*','',line).rstrip('】]）):： ')
    return bare if len(bare)<=16 and any(bare.startswith(h) or bare.endswith(h) for h in HEADINGS) else ''

def paste_sections(text):
    sections=[];current={'title':'貼上內容','level':1,'lines':[]}
    for line in (text or '').splitlines():
        line=line.strip()
        if not line:continue
        title=heading(line)
        if title:
            if current['lines'] or current['title']!='貼上內容':sections.append(current)
            current={'title':title,'level':1,'lines':[]}
        else:current['lines'].append(line)
    sections.append(current)
    return [{'title':s['title'],'level':1,'text':'\n'.join(s['lines'])} for s in sections if s['lines']]

def preview(row,full=True):
    doc={k:row[k] for k in ('id','kind','name','name_en','license','version','approved','category','warning','page_url','retrieved','status','saved','sha256')}
    doc['sections']=json.loads(row['sections']);doc['label']=KIND_LABELS[row['kind']]
    doc['characters']=sum(len(s['text']) for s in doc['sections'])
    if not full:doc['sections']=[{'title':s['title'],'level':s['level']} for s in doc['sections']]
    return doc

class DrugLibrary:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS drug_documents(id TEXT PRIMARY KEY,kind TEXT,name TEXT,name_en TEXT,license TEXT,version TEXT,approved TEXT,category TEXT,warning TEXT,sections TEXT,source_url TEXT,page_url TEXT,retrieved TEXT,sha256 TEXT,status TEXT,created TEXT,saved TEXT,actor TEXT,request TEXT)''')
    def stage(self,document,actor,request):
        ident='D-'+secrets.token_hex(5);stamp=now()
        sections=json.dumps(document['sections'],ensure_ascii=False)
        with self.store.connect() as db:
            db.execute('INSERT INTO drug_documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,document['kind'],document.get('name',''),document.get('name_en',''),document.get('license',''),document.get('version',''),document.get('approved',''),document.get('category',''),document.get('warning',''),sections,document.get('source_url',''),document.get('page_url',''),stamp,hashlib.sha256(sections.encode()).hexdigest(),'staged',stamp,'',actor,request[:2000]))
        return self.get(ident)
    def get(self,ident,full=True):
        with self.store.connect() as db:row=db.execute('SELECT * FROM drug_documents WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('找不到這份待確認的藥品資料')
        return preview(row,full)
    def save(self,ident,actor,name=None):
        doc=self.get(ident)
        if doc['status']!='staged':raise ValueError('這份藥品資料已處理過，請重新查詢或貼上')
        name=(name if name is not None else doc['name']).strip()
        if not name:raise ValueError('請填入藥名，系統依藥名把這份資料納入檢索')
        if doc['kind']=='tfda':name=doc['name']
        with self.store.connect() as db:
            # A newer official insert for the same licence replaces the older saved copy (kept as superseded).
            replaced=[r[0] for r in db.execute("SELECT id FROM drug_documents WHERE status='saved' AND kind='tfda' AND license=?",(doc['license'],)).fetchall()] if doc['kind']=='tfda' else []
            for old in replaced:db.execute("UPDATE drug_documents SET status='superseded' WHERE id=?",(old,))
            db.execute("UPDATE drug_documents SET status='saved',saved=?,actor=?,name=? WHERE id=?",(now(),actor,name,ident))
        return self.get(ident),replaced
    def discard(self,ident):
        doc=self.get(ident,False)
        if doc['status']!='staged':raise ValueError('這份藥品資料已處理過')
        with self.store.connect() as db:db.execute("UPDATE drug_documents SET status='discarded' WHERE id=?",(ident,))
    def saved(self,full=True):
        with self.store.connect() as db:rows=db.execute("SELECT * FROM drug_documents WHERE status='saved' ORDER BY saved").fetchall()
        return [preview(r,full) for r in rows]

def ingredient_line(doc):
    for s in doc['sections']:
        if re.search(r'成分|含量',s['title']) and s['text']:return s['text'][:300]
    return ''

def document_pages(doc):
    """Pages for retrieval: page 1 carries names/licence so PharmacyRAG's document scope finds it."""
    head='\n'.join(x for x in (doc['name'],doc['name_en'],('許可證字號：'+doc['license']) if doc['license'] else '',('有效成分：'+ingredient_line(doc)) if ingredient_line(doc) else '') if x)
    if doc['kind']=='tfda':origin='GetDrugDoc · '+doc['license']+' · 版本 '+doc['version']+' · 核准日 '+doc['approved']
    else:origin='寫入日 '+doc['saved'][:10]
    groups=[];current=[]
    for s in doc['sections']:
        if s['level']==1 and current:groups.append(current);current=[]
        current.append(s)
    if current:groups.append(current)
    if doc.get('warning'):groups.insert(0,[{'title':'警語','level':1,'text':doc['warning']}])
    pages=[]
    for number,group in enumerate(groups,1):
        body='\n'.join(s['title']+'\n'+s['text'] for s in group if s['text'] or s['level']==1)
        pages.append({'file':doc['id'],'page':number,'text':(head+'\n'+body) if number==1 else (doc['name']+' · '+group[0]['title']+'\n'+body),'ocr':False,
                      'kind':KIND_LABELS[doc['kind']],'title':doc['name']+' · '+group[0]['title'],'origin':origin,
                      'url':doc['page_url'] if doc['kind']=='tfda' else '','unverified':doc['kind']=='pasted','document':doc['id']})
    return pages

def attach(corpus,docs):
    """Rebuild label retrieval over the original pages plus saved documents; base data is never edited."""
    from pharmacy_rag import PharmacyRAG
    if not hasattr(corpus,'base_pages'):corpus.base_pages,corpus.base_chunks,corpus.base_text=corpus.pages,corpus.chunks,corpus.pharma_text
    pages=[p for d in docs for p in document_pages(d)]
    chunks=[{**p,'text':p['text'][start:start+1600],'offset':start} for p in pages for start in range(0,len(p['text']),1200) if p['text'][start:start+1600].strip()]
    corpus.label_rag=PharmacyRAG(types.SimpleNamespace(pages=corpus.base_pages+pages,chunks=corpus.base_chunks+chunks))
    corpus.pharma_text=corpus.base_text+''.join('\n'+re.sub(r'\s+',' ',p['text']).casefold() for p in pages)
    return len(pages)

def candidates(catalog,query):
    rows=catalog.lookup(query) if query else []
    return [{'license':r['許可證字號'],'name':r.get('中文品名') or '','name_en':r.get('英文品名') or '','ingredient':r.get('主成分略述') or '','match':r['match']} for r in rows[:8]]
