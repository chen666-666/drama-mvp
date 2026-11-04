"use strict";
const $ = selector=>document.querySelector(selector);
const esc = value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state={ranking:null,catalogs:[],timer:null,request:0,busy:0,messages:new Map(),initialized:new Set()};
const isManual=()=>state.ranking?.kind==='manual';
const contextQuery=()=>isManual()?`catalog_id=${state.ranking.catalog_id}`:`ranking_id=${state.ranking.id}&series_id=${state.seriesId}`;
const discoveryKeywords=new Map();
const reviewSelections=new Map(),reviewFilters=new Map();
function reviewSelection(c){if(!reviewSelections.has(c.id))reviewSelections.set(c.id,new Set());return reviewSelections.get(c.id);}
function reviewAttention(row,c){
  const reasons=[];
  if(!row.episode_number&&!row.episode_range)reasons.push('集号待确认');
  if(row.episode_range)reasons.push('多集合集，需单独核对');
  if(/预告|花絮|合集|小说|抢先|抢鲜|(?:\d+|[一二三四五六七八九十百]+)\s*[-—–~～至到]\s*(?:\d+|[一二三四五六七八九十百]+)/.test(row.title))reasons.push('标题含宣传或多集信息');
  if(c.summary.duplicate_numbers?.includes(row.episode_number))reasons.push('集号重复');
  return reasons.join('；');
}
function reviewRows(c){const filter=reviewFilters.get(c.id)||'all';return c.episodes.filter(row=>filter==='all'||filter==='pending'&&row.status==='pending'||filter==='numbered'&&row.status==='pending'&&!reviewAttention(row,c)||filter==='attention'&&row.status==='pending'&&!!reviewAttention(row,c)||row.status===filter);}
function reviewToolbar(c){
  const options=[['all','全部作品'],['pending','待核验'],['numbered','待核验 · 集号明确'],['attention','待核验 · 需单独核对'],['approved','已确认正片'],['excluded','已排除']];
  return `<div class="catalog-tools batch-review-tools"><label>筛选目录<select data-review-filter="${c.id}">${options.map(([value,label])=>`<option value="${value}" ${(reviewFilters.get(c.id)||'all')===value?'selected':''}>${label}</option>`).join('')}</select></label><div class="actions"><button type="button" class="subtle" data-review-select-all="${c.id}">全选当前列表</button><button type="button" class="subtle" data-review-clear="${c.id}">取消选择</button><span data-selection-count></span></div><div class="actions"><button type="button" data-review-batch="${c.id}" data-decision="approved">批量确认正片</button><button type="button" class="subtle" data-review-batch="${c.id}" data-decision="excluded">批量排除</button></div><p class="muted">只处理你勾选的作品。可先筛选“待核验 · 集号明确”，核对后一起确认；集号不明、合集或宣传请单独辨别。目录审批不会代替下载后的人工抽检。</p></div>`;
}
function syncReviewSelection(c){
  const section=document.getElementById(`series-${c.series_id}`);if(!section)return;
  const selected=reviewSelection(c);section.querySelectorAll('[data-review-select]').forEach(input=>{input.checked=selected.has(Number(input.dataset.reviewSelect));});
  const count=section.querySelector('[data-selection-count]');if(count)count.textContent=`当前列表 ${reviewRows(c).length} 条 · 已选 ${selected.size} 条`;
  section.querySelectorAll('[data-review-batch]').forEach(button=>button.disabled=!selected.size);
}
state.seriesId=new URL(location).searchParams.get("series_id")||(/^#series-(\d+)$/.exec(location.hash)?.[1]??null);
const names={pending:"待核验",approved:"正片已确认",excluded:"已排除",public:"公开可访问",unknown:"访问范围未知",private:"私密",paid:"付费",unavailable:"无法访问",passed:"人工通过",rejected:"抽检发现问题"};
function notify(text,error=false,section=null) {$("#message").hidden=false;$("#message").textContent=text;$("#message").classList.toggle("error",error);if(section){state.messages.set(section.id,{text,error});const feedback=section.querySelector("[data-feedback]");if(feedback){feedback.hidden=false;feedback.textContent=text;feedback.classList.toggle("error",error);}}const dialog=document.querySelector("dialog[open]");if(dialog&&error) dialog.querySelector(".modal-error").textContent=text;}
async function api(url,data) {const response=await fetch(url,data===undefined?{cache:"no-store"}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});const result=await response.json();if(!response.ok) throw new Error(typeof result.detail === "string"?result.detail:Array.isArray(result.detail)?result.detail.map(item=>String(item.msg||"填写内容无效").replace(/^Value error, /,"")).join("；"):"操作未完成，请检查填写内容");return result;}
function action(handler) {return async event=>{event.preventDefault();const button=event.submitter||event.target.closest("button"),section=event.target.closest("#catalogs .panel"),label=button?.textContent,disabled=button?.disabled;state.busy++;if(button){button.disabled=true;button.textContent="处理中…";}try{await handler(event);}catch(error){notify(error.message,true,section);}finally{state.busy--;if(button&&button.isConnected){button.disabled=disabled;button.textContent=label;}}};}
function episodeById(id) {return state.catalogs.flatMap(c=>c.episodes).find(row=>row.id===Number(id));}
function catalogById(id) {return state.catalogs.find(row=>row.id===Number(id));}
function needsRepair(row) {return row.job?.status==="verified"&&(!row.media||!row.media.exists||row.media.manual_status==="rejected");}
function downloadLabel(c){
  const trial=c.episodes.find(row=>row.job?.trial),active=trial&&!c.summary.trial_passed?trial:c.episodes.find(row=>["queued","downloading","validating"].includes(row.job?.status));
  if(active){if(needsRepair(active))return "重新获取";return {queued:"排队中",downloading:"下载中",validating:"正在校验视频",paused:"恢复或重试",failed:"恢复或重试",cancelled:"恢复或重试"}[active.job.status]||"准备一集视频";}
  return "准备一集视频";
}
async function downloadOne(c,report) {
  report("正在检查并准备服务器文件…");
  const rankingId=$("#download-ranking").value,request=state.request;
  const result=await api(`/api/downloads/catalogs/${c.id}/download`,{});
  if(rankingId!==$("#download-ranking").value||request!==state.request)return;
  report(result.message);
  if(result.file_url)report("服务器文件已就绪，请前往播放区观看与下载。");
}
async function load(poll=false) {
  clearTimeout(state.timer);const id=$("#download-ranking").value;if(!id)return;
  if(poll&&(document.hidden||state.busy||document.querySelector("dialog[open]")||document.activeElement?.closest("#catalogs form, #catalogs .collection-settings"))){state.timer=setTimeout(()=>load(true).catch(error=>notify(error.message,true)),3000);return;}
  const request=++state.request;
  const manual=id.startsWith('manual:');
  if(!manual&&!state.initialized.has(id)){await api(`/api/downloads/rankings/${id}/initialize`,{});state.initialized.add(id);}
  if(request!==state.request||id!==$("#download-ranking").value)return;
  const result=await api(manual?`/api/downloads/collections/${id.slice(7)}`:`/api/downloads/rankings/${id}`);if(request!==state.request||id!==$("#download-ranking").value)return;
  const signature=JSON.stringify(result),changed=state.lastResult!==signature;state.lastResult=signature;
  state.ranking=result.ranking;state.catalogs=result.catalogs;if(!poll||changed){$("#ranking-info").textContent=manual?'指定收录 · 独立管理剧集，不改变 Top3 榜单。先核对目录，再准备视频并发布。':`${result.ranking.title} · V${result.ranking.version} · ${result.ranking.disclaimer} ${result.ranking.shortfall||""}`;$("#empty").hidden=!!result.catalogs.length;render();}
  if(!poll){const episodeId=new URL(location).searchParams.get('episode_id');if(episodeId)document.getElementById(`episode-${episodeId}`)?.scrollIntoView({block:'center'});}
  if(!poll&&location.hash)document.getElementById(location.hash.slice(1))?.scrollIntoView({block:"start"});state.timer=setTimeout(()=>load(true).catch(error=>notify(error.message,true)),4000);
}
let videoView=new URL(location).searchParams.get('view')==='inspection'?'inspection':'catalog';
function render() {
  document.body.dataset.videoView=videoView;
  document.querySelectorAll('[data-video-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.videoTab===videoView)));
  $('#video-view-note').textContent=videoView==='inspection'?'核对开头、中段、结尾和声音，主动提交后才记录抽检结论。':'先核对目录与公开正片，再准备试下载；人工抽检通过后可批量准备。';
  const items=state.ranking.items.filter(row=>row.selected);
  if(!state.seriesId&&items.length)state.seriesId=String(items[0].series_id);
  const selected=items.find(row=>String(row.series_id)===state.seriesId);
  const selector=$("#download-series");
  selector.innerHTML=(selected||!state.seriesId?"":'<option value="">请重新选择作品</option>')+items.map(row=>`<option value="${row.series_id}">${isManual()?'指定收录':`TOP ${row.position}`} · ${esc(row.series_title)}</option>`).join("");
  selector.value=selected?state.seriesId:"";selector.disabled=!items.length;
  const catalogs=selected?state.catalogs.filter(c=>String(c.series_id)===state.seriesId):[];
  $("#empty").hidden=!!catalogs.length;
  $("#empty").textContent=selected?"本作品暂无剧集目录，请刷新后重试。":items.length?"该作品不在当前榜单中，请选择要查看的作品。":"当前没有入选作品，请先生成一份有合格作品的榜单。";
  $("#download-title").textContent=selected?`${isManual()?'指定收录':`TOP ${selected.position}`} · ${selected.series_title}`:"视频管理";
  document.title=selected?`${selected.series_title} · 视频管理`:"视频管理";
  if(selected){const url=new URL(location);url.searchParams.delete(isManual()?'ranking_id':'catalog_id');url.searchParams.set(isManual()?'catalog_id':'ranking_id',isManual()?state.ranking.catalog_id:state.ranking.id);url.searchParams.set("series_id",state.seriesId);url.hash="";history.replaceState(null,"",url);}
  const context=new URLSearchParams(contextQuery());
  $('#playback-context').hidden=!selected;$('#playback-context').href=`/watch?${context}`;
  $('#delivery-context').href=`/control/system?ranking_id=${state.ranking.id}#delivery`;
  $('#delivery-context').hidden=isManual();
  $('#source-review-tab').href=`/control/reviews?${context}&run_id=${state.ranking.run_id}`;
  if(isManual())$('#source-review-tab').href=`/control/reviews?series_id=${state.seriesId}`;
  const opened=new Set([...document.querySelectorAll("#catalogs details[open]")].map(node=>node.id));
  $("#catalogs").innerHTML=catalogs.map(c=>{
    const item=state.ranking.items.find(row=>row.series_id===c.series_id&&row.selected),s=c.summary;
    const playable=c.episodes.find(row=>row.file_url),feedback=state.messages.get(`series-${c.series_id}`);
    const stats=[["沿用已审核资源",s.reused||0],["已发现",s.discovered],["确认公开",s.public],["已加入队列",s.queued],["服务器已获取",s.downloaded],["自动校验通过",s.verified],["人工抽检通过",s.manual_passed]];
    return `<section class="panel" id="series-${c.series_id}"><div class="panel-heading"><div><p class="eyebrow">${isManual()?'指定收录':`TOP ${item.position}`} · ${esc(item.author_name)}</p><h2>${esc(item.series_title)}</h2></div><div class="actions"><a class="button subtle" href="${esc(item.author_url||item.original_url)}" target="_blank" rel="noopener noreferrer">核对原作者 ↗</a>${item.collection_url?`<a class="button subtle" href="${esc(item.collection_url)}" target="_blank" rel="noopener noreferrer">合集 ↗</a>`:""}<button type="button" class="subtle" data-discover="${c.id}" ${c.source&&!c.has_more?'disabled':''}>${c.source?(c.has_more?"继续读取下一页目录":"已读到目录末页"):"读取原作者目录"}</button></div></div>${isManual()?`<div class="collection-settings"><label>标题筛选词（留空可查看作者所有作品）<input data-discovery-keyword value="${esc(item.series_title)}" maxlength="255"></label><p class="muted">每次读取一页；匹配标题只作筛选，新增视频仍须逐集确认正片归属。需要登录或验证时请到<a href="/control/system">系统与交付</a>处理。</p><button type="button" class="subtle" data-publication="${c.id}" data-published="${!c.published}">${c.published?'从网站隐藏':'显示到网站'}</button><p class="muted">${c.published?'已发布，只有自动校验和人工抽检均通过的公开正片可供游客播放。':'尚未发布；至少一集完成自动校验和人工抽检后可显示到网站。'}</p></div>`:''}<div class="stats">${stats.map(([name,value])=>`<div class="stat"><strong>${value}</strong>${name}</div>`).join("")}</div><p class="note"><span class="badge ${s.public&&s.verified===s.public?"ok":"warn"}">${esc(s.completion)}</span> <span class="badge">${esc(s.full_series)}</span><br>目录${c.checked?"已人工核对":"待核对"} · 全剧总集数 ${c.total_episodes??"未知"} · ${s.pending} 条待核验<br>${s.reused?`已沿用 ${s.reused} 集已审核资源，无需重复下载和审核。<br>`:""}${esc(c.note)}${s.missing_numbers?.length?`<br>当前目录缺少集号：${s.missing_numbers.slice(0,60).join('、')}${s.missing_numbers.length>60?'…':''}`:''}${s.duplicate_numbers?.length?`<br>重复集号待核对：${s.duplicate_numbers.join('、')}`:''}</p>
    ${window.DiscoveryPanel.render(c)}<div class="actions catalog-tools"><button type="button" data-trial="${c.id}">${downloadLabel(c)}</button>${playable?`<a class="button subtle" data-play="${playable.id}" href="${watchUrl(playable)}">审核预览</a>`:""}<button type="button" class="subtle" data-batch="${c.id}" ${!s.trial_passed?"disabled":""}>批量准备已确认正片</button></div><p class="status-bar${feedback?.error?" error":""}" data-feedback role="status" aria-live="polite" ${feedback?"":"hidden"}>${esc(feedback?.text)}</p>${!s.trial_passed?'<p class="muted">先准备一集视频，再进入“人工抽检”确认开头、中段、结尾和声音，通过后才可批量准备。观看和保存请前往播放区。</p>':""}
    <details class="catalog-tools" id="add-${c.id}"><summary>手动补充正片链接</summary><form class="inline-form" data-links="${c.id}"><label class="muted">每行一条完整视频链接，添加后逐集确认归属<textarea name="links" rows="3" required placeholder="https://www.douyin.com/video/视频ID"></textarea></label><button>添加到本剧目录</button></form></details>
    <details class="catalog-tools" id="scope-${c.id}"><summary>核对全剧目录与缺集说明</summary><form class="inline-form" data-scope="${c.id}"><div class="form-grid"><label>已核实的全剧总集数（未知留空）<input name="total_episodes" type="number" min="1" step="1" value="${c.total_episodes??""}"></label><label>目录与缺集说明<textarea name="note" required rows="2">${esc(c.note)}</textarea></label></div><label class="check"><input name="checked" type="checkbox" ${c.checked?"checked":""}>已核对原作者主页或合集，注明已发现范围与缺集原因</label><button>保存目录说明</button></form></details>
    ${reviewToolbar(c)}<div class="table-wrap"><table><thead><tr><th>正片与来源</th><th>目录核验</th><th>下载与校验</th><th>操作</th></tr></thead><tbody>${(videoView==="inspection"?c.episodes.filter(row=>row.media):reviewRows(c)).map(row=>episodeRow(row,c)).join("")}</tbody></table></div></section>`;
  }).join("");for(const id of opened){const node=document.getElementById(id);if(node)node.open=true;}
  for(const c of catalogs){const ids=new Set(reviewRows(c).map(row=>row.id));for(const id of reviewSelection(c))if(!ids.has(id))reviewSelection(c).delete(id);syncReviewSelection(c);const input=$(`#series-${c.series_id} [data-discovery-keyword]`);if(input&&discoveryKeywords.has(c.id))input.value=discoveryKeywords.get(c.id);if(isManual()&&c.source&&!c.has_more){const button=$(`#series-${c.series_id} [data-discover]`);button.disabled=false;button.textContent='重新读取原作者目录';}}
  window.refreshQuickReviews?.();
}
function watchUrl(row) {
  const catalog=state.catalogs.find(c=>c.episodes.some(episode=>episode.id===row.id));
  return `/downloads/watch?${contextQuery()}&episode_id=${row.id}`;
}
function episodeRow(row,c) {
  const job=row.job,media=row.media;
  const repair=needsRepair(row);
  const warning=reviewAttention(row,c);
  return `<tr id="episode-${row.id}"><td><label class="check catalog-tools episode-select"><input type="checkbox" data-review-select="${row.id}" aria-label="选择${esc(row.title)}">选择本条</label><strong>${row.episode_range||row.episode_number?`第 ${esc(row.episode_range||row.episode_number)} 集 · `:"集号待确认 · "}${esc(row.title)}</strong>${row.reuse_unavailable?'<small>沿用资源已失效，请重新准备；原审核未用于新文件。</small>':""}${warning?`<small class="catalog-tools review-warning">${esc(warning)}</small>`:''}<small>视频 ${esc(row.video_id)}</small><a href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">原视频 ↗</a>${media?`<small>${esc(media.relative_path)}</small>`:""}</td><td class="catalog-tools"><span class="badge ${row.status==="approved"?"ok":"warn"}">${names[row.status]}</span><small>${names[row.access]}</small><small>${esc(row.note)}</small><button type="button" class="subtle" data-review="${row.id}">核对正片</button></td><td>${job?`<span class="badge ${job.status==="verified"?"ok":"warn"}">${esc(job.status_label)}${job.trial?" · 试下载":""}</span><small>${esc(job.message)}</small><small>尝试 ${job.attempts}/${job.max_attempts}${job.bytes_downloaded?` · ${(job.bytes_downloaded/1048576).toFixed(1)} MB`:""}</small>`:'<span class="muted">尚未加入队列</span>'}${media?`<small>${media.size.toLocaleString()} 字节 · ${media.duration.toFixed(1)} 秒</small><small>${esc(media.validation_note)}</small><small>${!media.exists?"文件缺失":names[media.manual_status]||"等待人工抽看"}</small><details><summary>摘要与工具记录</summary><small>SHA-256 ${esc(media.sha256)}</small><small>${esc(job.tool)}</small></details>`:""}</td><td>${row.file_url?`<a class="button subtle" href="${watchUrl(row)}">审核预览</a>${row.reused?'<span class="badge ok">已沿用审核结论</span>':`<button type="button" data-media="${row.id}">人工抽检</button>`}`:`<a class="button subtle" href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">播放原视频 ↗</a><small>${media?"本地文件缺失，请恢复 / 重试":"下载完成后可在此播放本地视频"}</small>`}${job&&["queued","downloading","validating"].includes(job.status)?`<button type="button" class="subtle" data-job="${job.id}" data-action="pause">暂停</button>`:""}${job&&(["failed","paused","cancelled"].includes(job.status)||repair)?`<button type="button" class="subtle" data-job="${job.id}" data-action="resume" ${job.attempts>=job.max_attempts?"disabled":""}>恢复 / 重试</button>`:""}${job&&!["verified","cancelled"].includes(job.status)?`<button type="button" class="subtle" data-job="${job.id}" data-action="cancel">取消</button>`:""}</td></tr>`;
}
let batchReviewTarget=null;
function openBatchReview(c,status){
  const rows=reviewRows(c).filter(row=>reviewSelection(c).has(row.id));
  if(!rows.length)throw new Error('请先勾选需要处理的作品');
  if(rows.length>1000)throw new Error('一次最多处理 1000 条，请减少勾选数量');
  batchReviewTarget={catalogId:c.id,episodeIds:rows.map(row=>row.id),selection:$('#download-ranking').value,seriesId:state.seriesId};
  const form=$('#batch-review-form');form.reset();form.elements.status.value=status;
  form.querySelector('.modal-error').textContent='';
  $('#batch-review-title').textContent=`批量${status==='approved'?'确认正片':'排除'} · ${rows.length} 条`;
  $('#batch-review-checks').hidden=status!=='approved';
  for(const input of $('#batch-review-checks').querySelectorAll('input'))input.required=status==='approved';
  $('#batch-review-list').innerHTML=rows.map(row=>`<li><a href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">${esc(row.title)} ↗</a><small>集号：${esc(row.episode_range||row.episode_number||'未知')} · ${names[row.status]}${reviewAttention(row,c)?` · ${esc(reviewAttention(row,c))}`:''}</small></li>`).join('');
  $('#batch-review-submit').textContent=status==='approved'?'确认所选作品为公开正片':'排除所选作品';
  $('#batch-review-dialog').showModal();
}
$('#catalogs').addEventListener('change',event=>{
  const input=event.target,c=state.catalogs.find(row=>`series-${row.series_id}`===input.closest('.panel')?.id);if(!c)return;
  if(input.matches('[data-review-select]')){input.checked?reviewSelection(c).add(Number(input.dataset.reviewSelect)):reviewSelection(c).delete(Number(input.dataset.reviewSelect));syncReviewSelection(c);}
  if(input.matches('[data-review-filter]')){reviewFilters.set(c.id,input.value);reviewSelection(c).clear();render();}
});
$("#catalogs").addEventListener("click",async event=>{
  const button=event.target.closest("button[type=button]");if(!button||button.disabled||button.dataset.discoveryAction)return;
  const section=button.closest(".panel"),report=(text,error=false)=>notify(text,error,section);
  await action(async()=>{
    if(button.dataset.reviewSelectAll){const c=catalogById(button.dataset.reviewSelectAll);for(const row of reviewRows(c))reviewSelection(c).add(row.id);syncReviewSelection(c);return;}
    if(button.dataset.reviewClear){const c=catalogById(button.dataset.reviewClear);reviewSelection(c).clear();syncReviewSelection(c);return;}
    if(button.dataset.reviewBatch){openBatchReview(catalogById(button.dataset.reviewBatch),button.dataset.decision);return;}
    if(button.dataset.review){const row=episodeById(button.dataset.review),form=$("#episode-form");form.reset();form.querySelector(".modal-error").textContent="";for(const key of ["id","title","episode_number","status","access","note","evidence_url"])form.elements[key].value=(key==="episode_number"?row.episode_range||row.episode_number:row[key])??"";form.elements.evidence_url.value=row.evidence_url||row.original_url;$("#episode-dialog").showModal();return;}
    if(button.dataset.media){const row=episodeById(button.dataset.media),media=row.media,form=$("#media-form");form.reset();form.querySelector(".modal-error").textContent="";form.elements.id.value=media.id;form.elements.note.value=media.manual_note;$("#preview").src=`/api/downloads/media/${media.id}/file`;$("#media-info").textContent=`${media.size.toLocaleString()} 字节 · ${media.duration.toFixed(1)} 秒\n${media.validation_note}\n${media.relative_path}`;$("#media-dialog").showModal();return;}
    const selection=$("#download-ranking").value,request=state.request;
    const stillCurrent=()=>selection===$("#download-ranking").value&&request===state.request;
    if(button.dataset.discover){const input=section.querySelector('[data-discovery-keyword]'),c=catalogById(button.dataset.discover);const result=await api(`/api/downloads/catalogs/${button.dataset.discover}/discover`,input?{keyword:input.value,restart:!!c.source&&!c.has_more}:{});if(!stillCurrent())return;report(`新增 ${result.added} 条。${result.message}`);}
    if(button.dataset.publication){const result=await api(`/api/downloads/collections/${button.dataset.publication}/publication`,{published:button.dataset.published==='true'});if(!stillCurrent())return;report(result.message);}
    if(button.dataset.trial){await downloadOne(catalogById(button.dataset.trial),report);}
    if(button.dataset.batch){const c=catalogById(button.dataset.batch),ids=c.episodes.filter(row=>row.status==="approved"&&row.access==="public"&&!row.job).map(row=>row.id);if(!ids.length)throw new Error("所有已确认公开正片都已有任务，可查看进度或恢复失败任务");const result=await api(`/api/downloads/catalogs/${c.id}/queue`,{episode_ids:ids,trial:false});report(result.message);}
    if(button.dataset.job){const result=await api(`/api/downloads/jobs/${button.dataset.job}/control`,{action:button.dataset.action});report(result.message);}await load();
  })(event);
});
$("#catalogs").addEventListener("submit",action(async event=>{const form=event.target;if(form.dataset.discoveryRecheck)return;const data=Object.fromEntries(new FormData(form));if(form.dataset.links){const result=await api(`/api/downloads/catalogs/${form.dataset.links}/links`,data);notify(`已添加 ${result.added} 条，请逐集核验。`);}if(form.dataset.scope){data.checked=form.elements.checked.checked;data.total_episodes=data.total_episodes?Number(data.total_episodes):null;await api(`/api/downloads/catalogs/${form.dataset.scope}/review`,data);notify("目录说明已保存。");}form.querySelector("button").focus();await load();}));
$("#episode-form").addEventListener("submit",action(async()=>{const form=$("#episode-form"),data=Object.fromEntries(new FormData(form));data.episode_number=data.episode_number.trim()||null;for(const key of ["ownership","main_episode","not_duplicate"])data[key]=form.elements[key].checked;await api(`/api/downloads/episodes/${data.id}/review`,data);$("#episode-dialog").close();await load();notify("目录核验已保存。");}));
$('#batch-review-form').addEventListener('submit',action(async()=>{
  const target=batchReviewTarget;if(!target||target.selection!==$('#download-ranking').value||target.seriesId!==state.seriesId)throw new Error('当前目录已切换，请重新选择作品');
  const form=$('#batch-review-form'),data={episode_ids:target.episodeIds,status:form.elements.status.value,note:form.elements.note.value};
  for(const key of ['ownership','main_episode','not_duplicate','public_access'])data[key]=form.elements[key].checked;
  const result=await api(`/api/downloads/catalogs/${target.catalogId}/episodes/review`,data);
  if(target.selection!==$('#download-ranking').value||target.seriesId!==state.seriesId)return;
  reviewSelections.delete(target.catalogId);await load();$('#batch-review-dialog').close();notify(result.message);
}));
$("#media-form").addEventListener("submit",action(async()=>{const form=$("#media-form"),data=Object.fromEntries(new FormData(form));for(const key of ["beginning","middle","ending","sound"])data[key]=form.elements[key].checked;await api(`/api/downloads/media/${data.id}/review`,data);$("#media-dialog").close();await load();notify("人工抽检已保存。");}));
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>document.getElementById(button.dataset.close).close()));
$("#media-dialog").addEventListener("close",()=>{$("#preview").pause();$("#preview").removeAttribute("src");$("#preview").load();});
$("#refresh").addEventListener("click",action(()=>load()));
$("#download-ranking").addEventListener("change",action(async()=>{reviewSelections.clear();state.seriesId=null;state.request++;$("#message").hidden=true;$("#catalogs").innerHTML="";const url=new URL(location),id=$("#download-ranking").value,manual=id.startsWith('manual:');url.searchParams.delete(manual?'ranking_id':'catalog_id');url.searchParams.set(manual?'catalog_id':'ranking_id',manual?id.slice(7):id);url.searchParams.delete("series_id");url.hash="";history.replaceState(null,"",url);await load();}));
$("#download-series").addEventListener("change",action(async()=>{reviewSelections.clear();state.seriesId=$("#download-series").value;state.request++;$("#message").hidden=true;render();await load();}));
async function loadChoices(selected){
  const [rankings,collections]=await Promise.all([api('/api/downloads/rankings'),api('/api/downloads/collections')]);
  $('#download-ranking').innerHTML=collections.map(row=>`<option value="manual:${row.id}">指定收录 · ${esc(row.title)}</option>`).join('')+rankings.map(row=>`<option value="${row.id}">${esc(row.name)} · V${row.version} · 榜单 ${row.id}</option>`).join('')||'<option value="">尚无目录，请先收录指定作品</option>';
  const params=new URL(location).searchParams,requested=selected||(params.get('catalog_id')?`manual:${params.get('catalog_id')}`:params.get('ranking_id'));
  if([...$('#download-ranking').options].some(row=>row.value===requested))$('#download-ranking').value=requested;
  if(rankings.length||collections.length)await load();else{$('#empty').hidden=false;$('#empty').textContent='点击上方“收录指定作品”，建立本剧目录，无需生成榜单。';}
}
let availableSeries=[];
$('#collection-series').addEventListener('change',()=>{const row=availableSeries.find(item=>String(item.id)===$('#collection-series').value);$('#collection-form').elements.source_url.value=row?.author_url||'';});
$('#collection-form').addEventListener('submit',action(async()=>{const form=$('#collection-form'),data=Object.fromEntries(new FormData(form));data.series_id=Number(data.series_id);const result=await api('/api/downloads/collections',data);state.seriesId=String(data.series_id);state.request++;await loadChoices(`manual:${result.id}`);$('#collection-create').open=false;notify(result.message);}));
$('#catalogs').addEventListener('input',event=>{if(event.target.matches('[data-discovery-keyword]')){const c=state.catalogs.find(row=>`series-${row.series_id}`===event.target.closest('.panel').id);if(c)discoveryKeywords.set(c.id,event.target.value);}});
(async()=>{availableSeries=await api('/api/downloads/series');$('#collection-series').innerHTML='<option value="">选择已通过核验的作品</option>'+availableSeries.map(row=>`<option value="${row.id}">${esc(row.title)} · ${esc(row.author_name)} · 作品 ${row.id}</option>`).join('');await loadChoices();})().catch(error=>notify(error.message,true));



 document.querySelectorAll('[data-video-tab]').forEach(button=>button.addEventListener('click',()=>{reviewSelections.clear();videoView=button.dataset.videoTab;const url=new URL(location);url.searchParams.set('view',videoView);history.replaceState(null,'',url);if(state.ranking)render();}));

$('#catalogs').addEventListener('click',async event=>{const button=event.target.closest('[data-discovery-action]');if(!button)return;state.busy++;button.disabled=true;try{const r=await window.DiscoveryPanel.send(button.dataset.discoveryCatalog,button.dataset.discoveryAction);notify(r.message);await load();}catch(e){notify(e.message,true);}finally{state.busy--;if(button.isConnected)button.disabled=false;}});
$('#catalogs').addEventListener('submit',async event=>{const form=event.target.closest('[data-discovery-recheck]');if(!form)return;event.preventDefault();state.busy++;try{const r=await window.DiscoveryPanel.send(form.dataset.discoveryRecheck,'recheck',form.elements.aliases.value.split('\n').map(s=>s.trim()).filter(Boolean));notify(r.message);await load();}catch(e){notify(e.message,true);}finally{state.busy--;}});
