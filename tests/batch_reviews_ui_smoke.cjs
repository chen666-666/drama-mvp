const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin))throw Error('Isolated server required');
const out='data/batch-reviews-ui/';fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await login(page,origin);await page.goto(origin+'/control/videos?catalog_id=1');await page.waitForSelector('[data-review-filter]');
  assert.equal(await page.locator('#catalogs tbody tr').count(),9);
  await page.locator('[data-review-filter]').selectOption('numbered');
  assert.equal(await page.locator('#catalogs tbody tr').count(),2);
  await page.locator('[data-review-select-all]').click();assert.equal(await page.locator('[data-review-select]:checked').count(),2);
  await page.waitForTimeout(4300);assert.equal(await page.locator('[data-review-select]:checked').count(),2);
  await page.locator('#refresh').click();await page.waitForTimeout(300);assert.equal(await page.locator('[data-review-select]:checked').count(),2);
  await page.locator('[data-review-batch][data-decision=approved]').click();
  assert.match(await page.locator('#batch-review-title').textContent(),/2 条/);
  assert.equal(await page.locator('#batch-review-list li').count(),2);
  await page.locator('#batch-review-submit').click();assert.equal(await page.locator('#batch-review-dialog').evaluate(el=>el.open),true);
  for(const name of ['ownership','main_episode','not_duplicate','public_access'])await page.locator(`#batch-review-form [name=${name}]`).check();
  await page.locator('#batch-review-form [name=note]').fill('合成验收：已逐条确认本批公开正片');
  await page.waitForTimeout(4300);assert.equal(await page.locator('#batch-review-form [name=note]').inputValue(),'合成验收：已逐条确认本批公开正片');
  await page.screenshot({path:out+'batch-dialog-desktop.png'});
  await page.locator('#batch-review-submit').click();await page.waitForFunction(()=>!document.querySelector('#batch-review-dialog').open&&document.querySelectorAll('#catalogs tbody tr').length===0);
  assert.equal(await page.locator('#catalogs tbody tr').count(),0);
  await page.locator('[data-review-filter]').selectOption('attention');assert.equal(await page.locator('#catalogs tbody tr').count(),4);
  await page.locator('[data-review-select-all]').click();await page.locator('[data-review-batch][data-decision=excluded]').click();
  assert.equal(await page.locator('#batch-review-checks').isVisible(),false);
  await page.locator('#batch-review-form [name=note]').fill('合成验收：宣传、重复及暂不纳入的作品');
  await page.locator('#batch-review-submit').click();await page.waitForFunction(()=>!document.querySelector('#batch-review-dialog').open);
  await page.locator('[data-review-filter]').selectOption('excluded');assert.equal(await page.locator('#catalogs tbody tr').count(),4);
  await page.locator('[data-review-select]').first().check();await page.locator('[data-review-filter]').selectOption('approved');
  assert.equal(await page.locator('[data-review-select]:checked').count(),0);assert.equal(await page.locator('#catalogs tbody tr').count(),5);
  const detail=await (await page.request.get(origin+'/api/downloads/collections/1')).json();
  for(const vid of ['810003','810004']){
   const row=detail.catalogs[0].episodes.find(row=>row.video_id===vid);assert.equal(row.status,'approved');assert.equal(row.access,'public');assert.equal(row.episode_number,Number(vid.slice(-1)));assert.equal(row.evidence_url,row.original_url);assert.equal(row.job,null);assert.equal(row.review_history.length,1);
  }
  assert.equal(detail.catalogs[0].published,false);
  await page.locator('[data-review]').first().click();assert.equal(await page.locator('#episode-dialog').evaluate(el=>el.open),true);await page.locator('[data-close=episode-dialog]').click();
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.locator('[data-review-select-all]').click();await page.locator('[data-review-batch][data-decision=approved]').click();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   if(width===390)await page.screenshot({path:out+'batch-dialog-mobile.png'});
   await page.locator('[data-close=batch-review-dialog]').click();
  }
  await page.locator('[data-video-tab=inspection]').click();assert.equal(await page.locator('.batch-review-tools').isVisible(),false);
  await page.locator('[data-video-tab=catalog]').click();assert.equal(await page.locator('[data-review-select]:checked').count(),0);
  assert.deepEqual(errors,[]);
  const result={passed:true,filteredSelection:true,bulkApproveAndExclude:true,checksAndNoteRequired:true,evidenceAndNumbersPreserved:true,auditPerEpisode:true,noAutomaticQueueOrPublication:true,selectionAndInputPreserved:true,singleReviewWorks:true,widths:[1440,768,390,320],errors};
  fs.writeFileSync(out+'acceptance.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){await page.screenshot({path:out+'failure.png'});throw error;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
