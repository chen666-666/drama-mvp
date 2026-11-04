"use strict";
(()=>{
 const dialog=document.querySelector('#quick-review-dialog'),video=document.querySelector('#quick-video');
 if(!dialog||!video)return;
 const find=selector=>dialog.querySelector(selector);
 let batch=null,rows=[],position=0,segment=0,epoch=0,timer=null,busy=false,covered=0,lastTime=0;
 const problems=new Map(),skipped=new Set(),deferred=new Map();
 let context=null;
 let started=false,prefetched=null,prefetching=false,previewReady=false;
 const publication=document.createElement('p');publication.id='quick-publication';publication.className='review-warning';find('#quick-form').prepend(publication);
 const scanButton=document.createElement('button');scanButton.type='button';scanButton.className='subtle';scanButton.textContent='手动检测异常（试点）';find('#quick-player .actions').append(scanButton);
 const analysis=document.createElement('section');analysis.hidden=true;analysis.innerHTML='<p role="status"></p><p class="muted">初始参数：黑屏2秒、静音3秒、冻结3秒。夜景、停顿和静态动画可能触发；提示不能替代人工结论。</p><div class="actions"></div><video controls playsinline preload="metadata" style="width:100%;max-height:35dvh" hidden></video>';find('#quick-player').append(analysis);
 let analysisTimer=null;
 scanButton.onclick=async()=>{video.pause();const id=current()?.media_id,ticket=epoch;if(!id)return;analysis.hidden=false;analysis.querySelector('p').textContent='正在启动手动异常检测…';try{await api(`/api/downloads/media/${id}/analysis`,{});await showAnalysis(id,ticket);}catch(e){analysis.querySelector('p').textContent=e.message;}};
 async function showAnalysis(id,ticket){const result=await api(`/api/downloads/media/${id}/analysis`);if(ticket!==epoch||current()?.media_id!==id||!active())return;analysis.querySelector('p').textContent=result.message+(result.tool?' · '+result.tool:'');const buttons=analysis.querySelector('.actions');buttons.replaceChildren();for(const finding of result.findings||[]){const button=document.createElement('button');button.type='button';button.className='subtle';button.textContent=`检测到${finding.kind}区间 ${finding.start.toFixed(1)}–${finding.end.toFixed(1)} 秒，定位复看`;button.onclick=()=>{video.pause();const player=analysis.querySelector('video');player.hidden=false;player.src=`/api/downloads/media/${id}/file`;player.addEventListener('loadedmetadata',()=>{player.currentTime=finding.start;player.play().catch(()=>{});},{once:true});};buttons.append(button);}if(result.status==='running')analysisTimer=setTimeout(()=>showAnalysis(id,ticket).catch(e=>{analysis.querySelector('p').textContent=e.message;}),2000);}
 const decisions=()=>rows.filter(row=>problems.has(row.media_id)||completed(row)&&!skipped.has(row.media_id)).map(row=>({media_id:row.media_id,status:problems.has(row.media_id)?'rejected':'passed',reason:problems.get(row.media_id)||''}));
 async function prepareNext(){
  if(!batch||prefetching||prefetched)return;prefetching=true;const id=batch.id;
  try{const result=await api(`/api/downloads/quick-reviews/${id}/prefetch`,{skip_media_ids:[...(deferred.get(context.catalogId)||[])],episode_ids:window.quickReviewScope?.(context.catalogId)||null});if(batch?.id===id)prefetched=result;}
  catch{/* No remaining eligible media or paused scope: current review is still usable. */}finally{prefetching=false;}
 }
 const nextBatch=document.createElement('button');nextBatch.type='button';nextBatch.className='subtle';nextBatch.textContent='暂缓这些剧集，准备下一批';nextBatch.hidden=true;dialog.append(nextBatch);
 nextBatch.addEventListener('click',()=>{const later=deferred.get(context.catalogId)||new Set();for(const row of batch.rows)later.add(row.media_id);deferred.set(context.catalogId,later);safeStorage(`quick-review-${context.catalogId}`,null);const id=context.catalogId;dialog.close();document.querySelector(`[data-quick-start="${id}"]`)?.click();});
 const say=text=>{find('#quick-message').textContent=text;};
 const active=()=>dialog.open&&context&&String(state.seriesId)===String(context.seriesId)&&document.querySelector('#download-ranking').value===context.choice;
 const current=()=>rows[position];
 const completed=row=>row.watched?.length===3;
 function safeStorage(key,value){try{if(value===undefined)return sessionStorage.getItem(key);value===null?sessionStorage.removeItem(key):sessionStorage.setItem(key,value);}catch{}return null;}
 window.refreshQuickReviews=()=>{
  for(const catalog of state.catalogs){
   const panel=document.getElementById(`series-${catalog.series_id}`);if(!panel||panel.querySelector('.quick-review-tools'))continue;
   const pending=catalog.episodes.filter(row=>row.media?.manual_status==='pending'&&row.job?.status==='verified'&&row.status==='approved'&&row.access==='public');
   const tools=document.createElement('div');tools.className='quick-review-tools inspection-tools';
   tools.innerHTML=`<h3>连续快审</h3><p>待人工抽检 ${pending.length} 集。每批最多 10 集，自动检查后依次播放开头、中段、结尾各约 5 秒，保留声音。实际看完后填写一次说明并统一提交。</p><div class="actions"><button type="button" data-quick-start="${catalog.id}" ${pending.length?'':'disabled'}>开始 / 继续本批快审</button><button type="button" class="subtle" data-quick-reset="${catalog.id}">重新准备待审批次</button></div><p class="muted">准备片段不修改人工结论；异常、跳过和未看完的剧集继续待审。批次保留 24 小时。短片段抽看不能保证整集所有位置都没有问题。</p>`;
   panel.querySelector('.table-wrap').before(tools);
  }
 };
 document.querySelector('#catalogs').addEventListener('click',async event=>{
  const button=event.target.closest('[data-quick-start],[data-quick-reset]');if(!button)return;
  const id=Number(button.dataset.quickStart||button.dataset.quickReset),catalog=state.catalogs.find(row=>row.id===id);if(!catalog)return;
  if(button.dataset.quickReset){safeStorage(`quick-review-${id}`,null);deferred.delete(id);await window.quickReviewDefer?.(id,[],true);}
  const savedDeferred=window.quickReviewDeferred?.(id);if(savedDeferred)deferred.set(id,new Set(savedDeferred));
  context={catalogId:id,seriesId:catalog.series_id,choice:document.querySelector('#download-ranking').value};
  const scope=window.quickReviewScope?.(id);if(scope&&!scope.length){notify('请先确认本作品允许准备和快审的范围');return;}
  started=false;prefetched=null;previewReady=false;
  dialog.showModal();nextBatch.hidden=true;find('#quick-summary').hidden=true;find('#quick-player').hidden=true;find('#quick-issue').hidden=true;find('#quick-finish').disabled=true;
  find('#quick-form').reset();say('正在读取本批状态或准备自动检查…');
  const ticket=++epoch;
  try{
   let saved=safeStorage(`quick-review-${id}`);
   if(saved){try{batch=await api(`/api/downloads/quick-reviews/${saved}`);if(batch.status==='submitted'||scope&&batch.rows.some(r=>!scope.includes(r.episode_id)))saved=null;}catch{saved=null;}}
   if(!saved)batch=await api(`/api/downloads/catalogs/${id}/quick-reviews`,{limit:10,skip_media_ids:[...(deferred.get(id)||[])],restart:!!button.dataset.quickReset,episode_ids:scope||null});
   if(ticket!==epoch||!active())return;
   safeStorage(`quick-review-${id}`,batch.id);problems.clear();skipped.clear();
   try{const marks=JSON.parse(safeStorage(`quick-review-marks-${batch.id}`)||'{}');for(const [key,value] of marks.problems||[])problems.set(key,value);for(const key of marks.skipped||[])skipped.add(key);}catch{}
   try{const draft=JSON.parse(safeStorage(`quick-review-draft-${batch.id}`)||'{}');find('#quick-note').value=draft.note||'';find('#quick-confirm').checked=!!draft.confirmed;find('#quick-sound').checked=!!draft.sound;}catch{}
   await poll(ticket);
  }catch(error){if(active())say(error.message);}
 });
 async function poll(ticket){
  if(ticket!==epoch||!active())return;
  batch=await api(`/api/downloads/quick-reviews/${batch.id}`);
  if(ticket!==epoch||!active())return;
  say(batch.message);find('#quick-batch-status').textContent=batch.rows.map(row=>`${row.episode_range||row.episode_number?'第'+(row.episode_range||row.episode_number)+'集':row.title}：${({waiting:'等待检查',checking:'正在检查',ready:'片段就绪',error:'需单独处理'})[row.state]}`).join('；');
  if(batch.status==='preparing'){timer=setTimeout(()=>poll(ticket).catch(error=>say(error.message)),2500);return;}
  if(batch.status!=='ready')return;
  rows=batch.rows.filter(row=>row.state==='ready');position=0;segment=0;
  find('#quick-errors').textContent=batch.rows.filter(row=>row.state==='error').map(row=>`${row.title}：${row.message}`).join('；');
  if(!rows.length){say('本批没有可播放的片段。可暂缓这些剧集，继续准备其他剧集；异常项仍然待审。');nextBatch.hidden=false;return;}
  await next();
 }
 async function next(){
  video.pause();find('#quick-issue').hidden=true;find('#quick-player').hidden=true;
  if(!active())return;
  while(position<rows.length&&(completed(rows[position])||problems.has(rows[position].media_id)||skipped.has(rows[position].media_id)))position++;
  if(position>=rows.length){await summary();return;}
  const row=current();segment=[0,1,2].find(index=>!row.watched.includes(index));
  clearTimeout(analysisTimer);analysis.hidden=true;analysis.querySelector('video').pause();analysis.querySelector('video').removeAttribute('src');
  const part=row.segments[segment];covered=0;lastTime=0;
  find('#quick-player').hidden=false;find('#quick-summary').hidden=true;
  find('#quick-now').textContent=`${position+1}/${rows.length} · ${row.episode_range||row.episode_number?'第 '+(row.episode_range||row.episode_number)+' 集 · ':''}${row.title} · ${part.label}`;
  find('#quick-overlay').textContent=`${row.episode_range||row.episode_number?'第'+(row.episode_range||row.episode_number)+'集 · ':''}${part.label} · 原片 ${Math.floor(part.start)} 秒处`;
  find('#quick-warning').textContent=(row.warnings||[]).join('；');say('请开启设备声音，点击“播放 / 继续”。三个片段会自动切换。');
  video.src=part.url;video.muted=false;video.volume=1;video.playbackRate=1;video.load();
  if(started){try{await video.play();}catch{say('片段已准备好，请点击“播放 / 继续”开始有声播放。');}}
 }
 video.addEventListener('timeupdate',()=>{
  const delta=video.currentTime-lastTime;
  if(!document.hidden&&!video.muted&&video.volume>0&&video.playbackRate===1&&delta>0&&delta<1.5)covered+=delta;
  lastTime=video.currentTime;
  find('#quick-progress').textContent=`本段 ${Math.min(video.currentTime,video.duration||0).toFixed(1)} / ${(video.duration||0).toFixed(1)} 秒`;
 });
 video.addEventListener('ended',async()=>{
  if(!active()||busy)return;
  if(video.muted||video.volume===0||covered<video.duration*.8||!video.played.length||video.played.start(0)>.2||video.played.end(video.played.length-1)<video.duration-.25){say('本段未正常播放完整，请点击“重播本段”再看一次。');return;}
  busy=true;const ticket=epoch,row=current();
  try{
   const result=await api(`/api/downloads/quick-reviews/${batch.id}/watched`,{media_id:row.media_id,index:segment});
   if(ticket!==epoch||!active())return;
   row.watched=result.rows.find(item=>item.media_id===row.media_id).watched;await next();
  }catch(error){say(error.message);}finally{busy=false;}
 });
 video.addEventListener('error',()=>{if(active()&&video.getAttribute('src'))say('本段播放失败，不能记为已审。可重播、打开完整视频或跳过本集。');});
 video.addEventListener('volumechange',()=>{if(active()&&(video.muted||video.volume===0)){video.pause();say('请开启声音后继续播放；提交前还需确认声音正常。');}});
 video.addEventListener('ratechange',()=>{if(video.playbackRate!==1){video.pause();video.playbackRate=1;say('快审使用正常速度，请继续播放。');}});
 document.addEventListener('visibilitychange',()=>{if(document.hidden&&dialog.open)video.pause();});
 find('#quick-play').addEventListener('click',async()=>{if(video.ended)return;started=true;prepareNext();say('正在播放，请实际观察画面并听声音。');video.muted=false;video.volume=1;try{if(video.paused)await video.play();else video.pause();}catch{say('浏览器暂未允许播放，请再次点击播放。');}});
 find('#quick-replay').addEventListener('click',()=>{covered=0;lastTime=0;video.currentTime=0;video.play().catch(()=>say('请点击播放继续。'));});
 const saveMarks=()=>safeStorage(`quick-review-marks-${batch.id}`,JSON.stringify({problems:[...problems],skipped:[...skipped]}));
 find('#quick-skip').addEventListener('click',()=>{if(busy)return;skipped.add(current().media_id);saveMarks();position++;next().catch(error=>say(error.message));});
 find('#quick-problem').addEventListener('click',()=>{if(busy)return;video.pause();find('#quick-issue').hidden=false;find('#quick-reason').value='';find('#quick-reason').focus();});
 find('#quick-problem-save').addEventListener('click',()=>{const reason=find('#quick-reason').value.trim();if(!reason){say('请先填写本集的问题位置与原因。');return;}problems.set(current().media_id,reason);saveMarks();position++;next().catch(error=>say(error.message));});
 find('#quick-full').addEventListener('click',()=>{video.pause();const url=new URL('/downloads/watch',location.origin);url.searchParams.set('episode_id',current().episode_id);url.searchParams.set(state.ranking.kind==='manual'?'catalog_id':'ranking_id',state.ranking.kind==='manual'?context.catalogId:state.ranking.id);url.searchParams.set('series_id',context.seriesId);window.open(url,'_blank','noopener');});
 async function summary(){
  video.pause();find('#quick-player').hidden=true;find('#quick-summary').hidden=false;
  const passed=rows.filter(row=>completed(row)&&!skipped.has(row.media_id)&&!problems.has(row.media_id));
  find('#quick-results').innerHTML=batch.rows.map(row=>`<li>${esc(row.title)}：${problems.has(row.media_id)?'发现问题 · '+esc(problems.get(row.media_id)):passed.includes(row)?'三个片段已看完，等待你确认通过':'继续待审'}</li>`).join('');
  previewReady=false;find('#quick-finish').disabled=true;publication.textContent='正在核对本批文件、当前目录资格及公开后果…';say(`本批待提交：${passed.length} 集通过候选，${problems.size} 集发现问题。请填写说明并确认后再保存。`);
  nextBatch.hidden=passed.length+problems.size>0;
  find('#quick-confirm').required=passed.length>0;find('#quick-sound').required=passed.length>0;
  find('#quick-confirm').parentElement.hidden=passed.length===0;find('#quick-sound').parentElement.hidden=passed.length===0;
  if(!find('#quick-note').value.trim())find('#quick-note').value=`本批 ${passed.length} 集的开头、中段、结尾规定片段已有实际播放记录；${problems.size} 集标记问题，${batch.rows.length-passed.length-problems.size} 集继续待审。请补充实际观察与声音结论。`;
  try{const result=await api(`/api/downloads/quick-reviews/${batch.id}/preview`,{decisions:decisions()});if(!active())return;publication.textContent=result.message;previewReady=true;find('#quick-finish').disabled=passed.length+problems.size===0;}catch(error){publication.textContent=error.message;}
 }
 find('#quick-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!active()||!previewReady)return;busy=true;find('#quick-finish').disabled=true;
  const ticket=epoch;
  try{
   const result=await api(`/api/downloads/quick-reviews/${batch.id}/submit`,{decisions:decisions(),note:find('#quick-note').value,confirmed:find('#quick-confirm').checked,sound:find('#quick-sound').checked});
   const later=deferred.get(context.catalogId)||new Set();for(const row of batch.rows)if(skipped.has(row.media_id)||row.state==='error')later.add(row.media_id);deferred.set(context.catalogId,later);
   await window.quickReviewDefer?.(context.catalogId,[...later],false);
   safeStorage(`quick-review-${context.catalogId}`,null);
   safeStorage(`quick-review-marks-${batch.id}`,null);
   safeStorage(`quick-review-draft-${batch.id}`,null);
   if(ticket!==epoch||!active())return;const catalogId=context.catalogId,nextId=prefetched?.id;await load();dialog.close();notify(result.message);
   if(nextId){safeStorage(`quick-review-${catalogId}`,nextId);setTimeout(()=>document.querySelector(`[data-quick-start="${catalogId}"]`)?.click(),0);}
  }catch(error){say(error.message);find('#quick-finish').disabled=false;}finally{busy=false;}
 });
 find('#quick-close').addEventListener('click',()=>dialog.close());
 find('#quick-form').addEventListener('input',()=>{if(batch)safeStorage(`quick-review-draft-${batch.id}`,JSON.stringify({note:find('#quick-note').value,confirmed:find('#quick-confirm').checked,sound:find('#quick-sound').checked}));});
 dialog.addEventListener('keydown',event=>{if(event.target.matches('input,textarea,select')||event.ctrlKey||event.metaKey||event.altKey)return;const keys={' ':'#quick-play',r:'#quick-replay',s:'#quick-skip',p:'#quick-problem'};const target=keys[event.key.toLowerCase()];if(target&&!find('#quick-player').hidden){event.preventDefault();find(target).click();}});
 dialog.addEventListener('close',()=>{epoch++;clearTimeout(timer);clearTimeout(analysisTimer);analysis.querySelector('video').pause();video.pause();video.removeAttribute('src');video.load();});
 window.refreshQuickReviews();
})();
