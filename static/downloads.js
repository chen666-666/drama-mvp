"use strict";
const $ = selector=>document.querySelector(selector);
const esc = value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state={ranking:null,catalogs:[],timer:null,request:0,busy:0,messages:new Map()};
const names={pending:"待核验",approved:"正片已确认",excluded:"已排除",public:"公开可访问",unknown:"访问范围未知",private:"私密",paid:"付费",unavailable:"无法访问",passed:"人工通过",rejected:"抽检发现问题"};
function notify(text,error=false,section=null) {$("#message").hidden=false;$("#message").textContent=text;$("#message").classList.toggle("error",error);if(section){state.messages.set(section.id,{text,error});const feedback=section.querySelector("[data-feedback]");if(feedback){feedback.hidden=false;feedback.textContent=text;feedback.classList.toggle("error",error);}}const dialog=document.querySelector("dialog[open]");if(dialog&&error) dialog.querySelector(".modal-error").textContent=text;}
async function api(url,data) {const response=await fetch(url,data===undefined?{cache:"no-store"}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});const result=await response.json();if(!response.ok) throw new Error(typeof result.detail === "string"?result.detail:"操作未完成，请检查填写内容");return result;}
function action(handler) {return async event=>{event.preventDefault();const button=event.submitter||event.target.closest("button"),section=event.target.closest("#catalogs .panel"),label=button?.textContent,disabled=button?.disabled;state.busy++;if(button){button.disabled=true;button.textContent="处理中…";}try{await handler(event);}catch(error){notify(error.message,true,section);}finally{state.busy--;if(button&&button.isConnected){button.disabled=disabled;button.textContent=label;}}};}
function episodeById(id) {return state.catalogs.flatMap(c=>c.episodes).find(row=>row.id===Number(id));}
function catalogById(id) {return state.catalogs.find(row=>row.id===Number(id));}
function needsRepair(row) {return row.job?.status==="verified"&&(!row.media||!row.media.exists||row.media.manual_status==="rejected");}
async function downloadOne(c,report) {
  report("正在检查下载状态…");
  const latest=await api(`/api/downloads/rankings/${state.ranking.id}`);
  c=latest.catalogs.find(row=>row.id===c.id);
  if(!c)throw new Error("当前作品目录已变更，请刷新后重试。");
  const eligible=c.episodes.filter(row=>row.status==="approved"&&row.access==="public");
  if(!eligible.length)throw new Error("暂无已确认公开的正片，请先核对正片与访问范围。");
  // Keep the first-download review gate and reuse persistent tasks instead of duplicating them.
  const trial=!c.summary.trial_passed&&eligible.find(row=>row.job?.trial);
  const first=trial||eligible.find(row=>!row.job)||eligible.find(row=>needsRepair(row)||["failed","paused","cancelled"].includes(row.job?.status))||eligible[0];
  const waiting=state.workerRunning?"可在下方查看下载进度。":"下载工作进程未运行，请使用 start.ps1 或 start-worker.ps1 启动后继续。";
  if(!first.job){
    report("正在提交下载，请稍候…");
    await api(`/api/downloads/catalogs/${c.id}/queue`,{episode_ids:[first.id],trial:true});
    report(`已加入下载队列。${waiting}`);return;
  }
  if(needsRepair(first)||["failed","paused","cancelled"].includes(first.job.status)){
    if(first.job.attempts>=first.job.max_attempts)throw new Error("已达到三次尝试上限，请检查来源后新建榜单版本重试。");
    report("正在恢复下载，请稍候…");
    await api(`/api/downloads/jobs/${first.job.id}/control`,{action:"resume"});
    report(`已恢复下载。${waiting}`);return;
  }
  if(first.job.status==="verified"){
    report(c.summary.trial_passed?"已下载，可点击“播放”观看。":"本集已下载，可点击“播放”观看并完成人工抽检，之后可批量下载。" );return;
  }
  report(`${first.job.status_label}，无需重复提交。${waiting}`);
}
async function loadStatus(){const status=await api("/api/downloads/status");state.workerRunning=status.running;$("#worker-status").textContent=`${status.running?"● 下载工作进程运行中":"○ 下载工作进程未运行，请使用 start.ps1 启动，或运行 start-worker.ps1"} · ${status.media_tools.ffprobe?"媒体检查已就绪":"缺少媒体检查工具，请运行 scripts/install_media.py"}\n保存位置：${status.video_directory}`;}
async function load(poll=false) {
  clearTimeout(state.timer);const id=$("#download-ranking").value;if(!id)return;
  if(poll&&(state.busy||document.querySelector("dialog[open]")||document.activeElement?.closest("#catalogs form"))){state.timer=setTimeout(()=>load(true).catch(error=>notify(error.message,true)),3000);return;}
  const request=++state.request,result=await api(`/api/downloads/rankings/${id}`);if(request!==state.request||id!==$("#download-ranking").value)return;
  state.ranking=result.ranking;state.catalogs=result.catalogs;$("#ranking-info").textContent=`${result.ranking.title} · V${result.ranking.version} · ${result.ranking.disclaimer} ${result.ranking.shortfall||""}`;$("#empty").hidden=!!result.catalogs.length;render();await loadStatus();
  if(!poll&&location.hash)document.getElementById(location.hash.slice(1))?.scrollIntoView({block:"start"});state.timer=setTimeout(()=>load(true).catch(error=>notify(error.message,true)),4000);
}
function render() {
  const opened=new Set([...document.querySelectorAll("#catalogs details[open]")].map(node=>node.id));
  $("#catalogs").innerHTML=state.catalogs.map(c=>{
    const item=state.ranking.items.find(row=>row.series_id===c.series_id&&row.selected),s=c.summary;
    const playable=c.episodes.find(row=>row.media?.exists),feedback=state.messages.get(`series-${c.series_id}`);
    const stats=[["已发现",s.discovered],["确认公开",s.public],["已加入队列",s.queued],["已下载",s.downloaded],["校验通过",s.verified],["人工通过",s.manual_passed]];
    return `<section class="panel" id="series-${c.series_id}"><div class="panel-heading"><div><p class="eyebrow">TOP ${item.position} · ${esc(item.author_name)}</p><h2>${esc(item.series_title)}</h2></div><div class="actions"><a class="button subtle" href="${esc(item.author_url||item.original_url)}" target="_blank" rel="noopener noreferrer">核对原作者 ↗</a>${item.collection_url?`<a class="button subtle" href="${esc(item.collection_url)}" target="_blank" rel="noopener noreferrer">合集 ↗</a>`:""}<button type="button" class="subtle" data-discover="${c.id}">${c.source&&c.has_more?"继续读取下一页目录":"读取原作者目录"}</button></div></div><div class="stats">${stats.map(([name,value])=>`<div class="stat"><strong>${value}</strong>${name}</div>`).join("")}</div><p class="note"><span class="badge ${s.public&&s.verified===s.public?"ok":"warn"}">${esc(s.completion)}</span> <span class="badge">${esc(s.full_series)}</span><br>目录${c.checked?"已人工核对":"待核对"} · 全剧总集数 ${c.total_episodes??"未知"} · ${s.pending} 条待核验<br>${esc(c.note)}</p>
    <div class="actions"><button type="button" data-trial="${c.id}">下载</button>${playable?`<button type="button" class="subtle" data-play="${playable.id}">播放</button>`:""}<button type="button" class="subtle" data-batch="${c.id}" ${!s.trial_passed?"disabled":""}>下载全部已确认公开正片</button></div><p class="status-bar${feedback?.error?" error":""}" data-feedback role="status" aria-live="polite" ${feedback?"":"hidden"}>${esc(feedback?.text)}</p>${!s.trial_passed?'<p class="muted">点击“下载”先保存一集到本地；完成后点击“播放”观看并抽检，确认声音及开头、中段、结尾，再开始批量下载。未下载时可点击“播放原视频”前往抖音观看。</p>':""}
    <details id="add-${c.id}"><summary>手动补充正片链接</summary><form class="inline-form" data-links="${c.id}"><label class="muted">每行一条完整视频链接，添加后逐集确认归属<textarea name="links" rows="3" required placeholder="https://www.douyin.com/video/视频ID"></textarea></label><button>添加到本剧目录</button></form></details>
    <details id="scope-${c.id}"><summary>核对全剧目录与缺集说明</summary><form class="inline-form" data-scope="${c.id}"><div class="form-grid"><label>已核实的全剧总集数（未知留空）<input name="total_episodes" type="number" min="1" step="1" value="${c.total_episodes??""}"></label><label>目录与缺集说明<textarea name="note" required rows="2">${esc(c.note)}</textarea></label></div><label class="check"><input name="checked" type="checkbox" ${c.checked?"checked":""}>已核对原作者主页或合集，注明已发现范围与缺集原因</label><button>保存目录说明</button></form></details>
    <div class="table-wrap"><table><thead><tr><th>正片与来源</th><th>目录核验</th><th>下载与校验</th><th>操作</th></tr></thead><tbody>${c.episodes.map(episodeRow).join("")}</tbody></table></div></section>`;
  }).join("");for(const id of opened){const node=document.getElementById(id);if(node)node.open=true;}
}
function episodeRow(row) {
  const job=row.job,media=row.media;
  const repair=needsRepair(row);
  return `<tr><td><strong>${row.episode_number?`第 ${row.episode_number} 集 · `:"集号待确认 · "}${esc(row.title)}</strong><small>视频 ${esc(row.video_id)}</small><a href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">原视频 ↗</a>${media?`<small>${esc(media.relative_path)}</small>`:""}</td><td><span class="badge ${row.status==="approved"?"ok":"warn"}">${names[row.status]}</span><small>${names[row.access]}</small><small>${esc(row.note)}</small><button type="button" class="subtle" data-review="${row.id}">核对正片</button></td><td>${job?`<span class="badge ${job.status==="verified"?"ok":"warn"}">${esc(job.status_label)}${job.trial?" · 试下载":""}</span><small>${esc(job.message)}</small><small>尝试 ${job.attempts}/${job.max_attempts}${job.bytes_downloaded?` · ${(job.bytes_downloaded/1048576).toFixed(1)} MB`:""}</small>`:'<span class="muted">尚未加入队列</span>'}${media?`<small>${media.size.toLocaleString()} 字节 · ${media.duration.toFixed(1)} 秒</small><small>${esc(media.validation_note)}</small><small>${!media.exists?"文件缺失":names[media.manual_status]||"等待人工抽看"}</small><details><summary>摘要与工具记录</summary><small>SHA-256 ${esc(media.sha256)}</small><small>${esc(job.tool)}</small></details>`:""}</td><td>${media&&media.exists?`<button type="button" data-media="${row.id}">播放</button>`:`<a class="button subtle" href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">播放原视频 ↗</a><small>${media?"本地文件缺失，请恢复 / 重试":"下载完成后可在此播放本地视频"}</small>`}${job&&["queued","downloading","validating"].includes(job.status)?`<button type="button" class="subtle" data-job="${job.id}" data-action="pause">暂停</button>`:""}${job&&(["failed","paused","cancelled"].includes(job.status)||repair)?`<button type="button" class="subtle" data-job="${job.id}" data-action="resume" ${job.attempts>=job.max_attempts?"disabled":""}>恢复 / 重试</button>`:""}${job&&!["verified","cancelled"].includes(job.status)?`<button type="button" class="subtle" data-job="${job.id}" data-action="cancel">取消</button>`:""}</td></tr>`;
}
$("#catalogs").addEventListener("click",async event=>{
  const button=event.target.closest("button[type=button]");if(!button||button.disabled)return;
  const section=button.closest(".panel"),report=(text,error=false)=>notify(text,error,section);
  await action(async()=>{
    if(button.dataset.review){const row=episodeById(button.dataset.review),form=$("#episode-form");form.reset();form.querySelector(".modal-error").textContent="";for(const key of ["id","title","episode_number","status","access","note","evidence_url"])form.elements[key].value=row[key]??"";form.elements.evidence_url.value=row.evidence_url||row.original_url;$("#episode-dialog").showModal();return;}
    if(button.dataset.media||button.dataset.play){const row=episodeById(button.dataset.media||button.dataset.play),media=row.media,form=$("#media-form");form.reset();form.querySelector(".modal-error").textContent="";form.elements.id.value=media.id;form.elements.note.value=media.manual_note;$("#preview").src=`/api/downloads/media/${media.id}/file`;$("#media-info").textContent=`${media.size.toLocaleString()} 字节 · ${media.duration.toFixed(1)} 秒\n${media.validation_note}\n${media.relative_path}`;$("#media-dialog").showModal();return;}
    if(button.dataset.discover){const result=await api(`/api/downloads/catalogs/${button.dataset.discover}/discover`,{});report(`新增 ${result.added} 条。${result.message}`);}
    if(button.dataset.trial){await downloadOne(catalogById(button.dataset.trial),report);}
    if(button.dataset.batch){const c=catalogById(button.dataset.batch),ids=c.episodes.filter(row=>row.status==="approved"&&row.access==="public"&&!row.job).map(row=>row.id);if(!ids.length)throw new Error("所有已确认公开正片都已有任务，可查看进度或恢复失败任务");const result=await api(`/api/downloads/catalogs/${c.id}/queue`,{episode_ids:ids,trial:false});report(result.message);}
    if(button.dataset.job){const result=await api(`/api/downloads/jobs/${button.dataset.job}/control`,{action:button.dataset.action});report(result.message);}await load();
  })(event);
});
$("#catalogs").addEventListener("submit",action(async event=>{const form=event.target,data=Object.fromEntries(new FormData(form));if(form.dataset.links){const result=await api(`/api/downloads/catalogs/${form.dataset.links}/links`,data);notify(`已添加 ${result.added} 条，请逐集核验。`);}if(form.dataset.scope){data.checked=form.elements.checked.checked;data.total_episodes=data.total_episodes?Number(data.total_episodes):null;await api(`/api/downloads/catalogs/${form.dataset.scope}/review`,data);notify("目录说明已保存。");}form.querySelector("button").focus();await load();}));
$("#episode-form").addEventListener("submit",action(async()=>{const form=$("#episode-form"),data=Object.fromEntries(new FormData(form));data.episode_number=data.episode_number?Number(data.episode_number):null;for(const key of ["ownership","main_episode","not_duplicate"])data[key]=form.elements[key].checked;await api(`/api/downloads/episodes/${data.id}/review`,data);$("#episode-dialog").close();await load();notify("目录核验已保存。");}));
$("#media-form").addEventListener("submit",action(async()=>{const form=$("#media-form"),data=Object.fromEntries(new FormData(form));for(const key of ["beginning","middle","ending","sound"])data[key]=form.elements[key].checked;await api(`/api/downloads/media/${data.id}/review`,data);$("#media-dialog").close();await load();notify("人工抽检已保存。");}));
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>document.getElementById(button.dataset.close).close()));
$("#media-dialog").addEventListener("close",()=>{$("#preview").pause();$("#preview").removeAttribute("src");$("#preview").load();});
$("#refresh").addEventListener("click",action(()=>load()));
$("#download-ranking").addEventListener("change",action(async()=>{$("#package-link").hidden=true;const url=new URL(location);url.searchParams.set("ranking_id",$("#download-ranking").value);url.hash="";history.replaceState(null,"",url);await load();}));
$("#export-package").addEventListener("click",action(async()=>{notify("正在核对文件并生成资料包，大文件可能需要一些时间。");const result=await api(`/api/downloads/rankings/${state.ranking.id}/export`,{});$("#package-link").href=result.url;$("#package-link").hidden=false;notify(result.message);}));
(async()=>{const rankings=await api("/api/downloads/rankings");$("#download-ranking").innerHTML=rankings.length?rankings.map(row=>`<option value="${row.id}">${esc(row.name)} · V${row.version} · 榜单 ${row.id}</option>`).join(""):'<option value="">尚无榜单</option>';const requested=new URL(location).searchParams.get("ranking_id");if(rankings.some(row=>String(row.id)===requested))$("#download-ranking").value=requested;$("#export-package").disabled=!rankings.length;if(rankings.length)await load();else{$("#empty").hidden=false;await loadStatus();}})().catch(error=>notify(error.message,true));
