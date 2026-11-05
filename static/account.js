'use strict';
// login-20261105: sign-in screen, simulated subscription checkout, and the account menu in the header.
let accountStatus=null,gateResolve=null;
function planPrice(plan){return plan.price===null?'另洽':plan.price===0?'免費':'NT$'+plan.price.toLocaleString();}
function planSeats(plan){return plan.seats?plan.seats+' 位藥師':'依院內需求';}
function gateRoot(){let root=$('#accountGate');if(!root){root=el('div','account-gate');root.id='accountGate';document.body.append(root);}root.replaceChildren();root.scrollTop=0;return root;}
function finishGate(){$('#accountGate')?.remove();renderAccountMenu();gateResolve?.();gateResolve=null;}
async function accountGate(){
  accountStatus=await api('/api/auth/status');
  if(!accountStatus.require_login)return;
  if(!(accountStatus.account&&accountStatus.subscription))await new Promise(resolve=>{gateResolve=resolve;accountStatus.account?showPlans():showLogin();});
  renderAccountMenu();
}
function brandMark(){const brand=el('div','gate-brand'),logo=el('img');logo.src='/logo.png';logo.alt='';brand.append(logo,el('span','','藥你安心：藥事安全Agent'));return brand;}
function gateField(label,attrs){const wrap=el('label','gate-field'),input=el('input');Object.assign(input,attrs);wrap.append(el('span','',label),input);return [wrap,input];}
function afterSignIn(status){accountStatus=status;status.subscription?finishGate():showPlans();}

function showLogin(){
  const root=gateRoot(),layout=el('div','login-layout'),intro=el('section','login-intro');
  intro.append(brandMark(),el('h1','','讓每一張處方，\n都有可追溯的用藥安全核對'),el('p','login-lead','輸入病人狀況與藥單，系統逐對核對交互作用、整理仿單與研究來源，由藥師確認後交付。'));
  const points=el('ul','login-points');
  for(const [title,detail] of [['病歷與用藥風險提醒','建立病歷、補充用藥，立即提示禁忌與併用風險'],['官方來源可追溯','食藥署電子仿單、原廠仿單與 MedHop 研究路徑分開標示'],['藥師確認後才交付','建議、處置與病歷異動都留下藥師紀錄']]){const li=el('li');li.append(el('b','',title),el('span','',detail));points.append(li);}
  intro.append(points);
  const form=el('form','login-form'),error=el('p','form-error');
  form.append(el('h2','','藥師登入'),el('p','gate-muted','使用藥局配發的帳號登入。'));
  const [userField,user]=gateField('帳號',{required:true,maxLength:40,autocomplete:'username',placeholder:'輸入帳號'});
  const [passField,pass]=gateField('密碼',{type:'password',required:true,maxLength:200,autocomplete:'current-password',placeholder:'輸入密碼'});
  const submit=el('button','btn btn-primary gate-wide','登入');submit.type='submit';
  const demo=button('Demo 體驗',async()=>{demo.disabled=submit.disabled=true;try{afterSignIn(await api('/api/auth/demo',{}));}catch(err){error.textContent=err.message;demo.disabled=submit.disabled=false;}},'btn gate-demo gate-wide');
  form.append(userField,passField,error,submit,el('div','gate-divider','或'),demo,el('p','gate-muted gate-center','免登入，以「陳藥師 · 示範社區藥局」帳號直接使用'),el('p','gate-hint','另有尚未訂閱的示範帳號 lin／medhop2026，可體驗訂閱流程'));
  form.addEventListener('submit',async e=>{e.preventDefault();demo.disabled=submit.disabled=true;error.textContent='';try{afterSignIn(await api('/api/auth/login',{username:user.value,password:pass.value}));}catch(err){error.textContent=err.message;demo.disabled=submit.disabled=false;}});
  layout.append(intro,form);root.append(layout);user.focus();
}

function gateHeader(action){
  const head=el('header','gate-header'),right=el('div','gate-account'),a=accountStatus.account;
  right.append(el('span','',`${a.name} · ${a.pharmacy}`));
  if(action==='manage')right.append(button('返回系統',()=>$('#accountGate').remove(),'text-btn'));
  if(action==='logout')right.append(button('登出',signOut,'text-btn'));
  head.append(brandMark(),right);return head;
}
function showPlans(manage=false){
  const root=gateRoot(),page=el('div','plans-page'),current=accountStatus.subscription?.plan;
  page.append(el('h1','',manage?'方案與用量':'選擇適合的方案'),el('p','gate-muted gate-center','所有方案都包含用藥安全檢查、仿單來源核對與藥師確認紀錄，可隨時調整。'));
  if(manage&&accountStatus.subscription){const s=accountStatus.subscription,usage=el('div','plan-usage');usage.append(el('span','',`目前方案：${s.name}`),el('span','',`本月已使用 ${s.checks_used} 次檢查${s.checks_limit?' ／ 上限 '+s.checks_limit+' 次':'（不限次數）'}`),el('span','',`下次續訂：${s.renews}`));page.append(usage);}
  const grid=el('div','plan-grid');
  for(const plan of accountStatus.plans){
    const card=el('section','plan-card'+(plan.recommended?' is-recommended':'')+(plan.id===current?' is-current':''));
    if(plan.recommended)card.append(el('span','plan-badge','推薦'));
    const price=el('p','plan-price');price.append(el('b','',planPrice(plan)),document.createTextNode(plan.price?' / '+plan.period:plan.price===0?' · '+plan.period:''));
    card.append(el('h2','',plan.name),price,el('p','plan-seats',planSeats(plan)));
    const features=el('ul','plan-features');plan.features.forEach(f=>features.append(el('li','',f)));card.append(features);
    if(plan.id===current){const now=el('button','btn plan-action','目前方案');now.disabled=true;card.append(now);}
    else if(plan.contact)card.append(button('聯絡我們',()=>toast('醫院方案依院內需求規劃，請與團隊聯絡'),'btn btn-quiet plan-action'));
    else card.append(button(plan.price?'選擇此方案':'開始免費試用',()=>showCheckout(plan,manage),'btn '+(plan.recommended?'btn-primary':'btn-quiet')+' plan-action'));
    grid.append(card);
  }
  page.append(grid);root.append(gateHeader(manage?'manage':'logout'),page);
}
function showCheckout(plan,manage){
  const root=gateRoot(),page=el('div','checkout-page'),summary=el('section','checkout-summary'),pay=el('section','checkout-pay');
  const start=new Date(),renew=new Date(start.getTime()+30*864e5),day=d=>d.toLocaleDateString('zh-TW');
  summary.append(el('h2','','訂單摘要'));
  const rows=el('dl','checkout-rows');
  for(const [k,v] of [['方案',plan.name],['藥師席次',planSeats(plan)],['檢查次數',plan.checks?`每月 ${plan.checks} 次`:'不限次數'],['計費週期',plan.price?'每月自動續訂，可隨時取消':'30 天免費試用'],['本期',`${day(start)} – ${day(renew)}`]])rows.append(el('dt','',k),el('dd','',v));
  const total=el('div','checkout-total');total.append(el('span','','應付金額'),el('b','',plan.price?'NT$'+plan.price.toLocaleString():'NT$0'));
  summary.append(rows,total);
  const card=el('div','demo-card');card.append(el('span','demo-card-brand','VISA'),el('span','demo-card-number','•••• •••• •••• 4242'),el('span','demo-card-meta','示範卡 · 有效期限 12/29'));
  const error=el('p','form-error');
  const confirm=button(plan.price?`確認訂閱並付款 NT$${plan.price.toLocaleString()}`:'開始免費試用',async()=>{
    confirm.disabled=back.disabled=true;confirm.textContent='付款處理中…';
    try{await new Promise(r=>setTimeout(r,700));accountStatus=await api('/api/subscription/checkout',{plan:plan.id});showSuccess(manage);}
    catch(err){error.textContent=err.message;confirm.disabled=back.disabled=false;confirm.textContent='重新付款';}
  },'btn btn-primary gate-wide');
  const back=button('返回方案',()=>showPlans(manage),'text-btn');
  pay.append(el('h2','','付款方式'),card,el('p','gate-note','示範環境：此為模擬付款，不會實際扣款，系統也不會收集或儲存任何付款資料。'),error,confirm,back);
  const grid=el('div','checkout-grid');grid.append(summary,pay);page.append(el('h1','','確認訂閱'),grid);
  root.append(gateHeader(manage?'manage':'logout'),page);
}
function showSuccess(manage){
  const root=gateRoot(),page=el('div','success-page'),s=accountStatus.subscription,a=accountStatus.account;
  page.append(el('div','success-mark','✓'),el('h1','','訂閱成功'),el('p','gate-muted gate-center',`${a.name} · ${a.pharmacy} 已開通「${s.name}」方案`));
  const rows=el('dl','checkout-rows');for(const [k,v] of [['開通日',s.started],['下次續訂',s.renews],['付款方式',s.payment]])rows.append(el('dt','',k),el('dd','',v));
  page.append(rows,button(manage?'返回系統':'進入系統',()=>{if(manage){$('#accountGate').remove();renderAccountMenu();}else finishGate();},'btn btn-primary'));
  root.append(gateHeader('none'),page);
}
async function signOut(){if(busy||active){toast('請先停止目前檢查');return;}await api('/api/auth/logout',{});location.reload();}
function renderAccountMenu(){
  const top=document.querySelector('.top');if(!top||!accountStatus?.account)return;
  top.querySelector('.account-menu')?.remove();
  const menu=el('div','account-menu'),a=accountStatus.account;
  menu.append(button(accountStatus.subscription?.name||'選擇方案',async()=>{accountStatus=await api('/api/auth/status');showPlans(true);},'plan-chip'),el('span','account-name',`${a.name} · ${a.pharmacy}`),button('登出',signOut,'text-btn'));
  top.append(menu);
}
