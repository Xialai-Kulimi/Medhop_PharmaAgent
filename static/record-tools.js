'use strict';
// record-tools-20261103: JSON exchange, pharmacist edits in the record panel, and external drug references.
const RECORD_KIND_LABELS={current:'長期／目前用藥',new_order:'新增處方',reported:'自行購買／自述'};
const TFDA_INSERT_PAGE='https://mcp.fda.gov.tw/im_detail_1/';

function recordAllergy(patient){let allergy='';for(const event of patient.events||[]){const match=event.body.match(/過敏史[：:]\s*([^\n。]+)/);if(match)allergy=match[1].trim();}return allergy==='未記錄'?'':allergy;}
function editorDialog(title,note){
  const dialog=el('dialog','record-editor'),form=el('form'),head=el('div','dialog-head'),error=el('p','form-error'),actions=el('div','dialog-actions');
  head.append(el('h2','',title),button('×',()=>dialog.close(),'close-btn'));form.append(head);if(note)form.append(el('p','muted',note));
  dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());
  return {dialog,form,error,actions,open(){form.append(error,actions);dialog.showModal();}};
}
function editorField(parent,label,control){const wrap=el('label','control-label editor-field');wrap.append(el('span','',label),control);parent.append(wrap);return control;}
function editorInput(value,attrs={}){const input=el('input');Object.assign(input,attrs);input.value=value??'';return input;}
function editorSelect(options,value){const select=el('select');for(const [key,label] of options){const option=el('option','',label);option.value=key;option.selected=key===value;select.append(option);}return select;}
function editorButtons(editor,label){const save=el('button','btn btn-primary',label);save.type='submit';editor.actions.append(button('取消',()=>editor.dialog.close(),'btn btn-quiet'),save);return save;}
let recordEditTurn=null;
function recheckButton(body,label){const actions=el('div','recommendation-actions'),start=button(label,()=>{start.disabled=true;if(body===recordEditTurn)recordEditTurn=null;beginPatientReview(body);},'btn btn-primary');actions.append(start);return actions;}
function announceRecordEdit(message){
  // Consecutive edits share one notice so the chat keeps a single re-check prompt.
  const reuse=recordEditTurn?.isConnected&&recordEditTurn.closest('article')===log.lastElementChild&&recordEditTurn.dataset.patientId===currentPatient.id;
  const body=reuse?recordEditTurn:newTurn();body.dataset.patientId=currentPatient.id;recordEditTurn=body;
  let entries=body.querySelector('.record-edit-log');if(!entries){entries=el('div','record-edit-log');body.append(entries);}
  body.querySelector('.record-edit-prompt')?.remove();body.querySelector('.recommendation-actions')?.remove();
  entries.append(el('p','',message),el('p','record-edit-prompt muted','病歷版本已更新，先前的檢查結果不適用於新版本。'));
  if(currentPatient.medications.some(m=>m.status==='active'))body.append(recheckButton(body,'依新版本重新檢查'));
  $('#log').scrollTop=$('#log').scrollHeight;
}
async function applyRecordEdit(path,payload,editor,save){
  if(busy||active){editor.error.textContent='請先停止目前檢查';return;}
  save.disabled=true;editor.error.textContent='';
  try{const result=await api(path,payload);currentPatient=result.patient;renderRecord();editor.dialog.close();if(result.message){toast(result.message);announceRecordEdit(result.message);}else toast('沒有變更');}
  catch(err){editor.error.textContent=err.message;save.disabled=false;}
}

function openProfileEditor(){
  const p=currentPatient;if(!p)return;
  const editor=editorDialog('編輯基本資料','只更新有變動的欄位；異動紀錄會留下修改前後。'),grid=el('div','editor-grid');
  const name=editorField(editor.form,'姓名',editorInput(p.name||'',{required:true,maxLength:80}));editor.form.append(grid);
  const sex=editorField(grid,'性別',editorSelect(['男','女','其他','未確認'].map(v=>[v,v]),p.sex||'未確認'));
  const age=editorField(grid,'年齡（歲）',editorInput(p.age,{type:'number',min:0,max:129,step:1}));
  const height=editorField(grid,'身高（公分）',editorInput(p.height_cm,{type:'number',min:1,max:300,step:'any'}));
  const weight=editorField(grid,'體重（公斤）',editorInput(p.weight_kg,{type:'number',min:1,max:600,step:'any'}));
  const pregnancy=editorField(grid,'懷孕狀態',editorSelect(Object.entries(PREGNANCY_LABELS),p.pregnancy||'unknown'));
  const weeks=editorField(grid,'孕週',editorInput(p.pregnancy_weeks,{type:'number',min:0,max:45,step:1}));
  const lactation=editorField(grid,'哺乳狀態',editorSelect(Object.entries(LACTATION_LABELS),p.lactation||'unknown'));
  const syncWeeks=()=>{weeks.disabled=pregnancy.value!=='pregnant';if(weeks.disabled)weeks.value='';};pregnancy.addEventListener('change',syncWeeks);syncWeeks();
  const save=editorButtons(editor,'儲存變更');
  editor.form.addEventListener('submit',async e=>{
    e.preventDefault();const number=input=>input.value.trim()===''?null:Number(input.value);
    const next={name:name.value.trim(),sex:sex.value,age:number(age),height_cm:number(height),weight_kg:number(weight),pregnancy:pregnancy.value,pregnancy_weeks:pregnancy.value==='pregnant'?number(weeks):null,lactation:lactation.value};
    const before={name:p.name||'',sex:p.sex||'未確認',age:p.age??null,height_cm:p.height_cm??null,weight_kg:p.weight_kg??null,pregnancy:p.pregnancy||'unknown',pregnancy_weeks:p.pregnancy_weeks??null,lactation:p.lactation||'unknown'};
    const changes=Object.fromEntries(Object.entries(next).filter(([key,value])=>value!==before[key]));
    if(!Object.keys(changes).length){editor.dialog.close();toast('沒有變更');return;}
    await applyRecordEdit('/api/patients/edit',changes,editor,save);
  });
  editor.open();
}
function openHistoryEditor(){
  const p=currentPatient;if(!p)return;
  const editor=editorDialog('編輯病史與過敏史','病史一行一項，儲存後取代目前顯示的病史；原本的輸入紀錄仍保留在異動紀錄。');
  const history=editorField(editor.form,'病史（一行一項）',el('textarea'));history.rows=6;history.maxLength=3000;history.value=(p.history||[]).join('\n');
  const allergy=editorField(editor.form,'過敏史',editorInput(recordAllergy(p),{maxLength:300,placeholder:'未記錄請留空'}));
  const save=editorButtons(editor,'儲存變更');
  editor.form.addEventListener('submit',async e=>{
    e.preventDefault();const changes={},items=history.value.split('\n').map(item=>item.trim()).filter(Boolean);
    if(JSON.stringify(items)!==JSON.stringify(p.history||[]))changes.history=items;
    if(allergy.value.trim()!==recordAllergy(p))changes.allergies=allergy.value.trim();
    if(!Object.keys(changes).length){editor.dialog.close();toast('沒有變更');return;}
    await applyRecordEdit('/api/patients/edit',changes,editor,save);
  });
  editor.open();
}
function openMedicationEditor(med){
  if(!currentPatient)return;
  const editor=editorDialog(med?'修改用藥':'新增用藥',med?'原始輸入會保留；異動紀錄會留下修改前後。':'新增後列入目前藥單，並記錄為藥師新增。'),grid=el('div','editor-grid');
  const name=editorField(editor.form,'藥名',editorInput(med?.name,{required:true,maxLength:100,placeholder:'例如：Clopidogrel'}));editor.form.append(grid);
  const dose=editorField(grid,'規格／用量',editorInput(med?.dose,{maxLength:60,placeholder:'例如：75 mg'}));
  const frequency=editorField(grid,'頻次',editorInput(med?.frequency,{maxLength:160,placeholder:'例如：每天一次'}));
  const kind=editorField(grid,'類別',editorSelect(Object.entries(RECORD_KIND_LABELS),med&&RECORD_KIND_LABELS[med.kind]?med.kind:'current'));
  if(med){
    const status=async value=>{if(busy||active){editor.error.textContent='請先停止目前檢查';return;}try{const result=await api('/api/medications/status',{id:med.id,status:value});currentPatient=result.patient;renderRecord();editor.dialog.close();toast(result.message);announceRecordEdit(result.message);}catch(err){editor.error.textContent=err.message;}};
    let confirmRemove=false;const remove=button('刪除（輸入錯誤）',()=>{if(!confirmRemove){confirmRemove=true;remove.textContent='再按一次確認刪除';return;}status('removed');},'btn btn-quiet editor-danger');
    editor.actions.append(remove,med.status==='stopped'?button('恢復使用',()=>status('active'),'btn btn-quiet'):button('標記停用',()=>status('stopped'),'btn btn-quiet'));
  }
  const save=editorButtons(editor,med?'儲存變更':'新增用藥');
  editor.form.addEventListener('submit',async e=>{e.preventDefault();await applyRecordEdit('/api/medications/save',{id:med?.id??null,name:name.value.trim(),dose:dose.value.trim(),frequency:frequency.value.trim(),kind:kind.value},editor,save);});
  editor.open();
}

const recordExchange=el('div','record-exchange'),recordImportFile=el('input');
recordImportFile.type='file';recordImportFile.accept='.json,application/json';recordImportFile.hidden=true;
const recordImportButton=button('匯入 JSON',()=>{if(busy||active){toast('請先停止目前檢查');return;}recordImportFile.click();});
const recordExportButton=button('匯出 JSON',exportCurrentRecord);
$('#editRecord').before(recordExchange);recordExchange.append(recordImportButton,recordExportButton,$('#editRecord'),recordImportFile);
async function exportCurrentRecord(){
  const p=currentPatient;if(!p)return;
  try{
    const record=await api('/api/patients/'+encodeURIComponent(p.id)+'/export'),link=el('a');
    link.href=URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));link.download=p.id+'.json';
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
    toast('已匯出 '+p.id+'.json；檔案含病人姓名，請依院內規定保管。');
    const latest=await api('/api/patients');if(latest.patient?.id===p.id&&!busy){currentPatient=latest.patient;renderRecord();}
  }catch(err){toast(err.message);}
}
recordImportFile.addEventListener('change',async()=>{
  const file=recordImportFile.files[0];recordImportFile.value='';if(!file)return;
  let record;try{record=JSON.parse(await file.text());}catch{toast('檔案不是有效的 JSON');return;}
  if(record?.format!=='medhop-patient-record'){toast('這不是本系統匯出的病歷 JSON（format 應為 medhop-patient-record）');return;}
  try{
    const result=await api('/api/patients/import',{file_name:file.name,record});
    clearPatientWorkspace();workflowResetRequired=false;workflowAwaitingIdentity=false;currentPatient=result.patient;renderRecord();await refreshPatients();
    const body=newTurn();body.dataset.patientId=currentPatient.id;body.append(el('p','',result.message));
    if(currentPatient.medications.some(m=>m.status==='active'))body.append(recheckButton(body,'開始用藥檢查'));
    toast('已匯入 '+file.name);
  }catch(err){toast(err.message);}
});

const recordToolsPreviousRecord=renderPatientRecord;
renderPatientRecord=function(){
  recordToolsPreviousRecord();
  recordExportButton.disabled=!currentPatient;
  if(!currentPatient)return;
  const box=$('#recScroll'),meds=new Map((currentPatient.medications||[]).map(m=>[String(m.id),m]));
  box.querySelector('.rec-name')?.append(button('編輯基本資料',openProfileEditor,'text-btn record-edit-link'));
  for(const heading of box.querySelectorAll('.rec-sec>h3'))if(heading.textContent==='病史')heading.append(button('編輯病史與過敏',openHistoryEditor,'text-btn record-edit-link'));
  for(const row of box.querySelectorAll('.record-med-row[data-med-id]')){const med=meds.get(row.dataset.medId);if(med&&['active','stopped'].includes(med.status)){row.classList.add('is-editable');row.append(button('修改',()=>openMedicationEditor(med),'text-btn record-med-edit'));}}
  const medSections=[...box.querySelectorAll('.rec-sec')].filter(section=>section.querySelector('.record-med-rows')||section.querySelector('h3')?.textContent==='用藥');
  const add=el('div','record-add-med');add.append(button('＋ 新增用藥',()=>openMedicationEditor(null)));
  (medSections.at(-1)||box.querySelector('.rec-sec'))?.append(add);
};

function externalLink(text,href){const link=el('a','source-chip',text+' ↗');link.href=href;link.target='_blank';link.rel='noopener noreferrer';return link;}
function tfdaInsertLink(license){return externalLink('食藥署仿單 · '+license,TFDA_INSERT_PAGE+encodeURIComponent(license));}
function externalDrugLinks(detail){
  const section=el('section','external-drug-links'),links=el('div','source-chips'),ingredients=detail.ingredient_lookup?.ingredients||[];
  const term=/[一-鿿]/.test(detail.name)&&ingredients.length?ingredients.join(' '):detail.name;
  links.append(detail.product?.['許可證字號']?tfdaInsertLink(detail.product['許可證字號']):externalLink('食藥署藥品仿單查詢平台','https://mcp.fda.gov.tw/'),
    externalLink('UpToDate · '+term,'https://www.uptodate.com/contents/search?search='+encodeURIComponent(term)),
    externalLink('Micromedex','https://www.micromedexsolutions.com/'),externalLink('OpenEvidence','https://www.openevidence.com/'));
  section.append(el('h4','','外部藥品資料庫'),links,el('p','reader-note',(detail.product?'':'尚未核對實際產品，食藥署仿單請依許可證查詢；產品候選下方有各自的仿單連結。')+'UpToDate、Micromedex 需機構訂閱帳號；外部網站的內容不會自動寫入本系統。'));
  return section;
}
