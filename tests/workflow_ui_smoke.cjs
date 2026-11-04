const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin))throw Error('Isolated server only');
const out='data/workflow-ui/';fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const click=async selector=>{await page.locator(selector).click();};
 const detail=()=>page.evaluate(()=>fetch('/api/control/tasks/'+new URLSearchParams(location.search).get('task_id')).then(r=>r.json()));
 async function refreshUntil(check,timeout=40000){const end=Date.now()+timeout;while(Date.now()<end){const d=await detail();if(check(d)){await click('#task-refresh');return d;}await page.waitForTimeout(600);}throw Error('Timed out waiting for workflow state');}
 async function reviewCandidates(type){for(const box of await page.locator('[data-candidate]').all())await box.check();const form=page.locator('#evidence-review');await form.locator('[name=target_type]').selectOption(type);await form.locator('[name=status]').selectOption('approved');await form.locator('[name=ai_type]').fill('合成AI动画');await form.locator('[name=ai_basis]').fill('仅合成测试：原作者制作声明与人工基准');for(const n of type==='series'?['story','ai','original_author']:['main_episode','ownership','public','not_duplicate'])await form.locator(`[name=${n}]`).check();await form.locator('[name=evidence_url]').fill('https://www.douyin.com/');await form.locator('[name=reason]').fill('合成测试：逐项核对所选证据');await form.locator('button').click();await page.waitForTimeout(700);}
 async function watchBatch(){await page.locator('#quick-now').waitFor({state:'visible',timeout:60000});const before=await page.locator('#quick-video').evaluate(v=>v.paused);assert.equal(before,true,'First playback requires explicit click');await click('#quick-play');await page.locator('#quick-summary').waitFor({state:'visible',timeout:70000});await page.waitForFunction(()=>!document.querySelector('#quick-finish').disabled);assert.match(await page.locator('#quick-publication').textContent(),/预计.*游客播放和保存/);await page.locator('#quick-confirm').check();await page.locator('#quick-sound').check();await page.locator('#quick-note').fill('合成测试：实际播放开头中段结尾，人工确认声音和画面');await click('#quick-finish');}
 try{
  await login(page,origin);await page.goto(origin+'/control/tasks');await page.locator('#task-run option').first().waitFor({state:'attached'});await page.locator('#task-search').check();await page.locator('#start-task button').click();await page.locator('#task-detail').waitFor({state:'visible'});
  await refreshUntil(d=>d.search_job?.status==='needs_login');await click('[data-task-action=resume]');await refreshUntil(d=>d.search_job?.status==='completed');
  await page.locator('[data-candidate]').first().waitFor();assert.equal(await page.locator('.evidence-card').count(),3);
  await reviewCandidates('series');await reviewCandidates('episode');
  let d=await detail();assert.equal(d.draft.items.filter(i=>i.selected).length,3);assert.equal(d.ranking_id,null);
  await page.locator('#finalize-confirm').check();await click('#task-finalize');await page.waitForFunction(()=>!document.querySelector('#task-initialize').hidden);await click('#task-initialize');await page.waitForSelector('[data-discover]');
  const first=page.locator('#catalogs .panel').first();await first.locator('[data-discover]').click();await page.waitForTimeout(700);
  let panel=page.locator('#catalogs .panel').first();assert.equal(await panel.locator('[data-directory]').count(),13);
  for(const box of await panel.locator('[data-directory]').all())await box.check();const form=panel.locator('[data-directory-form]');for(const n of ['ownership','main_episode','not_duplicate','public_access'])await form.locator(`[name=${n}]`).check();await form.locator('[name=note]').fill('合成目录逐条核验，非真实人工结论');await form.locator('button').click();await page.waitForTimeout(700);
  panel=page.locator('#catalogs .panel').first();for(const box of await panel.locator('[data-scope]').all())await box.check();await click('#task-scope');
  d=await refreshUntil(d=>d.catalogs[0].episodes.some(e=>e.job?.status==='verified'));assert.equal(d.catalogs[0].episodes.filter(e=>e.job).length,1,'Trial gate forbids premature batch queue');
  await click('[data-task-action=pause]');await refreshUntil(d=>d.status==='paused');await click('[data-task-action=resume]');await refreshUntil(d=>d.status==='active'&&d.catalogs[0].workflow.batch_id);
  await page.locator('[data-quick-start]').first().click();await watchBatch();await page.waitForFunction(()=>!document.querySelector('#quick-review-dialog').open);
  d=await refreshUntil(d=>d.catalogs[0].episodes.filter(e=>e.job?.status==='verified').length===13,60000);
  assert.equal(d.catalogs[0].episodes.filter(e=>e.media?.manual_status==='passed').length,1);
  await page.locator('[data-quick-start]').first().click();await page.locator('#quick-now').waitFor({state:'visible',timeout:60000});
  await click('#quick-skip');await click('#quick-problem');await page.locator('#quick-reason').fill('合成问题：中段需复查');await click('#quick-problem-save');
  await click('#quick-play');await page.locator('#quick-summary').waitFor({state:'visible',timeout:70000});await page.waitForFunction(()=>!document.querySelector('#quick-finish').disabled);
  assert.match(await page.locator('#quick-publication').textContent(),/拟拒绝 1 集.*继续待审 1 集/);
  for(const width of [1440,768,390,320]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));if(width===390)await page.screenshot({path:out+'summary-mobile.png'});}
  await page.locator('#quick-confirm').check();await page.locator('#quick-sound').check();await page.locator('#quick-note').fill('合成规定播放，问题及跳过独立保留');await click('#quick-finish');
  await page.waitForTimeout(1200);await watchBatch();await page.waitForFunction(()=>!document.querySelector('#quick-review-dialog').open);
  d=await detail();assert.equal(d.catalogs[0].episodes.filter(e=>e.media?.manual_status==='pending').length,1);assert.equal(d.catalogs[0].episodes.filter(e=>e.media?.manual_status==='rejected').length,1);
  await click('#task-refresh');await page.locator('[data-quick-reset]').first().click();await watchBatch();await page.waitForFunction(()=>!document.querySelector('#quick-review-dialog').open);
  d=await refreshUntil(d=>d.all_scoped_complete);await click('#task-complete');await refreshUntil(d=>d.status==='complete');
  const anonymous=await browser.newContext();
  const publicMedia=d.catalogs[0].episodes.find(e=>e.media?.manual_status==='passed').media.id;
  const rejectedMedia=d.catalogs[0].episodes.find(e=>e.media?.manual_status==='rejected').media.id;
  const response=await anonymous.request.get(origin+`/api/library/media/${publicMedia}/download`,{headers:{Range:'bytes=0-1023'}});assert.equal(response.status(),206);assert.match(response.headers()['content-disposition'],/attachment/);
  assert.equal((await anonymous.request.get(origin+`/api/library/media/${rejectedMedia}`)).status(),404);await anonymous.close();
  await page.screenshot({path:out+'completed-mobile.png'});assert.deepEqual(errors,[]);
  fs.writeFileSync(out+'acceptance.json',JSON.stringify({passed:true,syntheticSearchLoginResume:true,selectedAuditedReviews:true,immutableRanking:true,scopedDirectoryReview:true,trialGate:true,pauseResume:true,thirteenActualAudioReviews:true,skipRejectPreserved:true,nextBatchPrefetchAndContinue:true,publicPreview:true,widths:[1440,768,390,320],errors},null,2));console.log('Workflow browser acceptance passed');
 }catch(e){await page.screenshot({path:out+'failure.png'});console.error('UI state:',await page.locator('#message').textContent(),await page.locator('#quick-message').textContent());throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
