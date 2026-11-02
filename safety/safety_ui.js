// safety-alerts-20261102: one visible, source-labelled warning area per response.
const safetyPreviousAssessment = renderAssessment;
function renderSafetyWarnings(target, result, sources) {
  target.querySelectorAll('.safety-alert-panel').forEach(node=>node.remove());
  const items=result.safety_alerts;
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
