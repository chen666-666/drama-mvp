"use strict";
const video=document.getElementById('watch-video'), select=document.getElementById('watch-episode');
const errorBox=document.getElementById('watch-error'), start=document.getElementById('watch-start');
const fullscreen=document.getElementById('watch-fullscreen'), retry=document.getElementById('watch-retry');
const manage=document.body.dataset.manage==='true', publicSite=document.body.dataset.public==='true';
const byId=id=>document.getElementById(id);
const params=new URL(location).searchParams, rankingId=params.get('ranking_id'), seriesId=params.get('series_id');
const catalogId=params.get('catalog_id'),manual=!!catalogId;
const context=manual?`catalog_id=${catalogId}&series_id=${seriesId||''}`:`ranking_id=${rankingId}&series_id=${seriesId}`;
let episodes=[],files=[],playRequest=0,loadController,loadRequest=0,episodeGroup='';
let fileGroup='',downloadTimer=null,downloadQueue=[],downloadStarted=0,downloadTotal=0;
const selectedFiles=new Set();
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function showError(message){errorBox.textContent=message;errorBox.hidden=false;retry.hidden=false;}
async function read(url,signal){const controller=new AbortController(),abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();const timer=setTimeout(abort,15000);try{const response=await fetch(url,{cache:'no-store',signal:controller.signal});if(!response.ok)throw new Error(response.status===401?'登录已失效，请重新登录后继续。':'资料读取失败，请重试。');return await response.json();}finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}}
async function startPlayback(){const request=++playRequest;start.hidden=true;try{await video.play();}catch(error){if(request!==playRequest)return;if(error.name==='NotAllowedError')start.hidden=false;else if(error.name!=='AbortError')showError('视频暂时无法播放，请重试或返回查看剧集状态。');}}
function episodeSelection(row){
  const number=Number(row.episode_number);
  if(Number.isSafeInteger(number)&&number>0)return {label:String(number),description:`第 ${number} 集`,anchor:number,order:number*2,kind:'single'};
  // Ranges are display hints only; never turn a compilation into a stored single episode.
  const title=String(row.episode_range||row.title||'').normalize('NFKC').split('#')[0];
  const match=title.match(/(?:^|[^\d])(\d{1,15})\s*(?:集\s*)?[-–—~～至到]\s*(?:第\s*)?(\d{1,15})(?!\d)(?=\s*(?:集|合集|补发|\(\s*补发|$))/);
  if(match){
    const first=Number(match[1]),last=Number(match[2]);
    if(first>0&&last>first){
      const anchor=first-1;
      return {label:`${first}–${last}`,description:`第 ${first}–${last} 集 · 合集`,first,last,anchor,order:anchor*2+1,kind:'collection'};
    }
  }
  return {label:'待定',description:'集号待确认',anchor:null,order:Infinity,kind:'unknown'};
}
function orderedEpisodes(rows){
  const prepared=rows.map(row=>({...row,selection:episodeSelection(row)}));
  const numbers=prepared.filter(row=>row.selection.kind==='single').map(row=>row.selection.anchor);
  for(const row of prepared){
    const selection=row.selection;
    if(selection.kind!=='collection')continue;
    // Follow the last available single in this range; otherwise precede the range.
    const anchor=numbers.reduce((anchor,number)=>number>=selection.first&&number<=selection.last?Math.max(anchor,number):anchor,selection.first-1);
    row.selection={...selection,anchor,order:anchor*2+1};
  }
  return prepared.sort((a,b)=>a.selection.order-b.selection.order||Number(a.id)-Number(b.id));
}
function episodeLabel(row){return `${(row.selection||episodeSelection(row)).description} · ${row.title}`;}
function groupFor(row){const anchor=row.selection.anchor;return anchor===null?'unknown':String(Math.floor((Math.max(1,anchor)-1)/30)*30+1);}
function renderEpisodeGrid(){
  const groups=[...new Set(episodes.map(groupFor))];
  byId('episode-ranges').innerHTML=groups.map(group=>`<button type="button" class="episode-range" data-episode-group="${group}" aria-pressed="${group===episodeGroup}">${group==='unknown'?'待定集号':`${group}–${Number(group)+29}`}</button>`).join('');
  byId('episode-list').innerHTML=episodes.map(row=>`<button type="button" class="episode-choice ${row.selection.kind==='collection'?'episode-collection':''}" data-episode="${row.id}" aria-label="${esc(episodeLabel(row))}" title="${esc(episodeLabel(row))}" aria-current="${String(row.id)===select.value}" ${groupFor(row)!==episodeGroup?'hidden':''}>${row.selection.kind==='collection'?'<small>合集</small>':''}<span>${esc(row.selection.label)}</span></button>`).join('');
  const ranges=byId('episode-ranges'),active=ranges.querySelector('[aria-pressed=true]');
  if(active){const bounds=ranges.getBoundingClientRect(),tab=active.getBoundingClientRect();ranges.scrollLeft+=Math.min(0,tab.left-bounds.left)+Math.max(0,tab.right-bounds.right);}
}
function syncEpisodeGrid(){
  const row=episodes.find(row=>String(row.id)===select.value);if(!row)return;
  episodeGroup=groupFor(row);renderEpisodeGrid();
  byId('episode-current').textContent=`正在播放 · ${row.selection.description}`;
}
function updateDownload(){if(manage)return;const selected=files.find(row=>String(row.id)===select.value);const link=byId('watch-download');link.hidden=!selected?.file_url;if(selected?.file_url)link.href=selected.file_url;else link.removeAttribute('href');}
function playEpisode(){const episode=episodes.find(row=>String(row.id)===select.value);if(!episode)return;errorBox.hidden=true;retry.hidden=true;video.src=episode.url;const url=new URL(location);url.searchParams.set('episode_id',episode.id);history.replaceState(null,'',url);syncEpisodeGrid();updateDownload();startPlayback();}
select.addEventListener('change',playEpisode);
byId('episode-list').addEventListener('click',event=>{const button=event.target.closest('[data-episode]');if(button){select.value=button.dataset.episode;playEpisode();byId('episode-list').querySelector(`[data-episode="${select.value}"]`).focus();}});
byId('episode-ranges').addEventListener('click',event=>{const button=event.target.closest('[data-episode-group]');if(button){episodeGroup=button.dataset.episodeGroup;renderEpisodeGrid();byId('episode-ranges').querySelector(`[data-episode-group="${episodeGroup}"]`).focus();}});
start.addEventListener('click',startPlayback);
video.addEventListener('playing',()=>{start.hidden=true;errorBox.hidden=true;retry.hidden=true;});
video.addEventListener('error',()=>{start.hidden=true;showError('视频暂时无法播放，请重试或返回查看剧集状态。');});
fullscreen.addEventListener('click',async()=>{try{if(video.requestFullscreen)await video.requestFullscreen();else if(video.webkitEnterFullscreen)video.webkitEnterFullscreen();else showError('此浏览器暂不支持全屏，可在本页继续观看。');}catch{showError('暂时无法进入全屏，可使用播放器自带的全屏按钮。');}});
window.addEventListener('pagehide',()=>{++playRequest;loadController?.abort();stopBatchDownload();video.pause();});
window.addEventListener('pageshow',event=>{if(event.persisted&&video.hasAttribute('src'))startPlayback();});
function updateFileSelection(){
  const busy=downloadTotal>0;
  const available=new Set(files.filter(row=>row.file_url).map(row=>String(row.id)));
  byId('watch-files-download').disabled=busy||!selectedFiles.size;
  byId('watch-files-download').textContent=busy?`正在发起（${downloadStarted}/${downloadTotal}）`:`下载所选（${selectedFiles.size}）`;
  byId('watch-files-all').disabled=busy||!files.some(row=>groupFor(row)===fileGroup&&row.file_url&&!selectedFiles.has(String(row.id)));
  byId('watch-files-clear').disabled=busy||!selectedFiles.size;
  byId('watch-files-stop').hidden=!busy;
  byId('watch-file-rows').querySelectorAll('[data-download-select]').forEach(input=>{
    input.checked=selectedFiles.has(input.dataset.downloadSelect);
    input.disabled=busy||!available.has(input.dataset.downloadSelect);
    input.closest('.watch-file-tile').classList.toggle('is-selected',input.checked);
  });
}
function renderFiles(){
  byId('watch-files').hidden=false;
  const groups=[...new Set(files.map(groupFor))];
  if(!groups.includes(fileGroup))fileGroup=groups.includes(episodeGroup)?episodeGroup:groups[0]||'';
  for(const id of selectedFiles)if(!files.some(row=>String(row.id)===id&&row.file_url))selectedFiles.delete(id);
  byId('watch-file-ranges').innerHTML=groups.map(group=>`<button type="button" class="watch-file-range" data-file-group="${group}" aria-pressed="${group===fileGroup}">${group==='unknown'?'待定集号':`${group}–${Number(group)+29}`}</button>`).join('');
  byId('watch-file-rows').innerHTML=files.map(row=>{
    const label=episodeLabel(row)+(row.representative?' · 代表集':'');
    return `<div class="watch-file-tile ${row.file_url?'':'is-unavailable'}" ${groupFor(row)!==fileGroup?'hidden':''} title="${esc(label)}"><label class="watch-file-choice"><input type="checkbox" data-download-select="${row.id}" aria-label="${esc('选择下载 '+label)}" ${row.file_url?'':'disabled'}>${row.selection.kind==='collection'?'<small>合集</small>':''}<span class="watch-file-number ${row.selection.kind==='collection'?'is-collection':''}">${esc(row.selection.label)}</span></label>${row.file_url?`<a data-save href="${esc(row.file_url)}" download aria-label="${esc('下载 '+label)}">下载</a>`:'<span class="watch-file-unavailable">未就绪</span>'}</div>`;
  }).join('')||'<p class="watch-files-empty">暂无可下载的剧集。</p>';
  byId('watch-download-message').textContent=files.some(row=>row.file_url)?'选择剧集后保存到电脑，请在浏览器下载列表确认结果。':'本站暂未收录可下载剧集。';
  updateFileSelection();updateDownload();
}
function stopBatchDownload(){
  if(manage||!downloadTotal)return;
  clearTimeout(downloadTimer);downloadTimer=null;downloadQueue=[];
  byId('watch-batch-message').textContent=`已停止继续发起，已发起 ${downloadStarted} 个文件的保存；已发起的文件请在浏览器下载列表管理。`;
  downloadTotal=0;updateFileSelection();
}
function downloadNextFile(){
  const row=downloadQueue.shift();if(!row)return;
  // Use attachment links directly: do not buffer entire videos in browser memory.
  const link=document.createElement('a');link.href=row.file_url;link.download='';link.hidden=true;
  document.body.append(link);link.click();link.remove();downloadStarted++;
  byId('watch-batch-message').textContent=`已发起 ${downloadStarted}/${downloadTotal} 个文件的保存。如浏览器提示，请允许下载多个文件；请在下载列表确认结果。`;
  if(downloadQueue.length)downloadTimer=setTimeout(downloadNextFile,1000);
  else{downloadTimer=null;downloadTotal=0;}
  updateFileSelection();
}
if(!manage){
  byId('watch-file-ranges').addEventListener('click',event=>{
    const button=event.target.closest('[data-file-group]');if(!button)return;
    fileGroup=button.dataset.fileGroup;renderFiles();byId('watch-file-ranges').querySelector(`[data-file-group="${fileGroup}"]`).focus();
  });
  byId('watch-file-rows').addEventListener('change',event=>{
    const input=event.target.closest('[data-download-select]');if(!input)return;
    if(input.checked&&!input.disabled)selectedFiles.add(input.dataset.downloadSelect);else selectedFiles.delete(input.dataset.downloadSelect);
    updateFileSelection();
  });
  byId('watch-files-all').addEventListener('click',()=>{files.filter(row=>groupFor(row)===fileGroup&&row.file_url).forEach(row=>selectedFiles.add(String(row.id)));updateFileSelection();});
  byId('watch-files-clear').addEventListener('click',()=>{selectedFiles.clear();updateFileSelection();});
  byId('watch-files-download').addEventListener('click',()=>{
    if(downloadTotal)return;
    downloadQueue=files.filter(row=>row.file_url&&selectedFiles.has(String(row.id)));
    downloadTotal=downloadQueue.length;downloadStarted=0;if(downloadTotal)downloadNextFile();
  });
  byId('watch-files-stop').addEventListener('click',stopBatchDownload);
}
async function loadFiles(signal){
  if(manage)return;
  const login=byId('watch-login'),prepare=byId('watch-prepare');
  files=episodes.map(row=>({...row,file_url:row.download_url}));
  renderFiles();
  if(publicSite)return;
  login.href='/login?next='+encodeURIComponent(location.pathname+location.search+location.hash);
  try{const session=await read('/api/auth/session',signal);if(signal.aborted)return;if(!session.authenticated)return;
    const result=await read(manual?`/api/playback/collections/${catalogId}`:`/api/playback/rankings/${rankingId}/series/${seriesId}`,signal);if(signal.aborted)return;files=orderedEpisodes(result.episodes);byId('watch-files').hidden=false;prepare.hidden=files.length>0&&files.every(row=>row.file_url);prepare.href=`/control/videos?${context}`;
    renderFiles();
  }catch(error){if(signal.aborted)return;/* Public downloads remain available if administrator details cannot load. */}
}
document.addEventListener('click',event=>{const link=event.target.closest('[data-save],#watch-download');if(link?.hasAttribute('href'))byId('watch-download-message').textContent='已发起保存，请在浏览器下载列表中确认。';});
async function loadEpisodes(){
  const request=++loadRequest;loadController?.abort();loadController=new AbortController();const signal=loadController.signal;
  retry.hidden=true;errorBox.hidden=true;
  if(!(manual?/^\d{1,18}$/.test(catalogId):/^\d{1,18}$/.test(rankingId||'')&&/^\d{1,18}$/.test(seriesId||''))){showError('播放地址不完整，请返回重新选择作品。');return;}
  byId('watch-back').href=manage?`/control/videos?${context}&view=inspection`:manual?`/?area=manual#collection-${catalogId}`:`/?ranking_id=${rankingId}#series-${seriesId}`;
  try{const bootstrap=byId('watch-bootstrap');const detail=!manage&&bootstrap?JSON.parse(bootstrap.textContent):await read(manual?`/api/${manage?'downloads':'library'}/collections/${catalogId}`:`/api/${manage?'downloads':'library'}/rankings/${rankingId}`,signal);if(request!==loadRequest||signal.aborted)return;
    const {ranking,catalogs}=detail,item=ranking.items.find(row=>row.selected&&(manual||String(row.series_id)===seriesId)),catalog=catalogs.find(row=>manual||String(row.series_id)===seriesId);
    if(!item||!catalog)throw new Error('本榜单未收录此作品，请返回重新选择。');
    byId('watch-title').textContent=item.series_title;document.title=`${item.series_title} · ${manage?'审核预览':'站内播放'}`;byId('watch-author').textContent=`原作者 · ${item.author_name}`;byId('watch-basis').textContent=item.title_note||item.ai_basis||item.title;byId('watch-original').href=item.original_url;byId('watch-original').hidden=false;byId('watch-report').href=`/rankings/${rankingId}/report`;byId('watch-report').hidden=manual;
    episodes=orderedEpisodes(manage?catalog.episodes.filter(row=>row.file_url&&row.media).map(row=>({...row,url:`/api/downloads/media/${row.media.id}/file`,representative:!manual&&row.video_id===item.video_id})):catalog.episodes);
    loadFiles(signal);
    byId('episode-empty').hidden=!!episodes.length;byId('watch-summary').textContent=`本站${manage?'可预览':'可播放'} ${episodes.length} 集 · ${catalog.summary.full_series}`;
    renderEpisodeGrid();
    select.replaceChildren(...episodes.map(row=>new Option(episodeLabel(row),row.id)));
    if(!episodes.length){select.disabled=true;fullscreen.disabled=true;throw new Error('本站暂未收录可播放剧集，可查看原平台或剧集下载状态。');}
    const requested=new URL(location).searchParams.get('episode_id'),selected=episodes.find(row=>String(row.id)===requested)||episodes.find(row=>row.representative)||episodes[0];
    select.value=String(selected.id);select.disabled=false;fullscreen.disabled=false;playEpisode();
    if(params.get('download')==='1')byId('download-actions')?.scrollIntoView({block:'nearest'});
  }catch(error){if(request!==loadRequest||signal.aborted)return;select.disabled=true;fullscreen.disabled=true;showError(error.name==='AbortError'?'网络响应较慢，请重新加载。':error instanceof TypeError?'暂时无法读取剧集，请检查网络后重试。':error.message);}
}
retry.addEventListener('click',()=>episodes.length?playEpisode():loadEpisodes());
loadEpisodes();
