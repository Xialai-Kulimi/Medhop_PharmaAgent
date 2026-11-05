'use strict';
// drug-knowledge-20261103: preview cards and product candidates for drug data the pharmacist agent stages; nothing is saved before the pharmacist confirms.
function drugSteps(result,first){return [first,...result.steps];}
function renderDrugResult(body,steps,result,first){
  renderSteps(steps,drugSteps(result,first));body.append(el('div','say',result.message));
  if(result.candidates?.length){
    const list=el('div','candidate-list drug-candidates');
    for(const c of result.candidates){const pick=button(`${c.name||c.name_en} · ${c.license}${c.ingredient?' · '+c.ingredient:''}`,()=>fetchDrugCandidate(c.license,list),'btn btn-quiet');list.append(pick);}
    body.append(list,el('p','muted drug-note','候選來自本機的食藥署未註銷許可證快照；請依實際產品選擇，不同廠牌的仿單內容可能不同。'));
  }
  if(result.document)body.append(drugPreview(result.document,body));
  scrollEnd();
}
async function fetchDrugCandidate(license,list){
  if(busy||active)return;
  list.querySelectorAll('button').forEach(b=>b.disabled=true);setBusy(true);
  const body=newTurn(),steps=el('ol','steps'),first={title:'藥師選擇實際產品',by:'藥師',result:license,state:'done'};body.append(steps);
  renderSteps(steps,[first,{title:'取得食藥署電子仿單',by:'fetch_tfda_insert',result:'連線中',state:'running'}]);
  try{renderDrugResult(body,steps,await api('/api/drug-knowledge/fetch',{license}),first);}
  catch(err){renderSteps(steps,[first,{title:'取得食藥署電子仿單',by:'fetch_tfda_insert',result:'未完成',state:'error'}]);body.append(el('div','notice warn',err.message));list.querySelectorAll('button').forEach(b=>b.disabled=false);}
  finally{setBusy(false);scrollEnd();}
}
function drugPreview(doc,body){
  const card=el('section','drug-preview'+(doc.kind==='pasted'?' is-pasted':''));
  card.append(el('h3','','藥品資料預覽 · 待藥師確認'));
  card.append(el('p','drug-source',doc.kind==='tfda'?`食藥署電子仿單 · ${doc.license} · 版本 ${doc.version||'未標示'} · 核准日 ${doc.approved||'未標示'}`:'藥師貼上 · 未經官方核對'));
  let nameInput=null;
  if(doc.kind==='tfda'){const name=el('p','drug-name-line');name.append(el('b','',doc.name),document.createTextNode(doc.name_en?'　'+doc.name_en:''));card.append(name);}
  else{const label=el('label','control-label editor-field');nameInput=editorInput(doc.name,{maxLength:80,required:true,placeholder:'例如：Clopidogrel'});label.append(el('span','','藥名（系統依藥名把這份資料納入檢索）'),nameInput);card.append(label);}
  card.append(el('p','muted',`${doc.sections.length} 段 · ${doc.characters.toLocaleString()} 字${doc.category?' · '+doc.category:''}`));
  if(doc.warning)card.append(el('p','drug-warning',doc.warning));
  const sections=el('details','drug-sections');sections.append(el('summary','',`查看段落原文（${doc.sections.length} 段）`));
  for(const s of doc.sections){const d=el('details','drug-section level-'+Math.min(s.level,3));d.append(el('summary','',s.title),el('div','drug-section-text',s.text||'（此段無文字）'));sections.append(d);}
  card.append(sections);
  card.append(el('p','muted drug-note',doc.kind==='tfda'?'內容取自食藥署電子仿單平台，未改寫。寫入後加入仿單檢索，引用時標示來源、版本與核准日。':'貼上內容原文照存，不改寫。寫入後只作為待藥師判讀的來源，系統不會據此自動判定交互作用。'));
  const actions=el('div','recommendation-actions'),error=el('p','form-error');
  if(doc.page_url){const a=el('a','btn btn-quiet','在食藥署網站核對 ↗');a.href=doc.page_url;a.target='_blank';a.rel='noopener';actions.append(a);}
  const save=button('寫入藥品資料庫',async()=>{
    save.disabled=skip.disabled=true;error.textContent='';
    try{const result=await api('/api/drug-knowledge/save',{id:doc.id,...(nameInput?{name:nameInput.value}:{})});
      actions.replaceChildren(el('p','drug-saved',result.message));if(nameInput)nameInput.disabled=true;toast('已寫入藥品資料庫');
      if(currentPatient?.medications.some(m=>m.status==='active'))actions.append(recheckButton(body,'依新資料重新檢查'));}
    catch(err){error.textContent=err.message;save.disabled=skip.disabled=false;}
  },'btn btn-primary');
  const skip=button('略過',async()=>{save.disabled=skip.disabled=true;try{const result=await api('/api/drug-knowledge/discard',{id:doc.id});actions.replaceChildren(el('p','muted',result.message));}catch(err){error.textContent=err.message;save.disabled=skip.disabled=false;}},'btn btn-quiet');
  actions.append(save,skip);card.append(actions,error);
  return card;
}
