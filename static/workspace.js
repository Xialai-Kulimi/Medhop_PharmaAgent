'use strict';
// MedHop workspace: only real execution steps and source-grounded evidence are shown.
workflowWelcome=function(){const welcome=el('article','turn welcome-message');welcome.append(meta('藥你安心：藥事安全Agent'),el('p','intro-copy','您好，我是藥師 Agent。用平常的說法交代我就可以，例如：\n・新病人王小明，68 歲男性，目前吃 Clopidogrel 75mg QD、Omeprazole 20mg QD\n・Warfarin 和 Aspirin 可以一起吃嗎？\n・查普拿疼的官方仿單\n我會自己判斷要建立病歷、補充用藥、試算併用或查藥品資料；所有結果都待藥師確認。'));log.append(welcome);};
newTurn=function(){const turn=el('article','turn'),body=el('div','turn-body');turn.append(meta('藥你安心：藥事安全Agent'),body);log.append(turn);return body;};
userTurn=function(text){const turn=el('article','msg-user');turn.append(meta('藥師'),el('div','bubble',text));log.append(turn);scrollEnd();};
const TOOL_NAMES={consult_pharmacy_agent:'委派藥事 Agent',read_patient_medications:'讀取病人條件與藥單',retrieve_pharmacy_evidence:'查詢藥事仿單',retrieve_medhop_mechanisms:'MedHop 機轉檢索'};
renderSteps=function(target,steps){target.replaceChildren();for(const s of steps){const row=el('li','step '+s.state),content=el('div','step-row');content.append(el('span','step-title',TOOL_NAMES[s.title]||s.title),el('span','by',s.by),el('span','res',s.result||''));row.append(el('span','dot',s.state==='done'?'✓':s.state==='running'?'⋯':''),content);target.append(row);}};
function recSection(title,children){const section=el('section','rec-sec');section.append(el('h3','',title),children);return section;}
function interventionView(action){
  if(action.kind==='counseling')return {...action,heading:'用藥衛教',description:[action.advice,action.education].filter(Boolean).join('\n')};
  let payload;try{payload=JSON.parse(action.note);}catch{}
  if(payload?.kind==='counseling')return {...action,kind:'counseling',advice:payload.advice,education:payload.education,heading:'用藥衛教',description:[payload.advice,payload.education].filter(Boolean).join('\n')};
  return {...action,heading:action.kind==='prescription_change'||action.action==='adjusted'?'處方變更':action.action==='contact_prescriber'?'聯繫開方醫師':'用藥核對',description:action.note||''};
}
function patientInterventions(patient){return (patient.interventions||patient.actions||[]).map(interventionView).sort((a,b)=>new Date(a.created)-new Date(b.created));}
function medRecordRows(meds){
  const rows=el('div','record-med-rows');
  for(const med of meds){
    const row=el('div','record-med-row'+(med.status==='stopped'?' is-stopped':''));row.dataset.medId=med.id;
    row.append(el('b','record-med-name',med.name),el('span','cell-dim record-med-dose',med.dose||'規格未提供'),el('span','cell-dim record-med-frequency',med.frequency||'頻次未提供'));
    const status=med.status==='stopped'?(confirmedReplacementNames(currentPatient).has(med.name.normalize('NFKC').trim().toLowerCase())?'已更換':'已停用'):med.confirmation==='confirmed'?'已確認':med.kind==='new_order'?'待藥師確認':med.kind==='reported'||med.acquisition==='self_purchased'?'病人自述':'';
    if(status)row.append(el('span','status-tag'+(med.confirmation==='confirmed'?' confirmed':''),status));
    if(med.components?.length)row.append(el('small','cell-dim record-med-components','成分：'+med.components.join(' ＋ ')));rows.append(row);
  }return rows;
}
function readableRecordBody(body){
  try{const value=JSON.parse(body);if(value&&typeof value==='object')return JSON.stringify(value,null,2);}catch{}
  return body||'';
}
function patientTimeline(patient,interventions){
  const items=[],events=patient.events||[];
  items.push({created:patient.created,title:'建立病歷',actor:'系統建檔',detail:'病歷號：'+patient.id,key:'created'});
  events.forEach((event,index)=>{
    let title=event.source||'病歷資料更新';
    if(index===0&&event.created===patient.created)title='初次病人資料與用藥';
    else if(title==='藥師已確認處方變更')title='處方變更';
    else if(title==='藥師輸入')title=/自己買|自行購買|自購/.test(event.body)?'新增自述用藥':/新增處方|醫師新增|今天.*開/.test(event.body)?'新增處方':/服用|目前用藥|用法|藥袋/.test(event.body)?'補充用藥資料':'病歷資料補充';
    items.push({created:event.created,title,actor:(event.source||'').includes('藥師已確認')?'藥師確認':(event.source||'').includes('藥師')?'藥師輸入':'系統紀錄',detail:readableRecordBody(event.body),key:'event-'+event.id});
  });
  for(const intervention of interventions){
    // Prescription changes already have a full patient_event with the actual old/new drugs.
    if(intervention.kind==='prescription_change')continue;
    items.push({created:intervention.created,title:intervention.heading,actor:intervention.actor||'藥師紀錄',detail:intervention.description,review:intervention.review_id,key:'intervention-'+intervention.id});
  }
  for(const review of [...(patient.reviews||[])].reverse())items.push({created:review.created,title:'用藥檢查',actor:'系統檢索',detail:'用藥版本 '+review.version,review:review.id,key:'review-'+review.id});
  for(const query of [...(patient.queries||[])].reverse())items.push({created:query.created,title:'研究資料查詢',actor:'系統檢索',detail:'用藥版本 '+query.version,query:query.id,key:'query-'+query.id});
  const represented=new Set(['patient_created','patient_updated','patient_found','disposition','confirmed_prescription_change','review_completed','research_query_saved','patient_imported','patient_record_edited','medication_edited','medication_status_changed']);
  const names={medication_classification:'藥品分類核對',medication_identity:'研究藥名核對',report_draft_saved:'既有說明草稿儲存',report_approved:'既有交付覆核',patient_exported:'匯出病歷 JSON'};
  for(const audit of patient.audit_events||[]){if(represented.has(audit.event))continue;items.push({created:audit.created,title:names[audit.event]||'操作紀錄：'+audit.event,actor:'操作紀錄',detail:JSON.stringify(audit.detail||{},null,2),key:'audit-'+audit.id});}
  return items.sort((a,b)=>new Date(a.created)-new Date(b.created));
}
renderPatientRecord=function(){
  const box=$('#recScroll'),oldScroll=box.scrollTop;box.replaceChildren();$('#patientPicker').hidden=false;$('#editRecord').textContent='新增病例';
  $('#recordState').textContent=currentPatient?'更新於 '+new Date(currentPatient.updated).toLocaleTimeString('zh-TW',{timeZone:'Asia/Taipei',hour12:false,hour:'2-digit',minute:'2-digit'}):'';
  if(!currentPatient){const empty=el('div','empty-state');empty.append(el('h3','','尚未開啟病歷'),el('p','','在左側描述病人狀況。\n系統會先查詢病歷，查無紀錄時新增。'));box.append(empty);return;}
  const patient=currentPatient,head=el('div','rec-head'),title=el('h2','rec-name',patient.name||'姓名未提供');title.append(el('span','rec-id',patient.id));head.append(title);
  let allergy='未記錄';for(const event of patient.events||[]){const match=event.body.match(/過敏史[：:]\s*([^\n。]+)/);if(match)allergy=match[1];}
  const fields=el('dl','rec-fields');for(const [key,value] of [['性別',patient.sex||'未確認'],['年齡',patient.age!=null?patient.age+' 歲':'未確認'],['建檔',new Date(patient.created).toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'})],['過敏',allergy]]){const field=el('div');field.append(el('dt','',key),el('dd','',value));fields.append(field);}head.append(fields);box.append(head);
  if(patient.sex!=='男'){const conditions=el('div','population-context');conditions.append(el('p','',`懷孕狀態：${PREGNANCY_LABELS[patient.pregnancy]||'未確認'}${patient.pregnancy==='pregnant'&&patient.pregnancy_weeks!=null?' · '+patient.pregnancy_weeks+'週':''}`));box.append(recSection('病人條件',conditions));}
  const history=el('div','history-rows');for(const fact of patient.history||[])history.append(el('p','',fact));if(!history.childElementCount)history.append(el('p','muted','尚未提供病史'));box.append(recSection('病史',history));
  const all=(patient.medications||[]).filter(med=>med.status!=='merged').sort((a,b)=>a.id-b.id);
  const baseline=all.filter(m=>m.status==='active'&&m.kind!=='new_order'&&m.kind!=='reported'&&m.acquisition!=='self_purchased');
  const prescriptions=all.filter(m=>m.kind==='new_order'&&m.acquisition!=='self_purchased');
  const reported=all.filter(m=>m.kind==='reported'||m.acquisition==='self_purchased');
  const otherStopped=all.filter(m=>m.status==='stopped'&&!prescriptions.includes(m)&&!reported.includes(m));
  if(baseline.length)box.append(recSection('長期／目前用藥',medRecordRows(baseline)));
  if(prescriptions.length)box.append(recSection('新增處方與變更',medRecordRows(prescriptions)));
  if(reported.length)box.append(recSection('自行購買／自述用藥',medRecordRows(reported)));
  if(otherStopped.length)box.append(recSection('已停用／已更換用藥',medRecordRows(otherStopped)));
  if(!all.length)box.append(recSection('用藥',el('p','muted','尚未提供，請在對話中補充。')));

  const interventions=patientInterventions(patient),interventionRows=el('div','intervention-rows');
  interventions.forEach((item,index)=>{
    const row=el('article','clinical-intervention'+(index===interventions.length-1?' latest':'')),line=el('div','intervention-title');
    const time=el('time','',formatTime(item.created));time.dateTime=item.created;line.append(time,el('b','',item.heading));if(index===interventions.length-1)line.append(el('span','change-label','最新'));row.append(line);
    if(item.kind==='counseling'){const facts=el('dl','intervention-facts');for(const [label,text] of [['建議',item.advice],['衛教',item.education]])if(text){facts.append(el('dt','',label),el('dd','',text));}row.append(facts);}
    else row.append(el('p','',item.description));
    if(item.review_id)row.append(button('查看相關檢查',()=>loadPastReview(item.review_id),'text-btn'));interventionRows.append(row);
  });
  if(!interventions.length)interventionRows.append(el('p','muted','尚無已確認的用藥／衛教紀錄。'));
  box.append(recSection('藥事介入紀錄',interventionRows));

  const timeline=el('ol','record-timeline'),items=patientTimeline(patient,interventions);
  for(const item of items){const row=el('li','record-timeline-row'),time=el('time','',formatTime(item.created));time.dateTime=item.created;
    const details=el('details','timeline-details');details.append(el('summary','',item.title));if(item.detail)details.append(el('p','timeline-detail-text',item.detail));
    if(item.review)details.append(button('查看檢查結果',()=>loadPastReview(item.review),'text-btn'));
    if(item.query)details.append(button('查看研究查詢',()=>showQueryHistory(item.query),'text-btn'));
    row.append(time,details,el('span','timeline-actor',item.actor));timeline.append(row);
  }const timelineSection=recSection('異動紀錄',timeline);timelineSection.querySelector('h3').append(el('small','history-count','全部 '+items.length+' 筆'));box.append(timelineSection);

  const activeMeds=(patient.medications||[]).filter(m=>m.status==='active').sort((a,b)=>a.id-b.id);
  const tools=el('details','record-tools');tools.append(el('summary','','藥品身分、分類與來源核對'));for(const med of activeMeds){const row=el('div','record-tool-row');row.append(el('b','',med.name),button('藥物說明',()=>showMedicationDetails(med)),button('核對實際產品／分類',()=>openMedicationClassification(med)),button('核對MedHop識別碼',()=>openResearchIdentity(med)));tools.append(row);}box.append(tools);
  if(patient.reviews?.length){const reviews=el('details','record-tools');reviews.append(el('summary','','歷次檢查結果 · 全部 '+patient.reviews.length+' 筆'));const list=el('ul','review-history-list');for(const review of patient.reviews){const row=el('li','review-history-item'),time=el('time','',formatTime(review.created));time.dateTime=review.created;row.append(time,button('查看檢查結果 · 版本 '+review.version,()=>loadPastReview(review.id),'review-history-open'));list.append(row);}reviews.append(list);box.append(reviews);}
  box.scrollTop=oldScroll;
};

function sourceButtons(ids,map,parent){const refs=el('div','source-chips');for(const id of ids||[]){const s=map.get(id);if(s)refs.append(button('['+id+'] '+s.title,()=>showSource(s),'source-chip'));}parent.append(refs);}
function researchPathKey(path){return JSON.stringify([path.nodes||[],(path.edges||[]).map(edge=>[String(edge.id),edge.source,edge.target])]);}
function distinctResearchPaths(paths){const seen=new Set();return (paths||[]).filter(path=>{const key=researchPathKey(path);if(seen.has(key))return false;seen.add(key);return true;});}
function researchNodeLabel(node,pair){return (pair.medhop?.identities||[]).find(item=>item.entity===node)?.input||node;}
function sourceExcerpt(source,edge){
  const text=String(source.text||source.full_text||'');if(!text)return '此來源沒有可顯示的片段，請開啟原文核對。';
  const positions=[edge.source,edge.target].map(name=>text.toLowerCase().indexOf(name.toLowerCase())).filter(pos=>pos>=0);
  const start=Math.max(0,(positions.length?Math.min(...positions):0)-55),end=Math.min(text.length,start+260);
  return (start?'…':'')+text.slice(start,end).trim()+(end<text.length?'…':'');
}
function mechanismCard(paths,pair,map){
  const card=el('section','mechanism-card compact-mechanism'),head=el('div','mechanism-heading'),title=el('div');
  title.append(el('h4','','MedHop 研究路徑'),el('p','mechanism-pair',pair.drugs.join(' × ')));
  head.append(title,el('span','research-state','研究線索 · 待核對'));card.append(head);
  const unique=distinctResearchPaths(paths),content=el('div','mechanism-content');
  if(unique.length>1){
    const label=el('label','path-picker-label');label.append(el('span','','查看路徑'));
    const select=el('select','path-picker');select.setAttribute('aria-label',pair.drugs.join('與')+'的研究路徑');
    unique.forEach((path,index)=>{const option=el('option','',`${index+1}／${unique.length} · ${path.nodes.map(node=>researchNodeLabel(node,pair)).join(' ↔ ')}`);option.value=String(index);select.append(option);});
    select.addEventListener('change',()=>draw(unique[Number(select.value)]));label.append(select);card.append(label);
  }
  card.append(content);
  function draw(path){
    content.replaceChildren();const chain=el('div','mechanism-chain');
    path.nodes.forEach((node,index)=>{
      const label=researchNodeLabel(node,pair),nodeBox=el('div','mechanism-node'+(index>0&&index<path.nodes.length-1?' mediator':''));
      nodeBox.append(el('b','',label),el('small','',label===node?'研究實體':node));chain.append(nodeBox);
      if(index<path.edges.length){
        const edge=path.edges[index],arrow=el('div','mechanism-arrow');
        const backwards=edge.source!==path.nodes[index];
        arrow.append(el('small','',`原圖關係 ${index+1}`),el('span','',backwards?'←':'→'));chain.append(arrow);
      }
    });content.append(chain);
    const evidence=el('div','path-evidence'),entries=[];
    path.edges.forEach((edge,index)=>{
      const ids=edge.source_ids||edge.citations||[],available=ids.map(id=>map.get(id)).filter(Boolean),source=available[0];
      if(!source){const row=el('div','path-source-line');row.append(el('span','source-number',String(index+1)),el('p','coverage-gap','此條連線沒有可開啟的支持來源，需重新核對。'));evidence.append(row);return;}
      const signature=source.unit_id||source.id,existing=entries.find(entry=>entry.signature===signature);
      if(existing){existing.numbers.push(index+1);existing.ids=[...new Set([...existing.ids,...ids])];return;}
      entries.push({signature,numbers:[index+1],source,edge,ids});
    });
    for(const entry of entries){const row=el('div','path-source-line'),text=el('div');
      text.append(el('p','path-source-quote',sourceExcerpt(entry.source,entry.edge)));
      const controls=el('div','path-source-actions');controls.append(button('['+entry.source.id+'] 查看完整原文',()=>showSource(entry.source),'text-btn'));
      const extra=entry.ids.filter(id=>id!==entry.source.id&&map.has(id));if(extra.length){const details=el('details','additional-edge-sources');details.append(el('summary','',`另有 ${extra.length} 筆來源`));sourceButtons(extra,map,details);controls.append(details);}
      text.append(controls);row.append(el('span','source-number',entry.numbers.map(number=>['①','②','③','④','⑤'][number-1]||String(number)).join('')),text);evidence.append(row);
    }content.append(evidence);
    const details=el('details','path-audit');details.append(el('summary','','檢視各邊方向與圖譜描述'));
    path.edges.forEach((edge,index)=>{const row=el('div','path-audit-row');row.append(el('b','',`${index+1}. ${edge.source} → ${edge.target} · #${edge.id}`),el('p','',edge.description||'原圖未提供描述'));
      if(edge.source!==path.nodes[index])row.append(el('p','muted','此步反向檢索，箭頭仍保留原圖方向。'));
      if(edge.qualified_text)row.append(el('p','muted','原文含否定或限制語句，請核對研究條件。'));
      sourceButtons(edge.source_ids||edge.citations,map,row);details.append(row);
    });content.append(details);
  }
  if(unique.length)draw(unique[0]);
  card.append(el('p','research-disclaimer','研究路徑與共同標靶不等於已證實的臨床交互作用；不同路徑的排列不代表風險或信心排名。'));
  return card;
}
function compactRiskKey(item){return item.drugs?.length?'pair:'+item.drugs.map(name=>String(name).toLowerCase()).sort().join('|'):'population:'+(item.drug||'')+'|'+(item.condition||'')+'|'+(item.rule_id||item.message||'');}
renderAssessment=function(target,result,sources){
  target.replaceChildren();target.classList.add('compact-assessment');
  const pairs=result.pairs||[],medications=result.medications||[],map=new Map((sources||[]).map(source=>[source.id,source]));
  // Keep the medication table in the pharmacist's original order.
  const overview=el('section','drug-overview');overview.append(el('h3','','藥品資料與來源'));
  const table=el('table','drug-table'),thead=el('thead'),head=el('tr');
  ['藥品','記錄規格／頻次','MedHop 對應','來源'].forEach(label=>head.append(el('th','',label)));thead.append(head);table.append(thead);
  const tbody=el('tbody');
  for(const med of medications){const mapping=pairs.flatMap(pair=>pair.medhop?.identities||[]).find(item=>item.input===med.name),row=el('tr');
    const name=el('td','drug-name',med.name);if(med.components?.length)name.append(el('small','drug-components','成分：'+med.components.join(' ＋ ')));
    row.append(name,el('td','',`${med.dose||'規格未提供'} · ${med.frequency||'頻次未提供'}`),el('td','',mapping?.entity||'未對應'));
    const cell=el('td');cell.append(button('藥物說明',()=>showMedicationDetails(med),'source-chip'));row.append(cell);tbody.append(row);
  }table.append(tbody);const scroll=el('div','table-scroll');scroll.append(table);overview.append(scroll);target.append(overview);

  const known=[],seen=new Set(),coveredPairs=new Set();
  for(const item of [...(result.population_checks||[]).filter(item=>['contraindicated','condition_unconfirmed'].includes(item.status)),...(result.interaction_alerts||[])]){
    const key=compactRiskKey(item)+'|'+(item.rule_id||item.message||item.quote||item.status);if(seen.has(key))continue;seen.add(key);known.push(item);if(item.drugs?.length)coveredPairs.add(compactRiskKey(item));
  }
  for(const pair of pairs.filter(pair=>['avoid','interaction'].includes(pair.status))){const key=compactRiskKey(pair);if(coveredPairs.has(key))continue;coveredPairs.add(key);known.push({...pair,message:PAIR_LABELS[pair.status],scope:'仿單併用影響；請核對實際產品與病人條件。'});}
  const summary=el('section','clinical-summary compact-clinical-summary'+(known.some(item=>item.status!=='condition_unconfirmed')?' has-risk':''));
  summary.append(el('h3','',known.length?'用藥風險提醒':'調劑核對摘要'));
  if(!known.length)summary.append(el('p','',pairs.length?`已整理 ${pairs.length} 組配對；請核對下方來源與缺口，資料不足不能視為沒有交互作用。`:'已整理單一用藥資料；仍須核對產品與病人條件。'));
  const warningGroups=new Map();for(const item of known){const key=compactRiskKey(item);if(!warningGroups.has(key))warningGroups.set(key,[]);warningGroups.get(key).push(item);}
  for(const group of warningGroups.values()){
    const first=group[0],row=el('section','compact-warning '+(group.every(item=>item.status==='condition_unconfirmed')?'is-pending':'is-risk'));row.setAttribute('role','alert');
    if(first.drugs?.length)row.append(el('h4','',first.drugs.join(' × ')));
    else if(first.drug)row.append(el('h4','',first.drug+' · '+(first.status==='contraindicated'?'病人條件禁忌':'病人條件待確認')));
    for(const item of group){
      row.append(el('p','warning-message',item.message||PAIR_LABELS[item.status]||'請核對來源'));
      const details=el('details','warning-source');details.append(el('summary','','仿單依據與適用範圍'));
      if(item.quote)details.append(el('blockquote','',item.quote));sourceButtons(item.source_ids,map,details);
      if(item.scope)details.append(el('p','muted',item.scope));row.append(details);
    }summary.append(row);
  }target.append(summary);
  // Remove only the instant warning cards superseded by the final assessment.
  if(known.length)target.closest('.turn-body')?.querySelectorAll('.instant-interaction-warning,.instant-population-warning').forEach(card=>card.remove());

  if(pairs.length){const research=el('section','research-section');research.append(el('h3','','MedHop 研究機轉'));
    let count=0;const renderedPairs=new Set();
    for(const pair of pairs){const paths=distinctResearchPaths(pair.medhop?.paths),key=pair.drugs.map(name=>name.toLowerCase()).sort().join('|');
      if(paths.length&&!renderedPairs.has(key)){research.append(mechanismCard(paths,pair,map));renderedPairs.add(key);count++;}
    }
    if(!count)research.append(el('p','muted','這份藥單未取得可逐邊引用的研究路徑；請查看名稱對應與來源缺口。'));
    const unmapped=[...new Set(pairs.flatMap(pair=>(pair.medhop?.identities||[]).filter(identity=>!identity.entity).map(identity=>identity.input)))];
    if(unmapped.length)research.append(el('p','coverage-gap','尚未對應的研究藥名：'+unmapped.join('、')));
    const coverage=el('details','coverage-disclosure');coverage.append(el('summary','','查詢研究資料涵蓋範圍'),button('查看可檢索的藥名與路徑',showMedhopCoverage,'source-chip'));research.append(coverage);target.append(research);
  }

  const synthesis=el('section','compact-synthesis');synthesis.append(el('h3','','彙整建議'));
  const urgent=known.some(item=>['interaction','avoid','contraindicated'].includes(item.status));
  synthesis.append(el('p',urgent?'clinical-attention':'',urgent?'來源提示用藥風險，請在調劑前聯繫開方醫師，確認實際處置。':'請先核對病人條件、實際產品與可用來源，再由藥師確認調劑方式。'));
  synthesis.append(el('p','muted','系統不自動更換藥物或劑量；下方可記錄已完成的核對或醫師確認結果。'));
  if(result.display_answer){const modelText=el('details','original-assessment-answer');modelText.append(el('summary','','檢視本次完整文字摘要'),el('p','say',result.display_answer));synthesis.append(modelText);}
  const missing=medications.filter(m=>!m.dose||!m.frequency);if(missing.length)synthesis.append(el('p','clinical-attention','需補充規格／實際用量或頻次：'+[...new Set(missing.map(m=>m.name))].join('、')+'。'));
  for(const duplicate of result.duplicates||[])synthesis.append(el('p','clinical-attention',`${duplicate.name} 有多筆用法，請核對：${duplicate.entries.join('；')}`));
  target.append(synthesis);

  const gaps=el('details','assessment-details');gaps.append(el('summary','',`逐對核對與資料缺口 · ${pairs.length} 組`));
  for(const pair of pairs){const card=el('section','evidence-pair');card.append(el('b','',pair.drugs.join(' × ')),el('p','',PAIR_LABELS[pair.status]||pair.status));
    sourceButtons(pair.source_ids,map,card);if(pair.medhop){card.append(el('p','',pair.medhop.message||''));for(const identity of pair.medhop.identities||[])card.append(el('p','mapping-note',identity.input+' → '+(identity.entity||'未對應')));}gaps.append(card);
  }
  if(result.pharmacy_rag){const rag=result.pharmacy_rag;gaps.append(el('p','reader-note',`仿單檢索：RAG／BM25 · ${(rag.source_ids||[]).length} 筆原文。檢索命中不等於交互作用成立。`));}
  if(pairs.length||result.pharmacy_rag)target.append(gaps);
};

async function showMedicationDetails(med){
  const dialog=$('#evidenceDialog'),host=$('#evidenceContent');$('#evidenceTitle').textContent='藥物說明';
  const activeMeds=(currentPatient?.medications||[]).filter(m=>m.status==='active');
  const meds=activeMeds.some(m=>m.id===med.id)?activeMeds:[med];
  let selected=Math.max(0,meds.findIndex(m=>m.id===med.id)),request=0;
  const shell=el('div','medication-reader'),sidebar=el('nav','medication-order'),content=el('div','medication-reading-pane'),footer=el('div','medication-reader-footer');
  sidebar.setAttribute('aria-label','依藥單順序的藥物說明');sidebar.append(el('p','reader-label','藥單順序'));
  const tabs=meds.map((item,index)=>{const tab=button(`${index+1}. ${item.name}`,()=>load(index),'medication-tab');sidebar.append(tab);return tab;});
  const previous=button('上一項',()=>load(selected-1),'btn btn-quiet'),next=button('下一項',()=>load(selected+1),'btn btn-quiet'),position=el('span','muted');footer.append(previous,position,next);
  const body=el('div','medication-reader-body');body.append(content,footer);shell.append(sidebar,body);host.replaceChildren(shell);if(!dialog.open)dialog.showModal();
  function fact(parent,label,value,cls=''){const row=el('div','drug-fact '+cls);row.append(el('b','',label),el('p','',value));parent.append(row);}
  function fullText(title,text){const details=el('details','drug-original');details.append(el('summary','',title));for(const part of (text||'原始來源未提供').split(/(?<=[。；;])\s*|\n+/).filter(Boolean))details.append(el('p','dose-paragraph'+(/危險|不可|禁止|禁忌|出血/.test(part)?' important-source':''),part));return details;}
  function productCard(product,item,confirmed){
    const card=el('section','product-reference');if(product['英文品名'])card.append(el('p','product-english',product['英文品名']));
    fact(card,'產品身分',confirmed?'藥師已核對實際產品':'名稱／成分候選，尚未確認為病人實際產品');
    const facts=el('div','product-facts');for(const [label,value] of [['主成分',product['主成分略述']],['劑型',product['劑型']],['藥品類別（原始欄位）',product['藥品類別']||product.source_availability?.classification.message]])fact(facts,label,value||'來源未提供');card.append(facts);
    card.append(fullText('適應症原文',product['適應症']));
    if(product.dosage_available)card.append(fullText('服用次數與劑量原文',product['用法用量']));else fact(card,'官方用法用量',product['用法用量']||'來源未提供；需核對實際仿單');
    if(product.source_availability)card.append(catalogSourceAvailability(product));
    if(!confirmed)card.append(button('核對實際產品',()=>openMedicationClassification({...item,license:product['許可證字號']}),'source-chip'));
    if(product['許可證字號'])card.append(tfdaInsertLink(product['許可證字號']));
    return card;
  }
  async function load(index){
    if(index<0||index>=meds.length)return;selected=index;const token=++request,item=meds[index];
    tabs.forEach((tab,i)=>{tab.classList.toggle('selected',i===index);if(i===index)tab.setAttribute('aria-current','true');else tab.removeAttribute('aria-current');});
    previous.disabled=index===0;next.disabled=index===meds.length-1;position.textContent=`${index+1} / ${meds.length}`;
    content.replaceChildren(el('p','muted','讀取藥物資料中…'));
    try{
      const detail=await api('/api/medications/'+item.id+'/details');if(token!==request)return;
      const title=el('h3','medication-reader-title',`${index+1}. ${detail.name}`),summary=el('section','drug-key-points');summary.append(el('h4','','藥單重點'));
      fact(summary,'服用次數與劑量',`${detail.recorded_dose||'規格／用量未提供'}；${detail.recorded_frequency||'頻次未提供'}`);
      const ingredient=detail.ingredient_lookup||{};fact(summary,'成分（現有資料庫）',ingredient.ingredients?.length?ingredient.ingredients.join(' ＋ '):ingredient.message||'現有資料不足，待核對');if(ingredient.source_field)summary.append(el('p','reader-note',`來源：${ingredient.source_file} · ${ingredient.source_field} · 快照 ${ingredient.snapshot_date}`));
      const population=detail.population_checks||[];fact(summary,'禁止使用對象／禁忌',population.length?population.map(c=>c.quote).join('；'):'現有來源未提供禁忌欄位，需核對實際產品仿單。');for(const check of population.filter(c=>c.status==='contraindicated'))summary.prepend(populationNotice(check,detail.population_sources||[]));
      fact(summary,'實際產品',detail.product?`${detail.product['中文品名']} · ${detail.product['許可證字號']}`:'尚未核對品牌與許可證');
      content.replaceChildren(title,summary,el('p','reader-note','以上用量為藥師輸入紀錄。產品候選的用法不會自動套用為病人處方。'),externalDrugLinks(detail));
      if(detail.pharmacy_rag){const rag=el('details','product-source-list');rag.append(el('summary','',`仿單 RAG 檢索（BM25 · ${detail.pharmacy_rag.sources.length} 筆）`));const map=new Map(detail.pharmacy_rag.sources.map(s=>[s.id,s]));sourceButtons(detail.pharmacy_rag.source_ids,map,rag);if(!detail.pharmacy_rag.sources.length)rag.append(el('p','',detail.pharmacy_rag.scope));content.append(rag);}
      if(detail.product){const section=el('section','confirmed-product');section.append(el('h4','','已核對產品資料'),productCard(detail.product,item,true));content.append(section);}
      else {
        const candidates=el('details','product-source-list');candidates.append(el('summary','',`產品候選與官方原文（${detail.candidates.length} 筆）`),el('p','reader-note',`快照 ${detail.snapshot_date}；依實際包裝核對後再採用。`));
        for(const product of detail.candidates){const candidate=el('details','product-candidate');candidate.append(el('summary','',`${product['中文品名']} · ${product['許可證字號']}`),productCard(product,item,false));candidates.append(candidate);}
        if(!detail.candidates.length)candidates.append(el('p','muted','目前來源沒有可核對的製劑候選，請補充品牌或許可證。'));
        content.append(candidates);
      }
      const sources=el('details','reader-sources');sources.append(el('summary','','資料來源與仿單'));
      for(const reference of detail.pharmacy_references){const a=el('a','source-chip',reference.title+' · '+(reference.label||'原藥事仿單'));if(reference.url)a.href=reference.url;a.target='_blank';a.rel='noopener';sources.append(a,el('p','reader-note',reference.note));}
      const source=el('a','source-chip','官方資料來源');source.href=detail.source_url;source.target='_blank';source.rel='noopener';sources.append(source);content.append(sources);
      content.scrollTop=0;
    }catch(error){if(token===request)content.replaceChildren(el('p','form-error',error.message));}
  }
  await load(selected);
}

function counselingDraft(job){
  const clinical=job.assessment?.clinical_advice;
  if(clinical){
    const actions=clinical.pharmacist_actions||[],education=clinical.patient_education||[];
    const format=items=>items.slice(0,5).map((item,index)=>`${index+1}. ${item.text}`).join('\n');
    return {kind:'counseling',record_id:crypto.randomUUID(),advice:format(actions),education:format(education),
      source_ids:[...new Set([...actions.slice(0,5),...education.slice(0,5)].flatMap(item=>item.source_ids||[]))]};
  }
  return {kind:'counseling',record_id:crypto.randomUUID(),advice:'這筆舊檢查尚無新版的個別化建議；請重新檢查目前用藥。',education:'請先重新檢查，再核對對應病人的衛教內容。',source_ids:[]};
}

renderDisposition=function(target,job){
  renderDispensingCard(target,job);
  const patientId=currentPatient?.id,version=currentPatient?.version,draft=counselingDraft(job);
  const card=el('section','counseling-card'),header=el('div','counseling-card-head'),state=el('span','research-state','衛教 · 待確認');
  header.append(el('h3','','用藥建議與衛教'),state);card.append(header);
  const rows=el('dl','counseling-facts'),advice=el('dd','counseling-advice',draft.advice),education=el('dd','counseling-education',draft.education);
  rows.append(el('dt','','建議'),advice,el('dt','','衛教'),education);card.append(rows);
  const edit=el('details','counseling-edit');edit.append(el('summary','','修改建議或衛教內容'));
  const adviceLabel=el('label');adviceLabel.append(document.createTextNode('建議'));const adviceInput=el('textarea');adviceInput.rows=3;adviceInput.maxLength=1800;adviceInput.value=draft.advice;adviceInput.setAttribute('aria-label','用藥建議內容');adviceLabel.append(adviceInput);
  const educationLabel=el('label');educationLabel.append(document.createTextNode('衛教'));const educationInput=el('textarea');educationInput.rows=2;educationInput.maxLength=1800;educationInput.value=draft.education;educationInput.setAttribute('aria-label','衛教內容');educationLabel.append(educationInput);edit.append(adviceLabel,educationLabel);card.append(edit);
  adviceInput.addEventListener('input',()=>{draft.advice=adviceInput.value;advice.textContent=draft.advice||'尚未填寫';});
  educationInput.addEventListener('input',()=>{draft.education=educationInput.value;education.textContent=draft.education||'尚未填寫';});
  const actions=el('div','counseling-actions'),confirm=button('確認，寫入衛教紀錄',save,'btn btn-primary'),skip=button('略過',()=>{state.textContent='已略過 · 未寫入';confirm.disabled=true;skip.disabled=true;edit.open=false;adviceInput.disabled=true;educationInput.disabled=true;message.textContent='未新增衛教紀錄，用藥狀態保持不變。';},'btn btn-quiet');
  const message=el('p','counseling-feedback');message.setAttribute('role','status');actions.append(confirm,skip);card.append(actions,message);
  async function save(){
    if(currentPatient?.id!==patientId||currentPatient?.version!==version){message.textContent='病人或用藥版本已變更，請重新開啟目前的檢查結果。';return;}
    if(!draft.advice.trim()&&!draft.education.trim()){message.textContent='請保留至少一項實際建議或衛教內容。';return;}
    confirm.disabled=true;skip.disabled=true;adviceInput.disabled=true;educationInput.disabled=true;message.textContent='儲存中…';
    try{
      const response=await api('/api/disposition',{review:job.id,action:'reviewed',note:JSON.stringify(draft)});
      // Never replace another patient's sidebar if the user switched during the request.
      if(currentPatient?.id===patientId){currentPatient=response.patient;renderRecord();}
      state.textContent='已確認 · 已寫入';confirm.textContent='已寫入衛教紀錄';edit.open=false;message.textContent='已保留建議與衛教內容，未直接變更藥物或劑量。';toast('已新增衛教紀錄');
    }catch(error){message.textContent=error.message;confirm.disabled=false;skip.disabled=false;adviceInput.disabled=false;educationInput.disabled=false;}
  }

  const clinical=job.assessment?.clinical_advice;
  if(clinical){const citations=el('div','counseling-citations'),map=new Map([...(job.sources||job.assessment?.sources||[]),...(clinical.sources||[])].map(s=>[s.id,s]));
    for(const id of draft.source_ids){const source=map.get(id);if(source)citations.append(button((source.external?'新增官方來源 · ':'')+'['+id+'] '+source.title,()=>showSource(source),'text-btn'));}card.append(citations);}
  target.append(card);
};

function openConfirmedChange(review){const d=el('dialog','confirmed-change-dialog'),form=el('form'),head=el('div','dialog-head');head.append(el('h2','','記錄已確認處方變更'),button('×',()=>d.close(),'close-btn'));form.append(head,el('p','muted','僅填寫已與醫師確認的實際內容。系統不預填替代藥、劑量或醫師同意結果。'));const label=el('label','control-label');label.append(document.createTextNode('實際變更（分行填寫已停用與新增處方）'));const text=el('textarea');text.rows=5;text.required=true;text.setAttribute('aria-label','已確認處方變更內容');text.placeholder='已停用：原藥名\n新增處方：已確認藥名、規格／用量、頻次';label.append(text);const noteLabel=el('label','control-label');noteLabel.append(document.createTextNode('聯繫與核對紀錄'));const note=el('textarea');note.required=true;note.minLength=4;note.rows=3;note.setAttribute('aria-label','醫師確認依據');noteLabel.append(note);const checkLabel=el('label','approval-check'),check=el('input');check.type='checkbox';check.required=true;checkLabel.append(check,document.createTextNode('我已與醫師確認以上變更，並核對目前病人。'));const save=el('button','btn btn-primary','確認，寫入病歷');save.type='submit';const error=el('p','form-error');form.append(label,noteLabel,checkLabel,save,error);form.addEventListener('submit',async e=>{e.preventDefault();save.disabled=true;try{const result=await api('/api/prescriptions/confirm-change',{review,text:text.value,note:note.value,acknowledged:check.checked});currentPatient=result.patient;renderRecord();d.close();const body=newTurn();body.append(el('p','',result.message),button('依新版本重新檢查',()=>beginPatientReview(body),'btn btn-primary'));}catch(err){error.textContent=err.message;save.disabled=false;}});d.append(form);document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal();}
function populationNotice(check,sources){const map=new Map(sources.map(s=>[s.id,s])),card=el('section','population-notice '+(['contraindicated','interaction'].includes(check.status)?'population-danger':'population-pending'));card.setAttribute('role','alert');card.append(el('h3','',check.status==='contraindicated'?'用藥禁忌警示':'病人條件待確認'),el('p','population-message',check.message),el('blockquote','',check.quote));sourceButtons(check.source_ids,map,card);card.append(el('p','population-source-note',check.scope));return card;}
const sourceWorkflowResponse=renderWorkflowResponse;
renderWorkflowResponse=function(body,result){sourceWorkflowResponse(body,result);for(const alert of result.interaction_alerts||[]){const card=populationNotice(alert,result.interaction_alert_sources||[]);card.querySelector('h3').textContent='交互作用警示';card.classList.add('instant-interaction-warning');body.prepend(card);}for(const alert of result.patient_alerts||[]){const card=populationNotice(alert,result.patient_alert_sources||[]);card.classList.add('instant-population-warning');body.prepend(card);}};

async function showMedhopCoverage(){
  const host=$('#evidenceContent');$('#evidenceTitle').textContent='MedHop 可檢索內容';host.replaceChildren(el('p','muted','讀取本機研究索引…'));$('#evidenceDialog').showModal();
  try{const data=await api('/api/medhop/coverage');host.replaceChildren();const stats=el('p','',`${data.stats.documents} 篇文件、${data.stats.entities} 個實體、${data.stats.relationships} 條關係；${data.searchable_names} 個藥物／識別碼檢索名稱。`);host.append(stats,el('p','muted',data.scope));const search=el('input');search.placeholder='輸入英文藥名或 DB 識別碼';search.setAttribute('aria-label','MedHop 藥名查詢');const results=el('div','coverage-results');let request=0;async function lookup(){const token=++request;if(!search.value.trim()){results.replaceChildren();return;}const found=await api('/api/medhop/coverage?q='+encodeURIComponent(search.value.trim()));if(token!==request)return;results.replaceChildren();for(const item of found.matches)results.append(el('p','',`${item.title}：${item.source_supported_edges} 條具原文支持的直接關係（非臨床判定）`));if(!found.matches.length)results.append(el('p','coverage-gap','沒有相符檢索名稱；不代表所有 DB 識別碼都已排除，仍需來源對照。'));}search.addEventListener('change',()=>lookup().catch(error=>toast(error.message)));const actions=el('div','coverage-search');actions.append(search,button('查詢',()=>lookup().catch(error=>toast(error.message)),'btn btn-quiet'));host.append(actions,results,el('h3','','已核對的研究路徑範例'));for(const example of data.examples){const card=el('div','coverage-path');card.append(el('b','',example.nodes.join(' ↔ ')),el('p','muted',example.scope));host.append(card);}host.append(el('p','muted','藥事仿單與 MedHop 分開保存來源。名稱未對應、仿單缺頁與沒有研究路徑，都是不同的缺口；不等於沒有交互作用。'));}catch(error){host.replaceChildren(el('p','form-error',error.message));}
}

function confirmedReplacementNames(patient){
  const names=new Set();
  for(const event of patient?.events||[]){
    if(event.source!=='藥師已確認處方變更'||!/新增處方[：:]/.test(event.body))continue;
    for(const match of event.body.matchAll(/(?:^|\n)已停用[：:]\s*([^\n]+)/g))names.add(match[1].normalize('NFKC').trim().toLowerCase());
  }return names;
}
function dispensingEvidence(job){
  const a=job.assessment||{},pairs=(a.pairs||[]).filter(p=>['avoid','interaction'].includes(p.status));
  const alerts=[...(a.interaction_alerts||[]),...(a.population_checks||[]).filter(p=>p.status==='contraindicated')];
  const sourceIds=[...new Set([...pairs,...alerts].flatMap(x=>x.source_ids||[]))];
  const alertText=alerts.map(p=>p.message||p.quote).filter(Boolean);
  const pairText=pairs.filter(p=>!alertText.some(t=>p.drugs.every(name=>t.toLowerCase().includes(name.toLowerCase())))).map(p=>p.quote||p.drugs.join(' × ')+'：'+(PAIR_LABELS[p.status]||'待核對'));
  const quotes=[...new Set([...alertText,...pairText].filter(Boolean).map(s=>s.replace(/\s+/g,' ').trim()))];
  return {risk:pairs.length+alerts.length>0,sourceIds,reason:quotes.slice(0,3).map(s=>s.slice(0,400)).join('\n')||'目前來源尚不足以產生具體換藥方案；請先核對實際產品、病人條件及原始仿單。'};
}
function renderDispensingCard(target,job){
  const suggestions=job.assessment?.clinical_advice?.dispensing_suggestions||[];
  if(suggestions.length){for(const proposal of suggestions)renderSingleDispensingCard(target,job,proposal);}
  else renderSingleDispensingCard(target,job,null);
}
function renderSingleDispensingCard(target,job,proposal){
  const patientId=currentPatient?.id,version=currentPatient?.version,meds=(currentPatient?.medications||[]).filter(m=>m.status==='active');
  if(!patientId||!job.id||!meds.length)return;
  if(job.assessment?.patient_id&&job.assessment.patient_id!==patientId){target.append(el('p','notice warn','此檢查不屬於目前病人，請重新選擇病歷。'));return;}
  const evidence=proposal?{risk:true,reason:proposal.reason,sourceIds:proposal.source_ids}:dispensingEvidence(job),card=el('section','dispensing-card'),header=el('div','dispensing-head');
  card.dataset.patientId=patientId;card.dataset.reviewId=job.id;if(proposal)card.dataset.proposalId=proposal.id;
  const state=el('span','research-state','待與醫師確認');header.append(el('h3','','配藥建議'),state);card.append(header);
  const rows=el('dl','dispensing-facts'),summary=el('dd','dispensing-plan',evidence.risk?'與開方醫師討論調整涉風險用藥；替代藥與劑量待確認。':'先補充來源與病人資訊，再由藥師確認是否需要調整。');
  const reasonText=el('dd','dispensing-reason',evidence.reason),refs=el('dd','dispensing-sources');
  const sourceMap=new Map([...(job.sources||job.assessment?.sources||[]),...(job.assessment?.clinical_advice?.sources||[])].map(s=>[s.id,s]));
  function drawSources(ids){refs.replaceChildren();for(const id of ids){const source=sourceMap.get(id);if(source)refs.append(button((source.external?'新增官方來源 · ':'')+'['+id+'] '+source.title,()=>showSource(source),'text-btn'));}if(!refs.childElementCount)refs.append(el('span','muted','目前沒有可直接支持換藥方案的引用，請核對原始仿單。'));}
  drawSources(evidence.sourceIds);
  rows.append(el('dt','','建議'),summary,el('dt','','理由'),reasonText,el('dt','','依據'),refs);card.append(rows);
  const edit=el('details','dispensing-edit');edit.append(el('summary','','填入或修改醫師已確認的調整方案'));
  const fields=el('div','dispensing-fields');
  function field(labelText,input){const label=el('label');label.append(document.createTextNode(labelText),input);fields.append(label);return input;}
  const old=field('調整的原用藥',el('select'));old.setAttribute('aria-label','配藥原用藥');const empty=el('option','','選擇原用藥…');empty.value='';old.append(empty);
  for(const m of meds){const option=el('option','',`${m.name} ${m.dose||'用量未提供'} · ${m.frequency||'頻次未提供'}`);option.value=String(m.id);old.append(option);}
  const newName=field('確認後藥名',el('input'));newName.setAttribute('aria-label','配藥確認後藥名');newName.maxLength=100;
  const dose=field('每次用量',el('input'));dose.setAttribute('aria-label','配藥確認後用量');dose.maxLength=40;dose.placeholder='填入醫師確認的mg或胰島素單位';
  const frequency=field('使用頻次／時間',el('input'));frequency.setAttribute('aria-label','配藥確認後頻次');frequency.maxLength=160;
  const reason=el('textarea');reason.value=evidence.reason;reason.maxLength=1200;reason.rows=3;reason.setAttribute('aria-label','配藥確認理由');field('確認理由／聯繫紀錄',reason);
  edit.append(fields,el('p','muted','替代藥與用量由藥師依實際醫師確認內容填入；引用支持風險說明，不代表已證實這個替代劑量適合病人。'));card.append(edit);
  function selected(){return meds.find(m=>String(m.id)===old.value);}
  const candidateNote=el('p','candidate-dose-note'),choices=proposal?.alternatives||[];
  let candidateSelect=null;
  function preview(){
    const m=selected();summary.replaceChildren();
    if(m&&newName.value.trim())summary.append(document.createTextNode(`${m.name} ${m.dose||''} ${m.frequency||''} → `),el('strong','',`${newName.value.trim()} ${dose.value.trim()||'（用量待確認）'} ${frequency.value.trim()||'（頻次待確認）'}`));
    else summary.textContent=proposal?.title||'目前沒有已核對的單一替代方案；可填入醫師確認的其他藥物。';
    reasonText.textContent=reason.value;
  }
  if(proposal&&choices.length){
    const bar=el('div','candidate-choice'),label=el('label','','替代藥候選');candidateSelect=el('select');candidateSelect.setAttribute('aria-label','替代藥候選');
    choices.forEach((choice,index)=>{const option=el('option','',choice.label||choice.name);option.value=String(index);candidateSelect.append(option);});
    const custom=el('option','','藥師自行輸入其他藥物');custom.value='custom';candidateSelect.append(custom);label.append(candidateSelect);bar.append(label,candidateNote);card.insertBefore(bar,edit);
    old.value=String(proposal.old_med_id);
    function choose(){
      const option=choices[Number(candidateSelect.value)];
      if(candidateSelect.value==='custom'){newName.value='';dose.value='';frequency.value='';edit.open=true;evidence.sourceIds=proposal.source_ids;candidateNote.textContent='自訂方案由藥師另核對；以下原候選來源不代表支持任意替代藥或用量。';}
      else{old.value=String(proposal.old_med_id);newName.value=option.name;dose.value=option.dose||'';frequency.value=option.frequency||'';evidence.sourceIds=option.source_ids||proposal.source_ids;candidateNote.textContent=option.dose_basis||proposal.scope;edit.open=!option.dose||!option.frequency;}
      drawSources(evidence.sourceIds);
      preview();
    }
    candidateSelect.addEventListener('change',choose);choose();
    old.addEventListener('change',()=>{if(old.value!==String(proposal.old_med_id)){candidateSelect.value='custom';newName.value='';dose.value='';frequency.value='';edit.open=true;candidateNote.textContent='已選不同的原用藥，不能套用前一個替代方案；請填入另外核對的實際處置。';preview();}});
    newName.addEventListener('input',()=>{const selectedChoice=choices[Number(candidateSelect.value)];if(candidateSelect.value!=='custom'&&selectedChoice&&newName.value.trim()!==selectedChoice.name){candidateSelect.value='custom';dose.value='';frequency.value='';candidateNote.textContent='已改為藥師自訂藥名；請另核對並填入用量及頻次，不沿用前一藥的劑量。';preview();}});
    if(proposal.required_information?.length)bar.append(el('p','candidate-requirements','核對事項：'+proposal.required_information.join('、')));
  }else candidateNote.textContent='未命中有來源的替代規則，不會自行編造候選；原檢查的風險仍須處理。';
  [old,newName,dose,frequency,reason].forEach(input=>input.addEventListener('input',preview));
  const feedback=el('p','dispensing-feedback');feedback.setAttribute('role','status');const actions=el('div','dispensing-actions');
  const confirm=button('已與醫師確認，寫入病歷',save,'btn btn-primary'),skip=button('略過',()=>{state.textContent='已略過 · 未變更用藥';confirm.disabled=true;skip.disabled=true;edit.open=false;fields.querySelectorAll('input,select,textarea').forEach(f=>f.disabled=true);if(candidateSelect)candidateSelect.disabled=true;feedback.textContent='未更改病歷中的用藥。';},'btn btn-quiet');actions.append(confirm,skip);card.append(actions,feedback);target.append(card);
  async function save(){
    if(busy||active){feedback.textContent='請等待目前檢查完成。';return;}
    if(currentPatient?.id!==patientId||currentPatient?.version!==version||(job.assessment?.version!=null&&job.assessment.version!==version)){feedback.textContent='病人或用藥版本已變更，請重新檢查後確認。';return;}
    const m=selected(),name=newName.value.trim(),amount=dose.value.trim(),freq=frequency.value.trim();
    if(!m||!name||!amount||!freq||reason.value.trim().length<4){edit.open=true;feedback.textContent='請填入醫師已確認的原用藥、確認後藥名、每次用量、頻次及理由。';return;}
    if(meds.filter(x=>x.name_key===m.name_key).length>1){feedback.textContent='原用藥有多筆不同用法，請先核對重複處方後再調整。';return;}
    if(/[\r\n:：;；，,、]/.test(name)||!/^[\p{L}][\p{L}\p{N} .'"()（）\/-]*$/u.test(name)){feedback.textContent='請只填單一實際藥品名稱，不要加入其他指令。';return;}
    if(!/^\d+(?:\.\d+)?(?:\/\d+(?:\.\d+)?)?\s*(?:mg|mcg|g|mL|毫克|微克|公克|錠|顆|單位|units?|IU|U)$/i.test(amount)){feedback.textContent='請填完整每次用量與單位；胰島素單位不能由口服藥mg自行換算。';return;}
    if(/[\r\n\x00-\x1f]/.test(freq)){feedback.textContent='請將醫師確認的頻次／時間填在同一欄，不能含換行。';return;}
    const text=`已停用：${m.name}\n新增處方：${name} ${amount}，${freq}`;
    const note=`配藥建議：${m.name} ${m.dose||''} ${m.frequency||''} → ${name} ${amount} ${freq}\n理由：${reason.value.trim()}\n來源定位（原風險／候選參考，個人用量及自訂藥物由醫師確認）：${evidence.sourceIds.map(id=>'['+id+']').join('、')||'由藥師另行核對醫師確認內容'}\n藥師確認已與開方醫師核對以上實際變更。`;
    confirm.disabled=true;skip.disabled=true;fields.querySelectorAll('input,select,textarea').forEach(f=>f.disabled=true);if(candidateSelect)candidateSelect.disabled=true;setBusy(true);feedback.textContent='正在更新病歷…';let saved=false;
    try{
      const response=await api('/api/prescriptions/confirm-change',{review:job.id,text,note,acknowledged:true,old_med_id:m.id,replacement_name:name,replacement_dose:amount,replacement_frequency:freq});
      if(response.patient?.id!==patientId)throw Error('回傳病歷身分不符，請重新載入確認狀態。');
      currentPatient=response.patient;renderRecord();renderPatientChoices();saved=true;edit.open=false;
      state.textContent='已寫入病歷';confirm.textContent='已寫入病歷';feedback.textContent='原藥已標示變更，新用藥已確認，並保留處方變更與介入紀錄。將依新版本重新檢查。';
      toast('已更新用藥與藥事介入紀錄');
    }catch(error){feedback.textContent=error.message+'；未確認成功前請勿重複操作，先核對右側病歷。';confirm.disabled=false;skip.disabled=false;fields.querySelectorAll('input,select,textarea').forEach(f=>f.disabled=false);if(candidateSelect)candidateSelect.disabled=false;}
    finally{setBusy(false);}
    if(saved)await beginPatientReview(newTurn());
  }
}

const showClinicalSourceOriginal=showSource;
showSource=function(source){
  showClinicalSourceOriginal(source);
  if(source.external){const link=$('#evidenceContent .original-pdf-link');if(link)link.textContent='開啟官方來源 ↗';
    $('#evidenceContent').append(el('p','external-source-scope','新增官方來源；查核 '+(source.verified_on||'日期未提供')+'。'+(source.scope||'')),el('p','external-source-verification',source.verification||''));
    if(source.source_summary)$('#evidenceContent').append(el('p','',source.source_summary));
  }
};

// safety-alerts-20261102: one visible, source-labelled warning area per response.
const safetyPreviousAssessment = renderAssessment;
function renderSafetyWarnings(target, result, sources) {
  target.querySelectorAll('.safety-alert-panel').forEach(node=>node.remove());
  const items=(result.safety_alerts||[]).filter(item=>item.id!=='model-incomplete');
  if(!Array.isArray(items)||!items.length)return;
  const sourceMap=new Map((sources||[]).map(s=>[s.id,s]));
  const panel=el('section','safety-alert-panel');panel.setAttribute('aria-label','用藥風險與待核對提醒');
  panel.append(el('h3','','用藥風險與待核對提醒'));
  for(const item of items){
    const card=el('section','safety-alert-card safety-'+item.category);card.setAttribute('role','alert');
    const label=item.category==='risk'?'來源記載風險':item.category==='gap'?'資料不足，無法判定':'待藥師核對';
    card.append(el('span','safety-alert-label',label),el('h4','',item.title||label),el('p','',item.message||'請核對來源與實際產品。'));
    if(item.quote||(item.source_ids||[]).length){
      const details=el('details','safety-evidence');details.append(el('summary','','查看來源與適用範圍'));
      if(item.quote)details.append(el('blockquote','',item.quote));
      sourceButtons(item.source_ids||[],sourceMap,details);
      if(item.scope)details.append(el('p','muted',item.scope));card.append(details);
    }
    panel.append(card);
  }
  target.prepend(panel);
}
renderAssessment=function(target,result,sources){
  safetyPreviousAssessment(target,result,sources);
  if(Array.isArray(result.safety_alerts)){
    // Replaces the former risk summary; preserve medicine, research and action UI.
    target.querySelectorAll('.compact-clinical-summary').forEach(node=>node.remove());
    renderSafetyWarnings(target,result,[...(sources||[]),...(result.sources||[])]);
    target.closest('.turn-body')?.querySelectorAll('.instant-safety-panel,.instant-interaction-warning,.instant-population-warning').forEach(node=>node.remove());
  }
};
const safetyPreviousWorkflow=renderWorkflowResponse;
renderWorkflowResponse=function(body,result){
  safetyPreviousWorkflow(body,result);
  if(!Array.isArray(result.safety_alerts))return;
  body.querySelectorAll('.instant-interaction-warning,.instant-population-warning').forEach(node=>node.remove());
  const host=el('div','instant-safety-panel');
  renderSafetyWarnings(host,result,[...(result.safety_alert_sources||[]),...(result.patient_alert_sources||[]),...(result.interaction_alert_sources||[])]);
  body.prepend(host);
};


// anthropometrics-ui-20261103
const anthropometricsPreviousRecord=renderPatientRecord;
renderPatientRecord=function(){
  anthropometricsPreviousRecord();
  if(!currentPatient)return;
  const box=$('#recScroll'),fields=box.querySelector('.rec-fields');
  if(!fields)return;
  for(const [key,label,unit] of [['height_cm','身高','公分'],['weight_kg','體重','公斤']]){
    const field=el('div','anthropometrics-field');
    field.append(el('dt','',label),el('dd','',currentPatient[key]!=null?currentPatient[key]+' '+unit:'未提供'));
    fields.append(field);
  }
};


// workspace-reading-search-20261103
const compactPreviousSteps=renderSteps;
renderSteps=function(target,steps){
  compactPreviousSteps(target,steps||[]);
  const fold=()=>{
    if(!target.isConnected)return;
    let details=target.parentElement;
    if(!details?.classList.contains('progress-details')){
      details=el('details','progress-details');
      const summary=el('summary');target.before(details);details.append(summary,target);
    }
    details.querySelector('summary').textContent='處理流程 · '+(steps||[]).length+' 個步驟（點選查看）';
  };
  if(target.isConnected)fold();else queueMicrotask(fold);
};
let inputReadingAnchor=null,inputReadingFollow=false,readingFrame=0;
function alignInputReading(){
  if(!inputReadingFollow||!inputReadingAnchor?.isConnected)return;
  const viewport=$('#log');
  viewport.scrollTop+=inputReadingAnchor.getBoundingClientRect().top-viewport.getBoundingClientRect().top-12;
}
// Existing callers cannot jump to the bottom while an answer grows.
scrollEnd=function(){if(inputReadingFollow)alignInputReading();};
const readingPreviousUserTurn=userTurn;
userTurn=function(text){
  inputReadingFollow=false;readingPreviousUserTurn(text);
  inputReadingAnchor=log.lastElementChild;inputReadingFollow=true;alignInputReading();
};
new MutationObserver(()=>{
  if(readingFrame||!inputReadingFollow)return;
  readingFrame=requestAnimationFrame(()=>{readingFrame=0;alignInputReading();});
}).observe(log,{childList:true,subtree:true,characterData:true});
for(const event of ['wheel','touchmove','pointerdown'])$('#log').addEventListener(event,()=>{inputReadingFollow=false;},{passive:true});
$('#log').addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].includes(e.key))inputReadingFollow=false;});
const readingPreviousSelect=selectExistingPatient;
selectExistingPatient=async function(id){
  inputReadingAnchor=null;inputReadingFollow=false;
  await readingPreviousSelect(id);
};
const searchablePreviousChoices=renderPatientChoices;
renderPatientChoices=function(){
  searchablePreviousChoices();
  const query=$('#patientSearch').value.trim(),picker=$('#patientPicker');
  let results=picker.querySelector('.patient-search-results');
  if(!results){results=el('div','patient-search-results');results.setAttribute('aria-live','polite');picker.append(results);}
  results.replaceChildren();results.hidden=!query;
  if(!query)return;
  if(!workflowPatients.length){
    $('#patientSelect').options[0].textContent='找不到相符病歷';
    results.append(el('p','muted','找不到相符病歷；請確認姓名、病歷號，以及目前使用的病歷資料夾。'));return;
  }
  results.append(el('p','muted','找到 '+workflowPatients.length+' 筆病歷，請點選要載入的病人：'));
  for(const p of workflowPatients.slice(0,8)){
    const choice=button((p.name||'姓名未提供')+' · '+p.id+(p.age!=null?' · '+p.age+'歲':''),()=>selectExistingPatient(p.id),'patient-search-choice');
    choice.dataset.patientId=p.id;results.append(choice);
  }
  if(workflowPatients.length>8)results.append(el('p','muted','其餘結果可從上方選單查看，或輸入更完整的姓名。'));
};
let patientSearchSequence=0;
refreshPatients=async function(){
  const sequence=++patientSearchSequence,query=$('#patientSearch').value;
  try{
    const result=await api('/api/patients?search='+encodeURIComponent(query));
    if(sequence!==patientSearchSequence||query!==$('#patientSearch').value)return;
    workflowPatients=result.patients||[];renderPatientChoices();
  }catch(error){if(sequence===patientSearchSequence)toast(error.message);}
};


// patient-picker-restore-list-20261103
const pickerPreviousChoices=renderPatientChoices;
renderPatientChoices=function(){
  // A selected older patient can be outside the recent-list server limit.
  if(!$('#patientSearch').value.trim()&&currentPatient&&!workflowPatients.some(p=>p.id===currentPatient.id)){
    workflowPatients=[...workflowPatients,{id:currentPatient.id,name:currentPatient.name,age:currentPatient.age,sex:currentPatient.sex,updated:currentPatient.updated}];
  }
  pickerPreviousChoices();
};
const pickerPreviousSelect=selectExistingPatient;
selectExistingPatient=async function(id){
  if(busy||active)return pickerPreviousSelect(id);
  await pickerPreviousSelect(id);
  // Selection clears the input, but the old code left the filtered array in memory.
  $('#patientSearch').value='';
  await refreshPatients();
};
const pickerPreviousReset=resetWorkflowPatient;
resetWorkflowPatient=async function(){
  if(busy||active)return pickerPreviousReset();
  await pickerPreviousReset();
  $('#patientSearch').value='';
  await refreshPatients();
};
