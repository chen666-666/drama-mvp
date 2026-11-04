"use strict";
const $=s=>document.querySelector(s),esc=v=>String(v??'未知').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state={catalogs:[],seriesId:null,ranking:null},task=null,ticket=0,writeBusy=false,scopeDirty=false,taskList=[],finalizeMessage='';
const statusText={active:'进行中',paused:'已暂停',interrupted:'重启后待恢复',cancelled:'已取消后续',complete:'已处理'};
const labels={running:'更新中',completed:'已完成',needs_login:'等待登录',blocked:'等待验证',interrupted:'待恢复',approved:'已核验',pending:'待核验',excluded:'已排除',public:'公开',unknown:'未知',private:'私密',paid:'付费',unavailable:'无法访问',verified:'自动校验通过',queued:'排队中',downloading:'下载中',validating:'校验中',passed:'人工通过',rejected:'人工拒绝',failed:'失败',paused:'已暂停',cancelled:'已取消'};
const label=value=>labels[value]||value;
const evidenceFilter=new CandidateReviewFilter($('#evidence-filters'),()=>{if(task?.cards)renderEvidence();});
function filteredEvidenceCards(){
 return task.cards.map(card=>({...card,episodes:card.episodes.filter(row=>evidenceFilter.matches(card.series_status,row.episode_status)).sort(compareCandidateLikes)})).filter(card=>card.episodes.length).sort((a,b)=>compareCandidateLikes(a.episodes[0],b.episodes[0]));
}
function renderEvidence(){
 const cards=filteredEvidenceCards(),count=cards.reduce((total,card)=>total+card.episodes.length,0);
 evidenceFilter.update(cards.length,count);
 renderEvidenceCards(cards);
}
const notify=message=>{$('#message').hidden=false;$('#message').textContent=message;};
const finalizeStatus=document.createElement('p');finalizeStatus.id='task-finalize-status';finalizeStatus.className='notice';finalizeStatus.setAttribute('role','status');finalizeStatus.setAttribute('aria-live','polite');$('#task-finalize').before(finalizeStatus);
$('#task-finalize').setAttribute('aria-describedby',finalizeStatus.id);$('#finalize-confirm').setAttribute('aria-describedby',finalizeStatus.id);
function finalizeRestriction(){
 if(task.status==='cancelled')return '此任务已取消，不能生成榜单。请继续同批次的进行中任务。';
 if(task.status!=='active')return task.status==='complete'?'此任务已处理，不能再次生成榜单。':'请先在任务上方点击“恢复”，再确认生成榜单。';
 if(task.draft.generation_blockers?.length)return task.draft.generation_blockers.join('；');
 if(task.search_job&&['queued','running'].includes(task.search_job.status))return '请等待搜索与互动更新完成后再确认版本。';
 return '';
}
function renderFinalize(){
 const fixed=!!task.ranking_id,reason=finalizeRestriction(),confirm=$('#finalize-confirm'),button=$('#task-finalize');
 button.hidden=fixed;confirm.parentElement.hidden=fixed;confirm.disabled=fixed||!!reason||writeBusy;
 button.disabled=fixed||!!reason||!confirm.checked||writeBusy;
 button.textContent=writeBusy?'正在生成正式榜单…':'确认生成正式榜单';
 button.setAttribute('aria-busy',String(writeBusy));
 finalizeStatus.textContent=fixed?`已生成并固定榜单 #${task.ranking_id}。请继续确认目录及准备范围。`:reason||finalizeMessage||(confirm.checked?'已确认，可生成新的正式榜单版本。':'请先勾选上方确认框，再生成正式榜单。');
 if(!fixed&&task.status==='cancelled'){
  const next=taskList.find(row=>row.id!==task.id&&row.run_id===task.run_id&&row.status==='active');
  if(next){const a=document.createElement('a');a.href=`/control/tasks?task_id=${next.id}`;a.textContent=`打开同批次任务 #${next.id} →`;finalizeStatus.append(' ',a);}
 }
 $('#task-initialize').hidden=!fixed;$('#task-initialize').disabled=task.status!=='active';
}
async function api(url,data){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);try{const response=await fetch(url,{cache:'no-store',signal:controller.signal,...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'请核对输入后重试');return result;}catch(error){if(error.name==='AbortError')throw new Error('请求超时。操作可能已保存，请刷新查询后再重试。');throw error;}finally{clearTimeout(timeout);}}
const endpoint=action=>`/api/control/tasks/${task.id}/${action}`;
const link=(url,label)=>{try{const parsed=new URL(url);return ['https:','http:'].includes(parsed.protocol)?`<a href="${esc(parsed.href)}" target="_blank" rel="noopener">${esc(label)}</a>`:'';}catch{return '';}};
function uid(){return crypto.randomUUID();}
async function listing(){const [runs,tasks]=await Promise.all([api('/api/runs'),api('/api/control/tasks')]);taskList=tasks;$('#task-run').innerHTML=runs.map(r=>`<option value="${r.id}" data-cutoff="${esc(r.cutoff_at_beijing)}" data-budget="${r.keywords.length*30}">${esc(r.name)} · ${esc(r.cutoff_at_beijing)}</option>`).join('');$('#task-choice').innerHTML='<option value="">选择任务</option>'+tasks.map(t=>`<option value="${t.id}">#${t.id} ${esc(t.name)} · ${statusText[t.status]||esc(t.status)}</option>`).join('');$('#task-run').dispatchEvent(new Event('change'));}
$('#task-run').addEventListener('change',()=>{const o=$('#task-run').selectedOptions[0];$('#task-budget').textContent=o?`截止时间：${o.dataset.cutoff}（北京时间）。搜索结果预算 ${o.dataset.budget} 条，上限 180 条；超过上限请在原工作台缩小范围。`:'';});
let startKey=uid();
$('#start-task').addEventListener('submit',async event=>{event.preventDefault();if(writeBusy)return;writeBusy=true;try{const r=await api('/api/control/tasks',{run_id:Number($('#task-run').value),operation_key:startKey,search:$('#task-search').checked});startKey=uid();await listing();await openTask(r.id);}catch(e){notify(e.message);}finally{writeBusy=false;}});
$('#task-choice').addEventListener('change',()=>{if($('#task-choice').value)openTask(Number($('#task-choice').value)).catch(e=>notify(e.message));});
async function openTask(id){ticket++;scopeDirty=false;finalizeMessage='';$('#finalize-confirm').checked=false;evidenceFilter.reset();task={id};history.replaceState(null,'',`?task_id=${id}`);$('#task-choice').value=id;await load();}
async function load(){if(!task)return;const own=++ticket,id=task.id,r=await api(`/api/control/tasks/${id}`);if(own!==ticket||id!==task.id)return;task=r;render();}
function renderEvidenceCards(cards){
 $('#evidence-cards').innerHTML=cards.map(c=>`<article class="evidence-card"><h3>${esc(c.title)} · ${esc(c.author_name)}</h3><dl><dt>稳定账号</dt><dd>${esc(c.account_id)}</dd><dt>制作依据</dt><dd>${esc(c.ai_basis||'待补依据')}</dd><dt>核验状态</dt><dd>${esc(label(c.series_status))}</dd></dl><p>${link(c.author_url,'作者主页')} ${link(c.collection_url,'作品合集')}</p><p class="review-warning">${esc(c.gaps.join('；')||'已有核验依据，交付时仍需检查公开资格')}<br>${esc(c.changes.length?'本任务期间变化：'+c.changes.join('、'):'与任务开始时资料一致')}</p><details><summary>既有作品核验记录 ${c.series_reviews.length} 条</summary>${c.series_reviews.map(r=>`<p>${esc(r.reviewed_at_beijing)} · ${esc(label(r.status))} · ${esc(r.reason)} ${link(r.evidence_url,'核对证据')}</p>`).join('')}</details>${c.episodes.map(r=>`<div class="evidence-episode"><label class="check"><input type="checkbox" data-candidate="${r.episode_id}" ${r.episode_id?'':'disabled'}>${esc(r.title)} · 视频 ${esc(r.video_id)} · ${esc(label(r.episode_status))}</label><p>${link(r.original_url,'原视频')} · 发布时间 ${esc(r.published_at_beijing)}</p><p>${esc(candidateOriginText(r))}</p><p>整条采样 ${esc(r.sampled_at_beijing)}：赞 ${esc(r.likes)} / 评 ${esc(r.comments)} / 藏 ${esc(r.favorites)} / 分享 ${esc(r.shares)}</p>${r.previous_sample?`<p class="muted">历史采样（不作本次排名）：${esc(r.previous_sample.sampled_at)} · 赞 ${esc(r.previous_sample.likes)}</p>`:""}<details><summary>正片核验记录 ${r.episode_reviews.length} 条及来源</summary>${r.episode_reviews.map(v=>`<p>${esc(v.reviewed_at_beijing)} · ${esc(v.reason)} ${link(v.evidence_url,'证据')}</p>`).join('')}${r.sources.map(s=>`<p>${link(s.source_url,'来源')} ${esc(s.keyword)} · ${esc(s.searched_at)}</p>`).join('')}</details></div>`).join('')}</article>`).join('')||(task.cards.length?'<p class="empty">当前分类暂无候选，可切换筛选或查看全部。</p>':'<p class="empty">暂无候选。搜索完成后显示；也可从原工作台手动导入。</p>');
}
function renderRefreshProblems(){
 let panel=$('#candidate-refresh-problems');
 if(!panel){panel=document.createElement('div');panel.id='candidate-refresh-problems';$('#candidate-pool-summary').after(panel);}
 const rows=task.cards.flatMap(c=>c.episodes).filter(r=>['failed','unavailable'].includes(r.membership?.refresh_status));
 panel.hidden=!rows.length;
 panel.innerHTML=rows.length?`<h3>互动更新异常 ${rows.length} 条</h3><p>以下是来源读取结果，原有人工核验结论保留。不能用历史互动冒充本次采样。请打开原视频核对；只有确认应排除时，才在下方核验表提交排除。</p><ul>${rows.map(r=>`<li>${link(r.original_url,r.title)} · ${esc(r.membership.refresh_note)} · 正片${esc(label(r.episode_status))}</li>`).join('')}</ul>`:'';
}
function render(){
 $('#task-detail').hidden=false;$('#task-title').textContent=`#${task.id} · ${task.run_name}`;
 const option=[...$('#task-choice').options].find(o=>Number(o.value)===task.id);if(option)option.textContent=`#${task.id} ${task.run_name} · ${statusText[task.status]||task.status}`;
 const phase={search:'搜索',review:'候选核验',scope:'确认范围',prepare:'准备及快审'}[task.stage]||task.stage;
 const collection=task.search_job,progress=collection?.progress;
 const collectionStatus=progress?.phase==='refresh'?`互动更新：已请求 ${progress.refresh_requests||0} 次 · ${label(collection.status)} · ${collection.message}`:task.message;
 $('#task-status').textContent=`${statusText[task.status]} · ${phase} · ${collectionStatus}`;$('#task-detailed').href=`/control/collection?run_id=${task.run_id}`;
 renderEvidence();
 renderRefreshProblems();
 $('#candidate-pool-summary').textContent=candidateSummaryText(task.cards.flatMap(c=>c.episodes),'本区展示');
 $('#refresh-candidate-pool').disabled=task.status!=='active'||task.stage==='search';
 $('#retry-candidate-pool').hidden=task.status!=='active'||!task.search_job||!['failed','blocked','needs_login','interrupted','paused'].includes(task.search_job.status);
 const d=task.draft;$('#task-draft').innerHTML=`<p>${esc(d.explanation)}</p><p class="review-warning">${esc((d.generation_blockers||[]).join('；'))}</p><p class="review-warning">${esc(d.review_scope.assessment)} · 待核验 ${d.review_scope.pending} · 可能影响 ${esc(d.review_scope.affecting_count)} · 影响未知 ${esc(d.review_scope.unknown_count)}</p><ol class="task-draft-list">${d.items.map(r=>`<li>${esc(r.series_title)} · ${esc(r.author_name)} · ${r.selected?'拟入选':'候补'} · ${esc(r.score)}</li>`).join('')}</ol><p>${esc(d.shortfall)} ${esc(d.disclaimer)}</p>`;
 renderFinalize();
 $('#task-difference').textContent=task.ranking_id?`已固定榜单 #${task.ranking_id}。`+(task.ranking_changed?'当前草案与固定版本有差异，固定版本保持不变；需要更新时新建任务或使用原工作台生成新版本。':'当前草案与固定版本排序一致。'):'';
 state.catalogs=task.catalogs;state.ranking={id:task.ranking_id,kind:'ranking'};state.seriesId=task.catalogs[0]?.series_id;
 $('#download-ranking').value=String(task.ranking_id||'');
 $('#catalogs').innerHTML=task.catalogs.map(c=>{const item=task.fixed_ranking.items.find(r=>r.series_id===c.series_id),info=c.workflow;return `<section class="panel" id="series-${c.series_id}"><h3>${esc(item.series_title)}</h3><p>${esc(info.message||'尚未确认准备范围')} · 新增或未确认 ${c.unconfirmed_count} 条</p><p>${esc(c.summary.full_series)}</p><div class="actions"><button data-discover="${c.id}" class="subtle">读取一页来源目录</button><a href="/control/videos?ranking_id=${task.ranking_id}&series_id=${c.series_id}">逐条核验 / 失败重试 →</a></div>${window.DiscoveryPanel.render(c)}<div class="table-wrap"><p class="muted">左侧勾选本次核验对象；右侧勾选允许准备的范围。</p><div class="task-entries">${c.episodes.map(r=>`<div class="task-entry"><label><input type="checkbox" data-directory="${r.id}" data-directory-catalog="${c.id}" aria-label="选中目录条目核验">核验</label><label><input type="checkbox" aria-label="允许准备本条视频" data-scope="${r.id}" data-catalog="${c.id}" ${r.status==='approved'&&r.access==='public'?'':'disabled'} ${info.episode_ids?.includes(r.id)?'checked':''}>准备</label><span>${esc(r.title)} · ${esc(label(r.status))} / ${esc(label(r.access))} · ${esc(label(r.job?.status||'未准备'))} · ${esc(label(r.media?.manual_status||'未抽检'))} ${link(r.original_url,'来源核对')}</span></div>`).join('')}</div></div><form data-directory-form="${c.id}"><fieldset><legend>对明确选中的目录条目逐项确认</legend><label class="check"><input name="ownership" type="checkbox">原作者及作品归属</label><label class="check"><input name="main_episode" type="checkbox">均为正片</label><label class="check"><input name="not_duplicate" type="checkbox">非重复上传</label><label class="check"><input name="public_access" type="checkbox">每条均公开可访问</label></fieldset><label>所选条目的核验依据<textarea name="note" required maxlength="10000"></textarea></label><label>结论<select name="status"><option value="approved">确认为公开正片</option><option value="excluded">排除</option></select></label><button>保存所选目录核验</button></form></section>`;}).join('');
 $('#task-scope').hidden=!task.catalogs.length||task.status!=='active';$('#task-complete').hidden=!task.all_scoped_complete||task.status!=='active';const supplement=document.getElementById('task-supplement');if(supplement)supplement.hidden=task.status!=='complete';
 window.refreshQuickReviews?.();for(const button of document.querySelectorAll('[data-quick-start],[data-quick-reset]')){button.disabled||=task.status!=='active';button.addEventListener('click',()=>{state.seriesId=task.catalogs.find(c=>c.id===Number(button.dataset.quickStart||button.dataset.quickReset))?.series_id;});}
}
async function mutate(action,data={}){if(writeBusy||!task)return;writeBusy=true;try{const r=await api(endpoint(action),data);scopeDirty=false;notify(r.message||'已保存');await load();return true;}catch(e){notify(e.message);return false;}finally{writeBusy=false;}}
$('#refresh-candidate-pool').onclick=()=>mutate('refresh-candidates');
$('#retry-candidate-pool').onclick=()=>mutate('retry-candidates');
$('#task-refresh').onclick=()=>load().catch(e=>notify(e.message));
document.querySelectorAll('[data-task-action]').forEach(b=>b.onclick=()=>mutate('control',{action:b.dataset.taskAction}));
let reviewKey=uid();
$('#evidence-review').onsubmit=async event=>{event.preventDefault();const form=event.target,rows=task.cards.flatMap(c=>c.episodes),ids=[...document.querySelectorAll('[data-candidate]:checked')].map(e=>Number(e.dataset.candidate));if(!ids.length){notify('请先明确勾选需要核验的条目');return;}const values=Object.fromEntries(new FormData(form));const checks={};form.querySelectorAll('[type=checkbox]').forEach(e=>checks[e.name]=e.checked);const data={...values,checks,operation_key:reviewKey,episode_ids:ids,tokens:Object.fromEntries(rows.filter(r=>ids.includes(r.episode_id)).map(r=>[r.episode_id,r.token]))};if(await mutate('reviews',data))reviewKey=uid();};
$('#finalize-confirm').onchange=()=>{finalizeMessage='';if(task?.draft)renderFinalize();};
$('#task-finalize').onclick=async()=>{
 if(writeBusy||!task?.draft||task.ranking_id)return;
 const reason=finalizeRestriction();if(reason||!$('#finalize-confirm').checked){finalizeMessage=reason||'请先勾选上方确认框，再生成正式榜单。';renderFinalize();return;}
 const id=task.id;writeBusy=true;$('#task-choice').disabled=true;finalizeMessage='正在生成正式榜单，请稍候…';renderFinalize();
 try{
  const result=await api(endpoint('finalize'),{token:task.draft.token,acknowledged:true});
  finalizeMessage=`已生成并固定榜单 #${result.ranking_id}。`;notify(finalizeMessage);
  // Preserve the saved result even if the following detail refresh fails.
  if(task.id===id)task.ranking_id=result.ranking_id;
  try{await load();}catch(e){finalizeMessage+=` 详情刷新失败：${e.message} 请刷新查询，避免重复生成。`;}
 }catch(e){finalizeMessage=e.message;notify(e.message);}
 finally{writeBusy=false;$('#task-choice').disabled=false;if(task.id===id){renderFinalize();if(finalizeMessage)finalizeStatus.textContent=finalizeMessage;}}
};
$('#task-initialize').onclick=()=>mutate('initialize');
$('#task-scope').onclick=()=>{const selections=task.catalogs.map(c=>{const ids=[...document.querySelectorAll(`[data-catalog="${c.id}"]:checked`)].map(e=>Number(e.dataset.scope));return {catalog_id:c.id,episode_ids:ids,tokens:Object.fromEntries(c.episodes.filter(r=>ids.includes(r.id)).map(r=>[r.id,r.scope_token]))};}).filter(c=>c.episode_ids.length);mutate('scope',{selections});};
$('#task-complete').onclick=()=>mutate('complete');
$('#catalogs').addEventListener('click',e=>{const b=e.target.closest('[data-discover]');if(b)mutate('discover',{catalog_id:Number(b.dataset.discover)});});
$('#catalogs').addEventListener('change',e=>{if(e.target.matches('[data-scope]'))scopeDirty=true;});
window.quickReviewScope=id=>task?.catalogs.find(c=>c.id===id)?.workflow.episode_ids||[];
window.quickReviewDeferred=id=>task?.catalogs.find(c=>c.id===id)?.workflow.deferred||[];
window.quickReviewDefer=async(id,mediaIds,reset)=>{await api(endpoint('defer'),{catalog_id:id,media_ids:mediaIds,reset});const info=task.catalogs.find(c=>c.id===id)?.workflow;if(info)info.deferred=reset?[]:mediaIds;};
function localMeasurements(){try{return JSON.parse(sessionStorage.getItem(`task-measurements-${task.id}`)||'[]');}catch{return [];}}
function measure(kind,target){if(!task)return;try{const events=localMeasurements();events.push({kind,target,at:new Date().toISOString()});sessionStorage.setItem(`task-measurements-${task.id}`,JSON.stringify(events.slice(-2000)));}catch{}}
document.addEventListener('click',event=>{const target=event.target.closest('button,a,input[type=checkbox],select');if(target)measure('click',target.id||target.name||target.dataset.taskAction||target.tagName.toLowerCase());});
document.addEventListener('change',event=>{if(event.target.matches('input:not([type=checkbox]),textarea'))measure('field_change',event.target.id||event.target.name||'field');});
$('#task-export-measurements').onclick=()=>{const data={task_id:task.id,exported_at:new Date().toISOString(),backend_events:task.operation_events||[],local_events:localMeasurements(),notes:'本页本浏览器会话的原始点击与字段修改记录；不含输入内容，未自动区分有效点击、主动操作时间、观看或等待。原流程和真实试点需用同口径人工记录。'};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`task-${task.id}-measurements.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
window.addEventListener('beforeunload',event=>{if(task?.status==='active'&&['search','prepare'].includes(task.stage)){event.preventDefault();event.returnValue='已授权后台工作将继续；需停止时先点击暂停。';}});
setInterval(()=>{if(!task||document.hidden||writeBusy||scopeDirty||$('#finalize-confirm').checked||document.querySelector('dialog[open]')||document.activeElement?.closest('form')||document.querySelector('[data-candidate]:checked')||document.querySelector('[data-directory]:checked')||document.querySelector('[data-scope]:focus'))return;load().catch(e=>notify(e.message));},5000);
listing().then(()=>{const id=Number(new URLSearchParams(location.search).get('task_id'));if(id)return openTask(id);}).catch(e=>notify(e.message));

$('#catalogs').addEventListener('submit',event=>{const form=event.target.closest('[data-directory-form]');if(!form)return;event.preventDefault();const id=Number(form.dataset.directoryForm),catalog=task.catalogs.find(c=>c.id===id),ids=[...document.querySelectorAll(`[data-directory-catalog="${id}"]:checked`)].map(e=>Number(e.dataset.directory));if(!ids.length){notify('请先勾选需要核验的目录条目（左侧选择框）');return;}const values=Object.fromEntries(new FormData(form));const data={catalog_id:id,episode_ids:ids,tokens:Object.fromEntries(catalog.episodes.filter(r=>ids.includes(r.id)).map(r=>[r.id,r.scope_token])),status:values.status,note:values.note};for(const name of ['ownership','main_episode','not_duplicate','public_access'])data[name]=form.elements[name].checked;mutate('catalog-review',data);});

$('#catalogs').addEventListener('click',async e=>{const b=e.target.closest('[data-discovery-action]');if(!b||writeBusy)return;writeBusy=true;b.disabled=true;try{const r=await window.DiscoveryPanel.send(b.dataset.discoveryCatalog,b.dataset.discoveryAction);notify(r.message);await load();}catch(error){notify(error.message);}finally{writeBusy=false;if(b.isConnected)b.disabled=false;}});
$('#catalogs').addEventListener('submit',async event=>{const form=event.target.closest('[data-discovery-recheck]');if(!form)return;event.preventDefault();if(writeBusy)return;writeBusy=true;try{await window.DiscoveryPanel.send(form.dataset.discoveryRecheck,'recheck',form.elements.aliases.value.split('\n').map(s=>s.trim()).filter(Boolean));await load();}catch(e){notify(e.message);}finally{writeBusy=false;}});
const supplementButton=document.createElement('button');supplementButton.id='task-supplement';supplementButton.textContent='为新增剧集新建补充准备流程';supplementButton.hidden=true;$('#task-complete').after(supplementButton);
supplementButton.onclick=async()=>{if(!task||writeBusy)return;writeBusy=true;try{const r=await api(endpoint('supplement'),{run_id:task.run_id,operation_key:crypto.randomUUID(),search:false});await listing();await openTask(r.id);}catch(e){notify(e.message);}finally{writeBusy=false;}};
