'use strict';
const $ = s => document.querySelector(s);
const log = $('#logInner');
let boot, record, draft = null, active = null, busy = false, modelEnabled = true;
const sourcesById = new Map();
const el = (tag, cls, text) => { const e = document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e; };
const button = (text, action, cls='text-btn') => {const b=el('button',cls,text);b.type='button';b.addEventListener('click',action);return b;};
const formatTime = value => new Date(value || Date.now()).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
function toast(text){$('#toast').textContent=text;$('#toast').style.display='block';setTimeout(()=>$('#toast').style.display='none',3500);}
async function api(path,body){const res=await fetch(path,{credentials:'same-origin',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});const data=await res.json();if(!res.ok)throw Error(data.error||(data.detail?'輸入格式驗證未通過，請核對必填欄位與內容長度':'連線失敗'));return data;}
function scrollEnd(){const l=$('#log');l.scrollTop=l.scrollHeight;}
function setBusy(value){busy=value;$('#send').disabled=value||!$('#input').value.trim();$('#input').disabled=value;['patientSelect','patientSearch'].forEach(id=>$('#'+id).disabled=value);}
function meta(name){const m=el('div','meta'),who=el('span','who');if(name==='藥你安心：藥事安全Agent'){const logo=el('img','agent-message-logo');logo.src='/logo.png';logo.alt='';who.append(logo);}who.append(document.createTextNode(name));m.append(who,el('time','',formatTime()));return m;}
function newTurn(){const t=el('article','turn');const body=el('div','turn-body');t.append(meta('藥你安心：藥事安全Agent'),body);log.append(t);return body;}
function userTurn(text){const t=el('article','msg-user');t.append(meta('你'),el('div','bubble',text));log.append(t);scrollEnd();}

function openRecord(text,source){$('#recordText').value=text??record.text;$('#recordSource').value=(source??record.source)||'使用者輸入';$('#recordError').textContent='';$('#recordDialog').showModal();}
function welcome(){workflowWelcome();}

function patientFields(text){
  const name=text.match(/(?:患者|姓名|病人)[：:]\s*([^，,\n：:]{1,15})/);
  const age=text.match(/(?:年齡[：:]\s*)?(\d{1,3})\s*歲/);
  const sex=text.match(/性別[：:]\s*([^\n，,]+)/)||text.match(/患者[：:][^\n]*?[，,]\s*([男女])/);
  const diagnosis=text.match(/診斷[：:]\s*([^\n]+)/);
  const allergy=text.match(/過敏史[：:]\s*([^\n]+)/);
  const lines=text.split('\n').map(l=>l.trim()).filter(Boolean);
  const meds=lines.filter(l=>/[a-zA-Z][a-zA-Z /'-]+\s*\d+(?:\.\d+)?\s*(?:mg|mcg|g\b|mL|錠)/i.test(l)&&!/(?:患者|實驗室|eGFR|HbA1c|ALT|AST|Bilirubin|level|替代[：:])/.test(l));
  return {name:name?name[1].trim():'姓名未提供',age:age?age[1]+' 歲':'年齡未提供',sex:sex?sex[1]:'性別未提供',diagnosis:diagnosis?.[1],allergy:allergy?.[1],meds};
}
function renderRecord(){
  if(isWorkflow())return renderPatientRecord();
  const target=$('#recScroll');target.replaceChildren();const current=draft||record;
  $('#recordState').textContent=draft?'待確認':record.text?'已儲存':'尚未建檔';$('#editRecord').textContent=record.text?'編輯病歷':'建立病歷 ＋';
  if(draft){const notice=el('div','draft-banner','原專案範例已載入，尚未寫入病歷。');notice.append(document.createElement('br'),button('確認內容並儲存',()=>openRecord(draft.text,draft.source),'btn btn-primary'),document.createTextNode('　'),button('略過',()=>{draft=null;renderRecord();}));target.append(notice);}
  if(!current.text){const empty=el('div','empty-state');empty.append(el('div','empty-glyph'),el('h3','','尚未建立病歷'),el('p','','從左側載入原專案範例，\n或輸入病人提供的資料。\n確認儲存後，後續查詢才會讀取。'),button('建立病歷',()=>openRecord('','使用者輸入'),'btn btn-quiet'));target.append(empty);return;}
  const p=patientFields(current.text);const head=el('div','record-head');head.append(el('h2','',p.name),el('div','meta',`${p.sex}　·　${p.age}`));target.append(head);
  function section(title,content){const s=el('section','record-section');s.append(el('h3','',title),content);target.append(s);return s;}
  section('診斷與病史',el('p','',p.diagnosis||'原文未標示診斷欄位，請查看完整病歷。'));
  section('過敏史',el('p','',p.allergy||'未提供'));
  const meds=el('div');p.meds.forEach(text=>meds.append(el('div','record-row',text)));if(!p.meds.length)meds.append(el('p','','未辨識到明確的藥品劑量列，請查看原文。'));const medSection=section('用藥原文',meds);medSection.querySelector('h3').append(el('span','',p.meds.length+' 列'));
  const raw=el('details','record-raw');raw.append(el('summary','','完整病歷原文'),el('div','',current.text));target.append(raw);
  target.append(el('p','record-source',`來源：${current.source||'使用者輸入'}${record.updated&&!draft?'\n儲存時間：'+formatTime(record.updated):''}`));
  const notes=el('div');for(const n of record.notes||[]){const card=el('div','note-card');const details=el('details');details.append(el('summary','','已確認的分析紀錄'),el('pre','',n.text));const labels=el('p','source-summary',n.sources.map(s=>`[${s.id}] ${s.title}`).join('\n'));details.append(labels);card.append(el('time','',formatTime(n.created)),details);notes.append(card);}if(!record.notes?.length)notes.append(el('p','muted','尚無分析紀錄。查詢結果確認後可另行存入。'));section('分析紀錄',notes);
}

function showSource(source){
  const box=$('#evidenceContent'),dialog=$('#evidenceDialog');box.replaceChildren();$('#evidenceTitle').textContent=`[${source.id}] ${source.title}`;
  box.append(el('p','muted',`${source.kind} · ${source.origin}${source.record_id?' · 紀錄 '+source.record_id:''}`));
  if(source.unverified)box.append(el('p','ocr-source-note','這段文字由藥師貼上，未經官方仿單核對；系統不會據此自動判定交互作用。'));
  if(source.ocr)box.append(el('p','ocr-source-note','這是掃描仿單的 OCR 擷取文字，可能有錯字或表格錯位；請以原始 PDF 核對藥名、數字及單位。'));
  if(source.url){const a=el('a','btn btn-primary original-pdf-link',(source.document?'在食藥署網站核對 ↗':source.kind==='補充原廠仿單'?'開啟原廠仿單 ↗':'開啟原始 PDF ↗'));a.href=source.url;a.target='_blank';a.rel='noopener';box.append(a);}
  const excerpt=source.text||'',full=source.full_text||excerpt;
  if(excerpt&&(!source.ocr||excerpt.length<=1200)){const quote=el('section','source-citation');quote.append(el('h3','','本次引用原句'+(source.ocr?'（OCR，需核對 PDF）':'')),el('blockquote','',excerpt));box.append(quote);}
  if(source.ocr||full!==excerpt){const raw=el('details','raw-source');raw.append(el('summary','',source.ocr?'查看完整 OCR 原始文字（未校正）':'查看完整來源文字'),el('pre','raw-source-text',full));box.append(raw);}
  if(!dialog.open)dialog.showModal();
}
function textWithCitations(target,text,sourceMap){target.replaceChildren();const re=/\[([MP]\d+)\]/g;let last=0;for(const m of text.matchAll(re)){target.append(document.createTextNode(text.slice(last,m.index)));const source=sourceMap.get(m[1]);target.append(source?button(m[0],()=>showSource(source),'cite-link'):document.createTextNode(m[0]));last=m.index+m[0].length;}target.append(document.createTextNode(text.slice(last)));}
function renderSteps(target,steps){target.replaceChildren();for(const s of steps){const li=el('li','step '+s.state);const row=el('div','step-row');row.append(el('span','step-title',s.title),el('span','by',s.by),el('span','res',s.result));li.append(el('span','dot',s.state==='done'?'✓':''),row);target.append(li);}}
function renderEvidence(target,job){
  target.replaceChildren();const map=new Map(job.sources.map(s=>[s.id,s]));
  if(job.coverage?.missing?.length)target.append(el('div','notice warn','現有仿單未找到以下字面名稱：'+job.coverage.missing.join('、')+'。這只表示資料覆蓋不足，不表示沒有風險；未自動換用別名或其他藥品。'));
  const pathsAlreadyPresented=!!(job.workflow||job.assessment||job.pathsAlreadyPresented);
  if(!pathsAlreadyPresented&&job.paths.length){for(const path of job.paths){const panel=el('div','medhop');const head=el('div','mh-head');head.append(el('span','mh-title','MedHop 圖譜路徑'),el('span','mh-query',path.edges.length+' 跳 · 索引抽取結果'));panel.append(head);const chain=el('div','chain');for(let i=0;i<path.nodes.length;i++){const node=el('div','node');node.append(el('span','node-name',path.nodes[i]));chain.append(node);if(i<path.edges.length){const edge=path.edges[i];const link=el('div','edge');link.append(button('關係 #'+edge.id,()=>showEdge(edge,map),'text-btn'));link.append(el('span','edge-line'+(edge.source!==path.nodes[i]?' reverse':'')));chain.append(link);}}panel.append(chain,el('p','path-foot','連線保留原索引方向；點選關係可核對描述與支持文本。'));target.append(panel);}}
  if(!pathsAlreadyPresented&&!job.paths.length&&job.mode!=='pharmacy'){target.append(el('div','notice','本次未找到可完整引用的 MedHop 多跳路徑，不補上推測的藥物關係。'));}
  const groups=[['MedHop 支持文本',job.sources.filter(s=>s.id.startsWith('M'))],['藥品仿單原文',job.sources.filter(s=>s.id.startsWith('P')||s.id.startsWith('L')||s.id.startsWith('R'))]];
  for(const [name,items] of groups){if(!items.length)continue;const heading=el('div','evidence-heading',name);heading.append(el('span','',items.length+' 筆 · 按下展開'));target.append(heading);const list=el('div','results');for(const source of items){const details=el('details','source-result');const summary=el('summary');summary.append(el('span','cite',source.id),document.createTextNode(source.title));details.append(summary,el('div','excerpt',source.text));const links=el('div','source-links');links.append(button('查看完整原文',()=>showSource(source)));if(source.url){const a=el('a','',(source.document?'食藥署仿單 ↗':source.kind==='補充原廠仿單'?'原廠仿單 ↗':'原始 PDF ↗'));a.href=source.url;a.target='_blank';a.rel='noopener';links.append(a);}if(source.record_id)links.append(el('span','source-summary','紀錄 #'+source.record_id));details.append(links);list.append(details);}target.append(list);}
}
function showEdge(edge,map){$('#evidenceTitle').textContent=`MedHop 關係 #${edge.id}`;const box=$('#evidenceContent');box.replaceChildren(el('h3','',edge.source+' → '+edge.target),el('p','muted','以下為既有圖譜的抽取描述，請以支持文本核對。'),el('pre','',edge.description));for(const id of edge.citations||edge.source_ids||[]){const s=map.get(id);if(s)box.append(el('h4','',`[${id}] ${s.title}`),el('pre','',s.full_text));}$('#evidenceDialog').showModal();}

async function submit(event){event?.preventDefault();const text=$('#input').value.trim();if(!text||busy)return;await submitWorkflow(text);}

document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$('#'+b.dataset.close).close()));
$('#editRecord').addEventListener('click',()=>isWorkflow()?resetWorkflowPatient():openRecord());
$('#recordForm').addEventListener('submit',async e=>{e.preventDefault();$('#saveRecord').disabled=true;try{record=await api('/api/record',{text:$('#recordText').value,source:$('#recordSource').value});draft=null;renderRecord();$('#recordDialog').close();toast('病歷已確認並儲存');}catch(err){$('#recordError').textContent=err.message;}finally{$('#saveRecord').disabled=false;}});
$('#input').addEventListener('input',()=>{$('#send').disabled=busy||!$('#input').value.trim();$('#input').style.height='auto';$('#input').style.height=Math.min($('#input').scrollHeight,160)+'px';});
$('#input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();submit();}});
$('#composer').addEventListener('submit',submit);
document.addEventListener('DOMContentLoaded',async()=>{try{boot=await api('/api/bootstrap');record=boot.record;initWorkflow(boot.workflow);modelEnabled=!!boot.model.available;welcome();renderRecord();setBusy(false);}catch(err){log.append(el('div','notice warn','無法載入本機資料：'+err.message));}},{once:true});
