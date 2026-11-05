'use strict';
// pharmacist-agent-20261105: every chat message goes to the pharmacist agent; the model decides which tools to call.
let agentRunId=null;
// The reply is plain text; stray Markdown from the model is flattened rather than rendered.
function plainReply(text){
  return text.split('\n').filter(line=>!/^\s*\|?\s*:?-{3,}/.test(line)).map(line=>{
    line=line.replace(/\*\*|__/g,'').replace(/^\s*#{1,6}\s*/,'');
    return /^\s*\|.*\|\s*$/.test(line)?line.trim().slice(1,-1).split('|').map(cell=>cell.trim()).filter(Boolean).join(' · '):line;
  }).join('\n');
}
function agentReply(target,text,sources){
  const map=new Map((sources||[]).map(s=>[s.id,s]));target.replaceChildren();let last=0;
  for(const m of text.matchAll(/\[([A-Z]\d+)\]/g)){
    target.append(document.createTextNode(text.slice(last,m.index)));
    const source=map.get(m[1]);target.append(source?button(m[0],()=>showSource(source),'cite-link'):document.createTextNode(m[0]));last=m.index+m[0].length;
  }
  target.append(document.createTextNode(text.slice(last)));
}
async function submitAgent(text){
  if(busy||active)return;
  const previousId=currentPatient?.id||null;
  userTurn(text);$('#input').value='';$('#input').style.height='auto';setBusy(true);
  let body=newTurn();const live=el('ol','steps'),controls=el('div','run-actions');
  const stop=button('停止',async()=>{if(!agentRunId)return;stop.disabled=true;try{await api('/api/agent/cancel',{id:agentRunId});}catch(err){toast(err.message);}},'btn btn-quiet');
  controls.append(stop);body.append(live,controls);scrollEnd();
  try{
    agentRunId=(await api('/api/agent',{message:text})).id;let run;
    while(true){run=await api('/api/agent/'+agentRunId);renderSteps(live,run.steps);scrollEnd();if(run.status!=='running')break;await new Promise(r=>setTimeout(r,600));}
    agentRunId=null;controls.remove();
    const patient=run.patient||null;
    // A different patient starts a clean workspace, as before, so one patient's results never sit beside another's.
    if(previousId&&patient&&patient.id!==previousId){clearPatientWorkspace();userTurn(text);body=newTurn();}else (live.closest('.progress-details')||live).remove();
    currentPatient=patient;body.dataset.patientId=patient?.id||'';renderRecord();await refreshPatients();
    const message=plainReply(run.reply||(run.status==='cancelled'?'已停止，這則訊息沒有繼續處理。':''));
    renderWorkflowResponse(body,{...(run.alerts||{}),steps:run.steps,message,candidates:run.patient_candidates||[]});
    // The agent's own steps sit apart from a review's steps, which follow under their own heading.
    // renderSteps folds its list in a microtask; relabel after it.
    queueMicrotask(()=>{const own=body.querySelector('.progress-details > summary');if(own&&run.steps?.length)own.textContent='Agent 執行了 '+run.steps.length+' 個步驟（點選查看）';});
    const say=[...body.querySelectorAll(':scope > .say')].pop();
    if(say&&message)agentReply(say,message,run.sources);
    if(run.error)body.append(el('div','notice warn',run.error));
    for(const action of run.actions||[])await renderAgentAction(body,action);
  }catch(err){controls.remove();body.append(el('div','notice warn',err.message));}
  finally{agentRunId=null;setBusy(false);$('#input').focus();scrollEnd();}
}
async function renderAgentAction(body,action){
  if(action.type==='review')return followReview(body,action.job);
  if(action.type==='quick_check')return renderQuickCheck(body,action);
  if(action.type==='drug_document'){body.append(drugPreview(action.document,body));return;}
  if(action.type==='drug_candidates'){
    const list=el('div','candidate-list drug-candidates');
    for(const c of action.candidates)list.append(button(`${c.name||c.name_en} · ${c.license}${c.ingredient?' · '+c.ingredient:''}`,()=>fetchDrugCandidate(c.license,list),'btn btn-quiet'));
    body.append(list,el('p','muted drug-note','候選來自本機的食藥署未註銷許可證快照；請依實際產品選擇，不同廠牌的仿單內容可能不同。'));
  }
}
async function renderQuickCheck(body,action){
  const job=await api('/api/jobs/'+action.job),box=el('section','quick-check');
  box.append(el('h3','quick-check-title','併用試算 · 未寫入病歷'),el('p','muted',action.with_patient?'以目前病人的用藥與條件，加上詢問的藥品試算；病歷沒有任何變更。':'沒有指定病人，只檢查詢問的藥品之間；沒有考慮病人條件。'));
  const assessment=el('div');renderAssessment(assessment,{...job.assessment,display_answer:job.answer},job.sources);box.append(assessment);
  const all=el('details','all-evidence');all.append(el('summary','','查看仿單原文與 MedHop 機轉支持文本'));const content=el('div');renderEvidence(content,job);all.append(content);box.append(all);
  if(action.with_patient&&currentPatient)box.append(button('把這些藥加入病歷並正式檢查',()=>submitAgent('把 '+action.drugs.join('、')+' 加入目前病人的用藥，並重新檢查'),'btn btn-quiet quick-check-add'));
  body.append(box);scrollEnd();
}
