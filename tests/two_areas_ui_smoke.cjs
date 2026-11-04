/* Two-area acceptance on synthetic data only; no real platform or production writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const crypto=require('node:crypto');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.TOP3_SMOKE_URL;
if(!base||!['8001','8002'].includes(new URL(base).port))throw new Error('Use an isolated local UI server');
const origin=new URL(base).origin;
const manifest=JSON.parse(fs.readFileSync('data/ui-refactor/fixture.json','utf8'));
const {ranking_id:ranking,previous_ranking_id:previous,series_id:series,run_id:run}=manifest;
const watch=`/watch?ranking_id=${ranking}&series_id=${series}`;
const shot='data/ui-refactor/';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
 const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));let downloads=0;page.on('download',()=>downloads++);
 const overflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`overflow on ${page.url()}`);
 try{
  await page.goto(origin);await page.waitForSelector('[data-watch]');assert.equal(await page.locator('.topbar a').allTextContents().then(rows=>rows.some(row=>row.includes('下载管理'))),false);
  assert.equal(await page.locator('#hongguo-area').isVisible(),false);assert.equal(await page.locator('[data-area=douyin]').getAttribute('aria-pressed'),'true');
  await page.screenshot({path:shot+'playback-home-desktop.png',fullPage:true});
  await page.goto(origin+watch);await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);
  assert.equal(await page.locator('[data-episode]').count(),2);await page.waitForSelector('#watch-download:not([hidden])');
  const publicDownload=page.waitForEvent('download');await page.locator('#watch-download').click();assert.equal(await (await publicDownload).failure(),null);
  await page.locator('[data-episode]').last().click();const episode=new URL(page.url()).searchParams.get('episode_id');await page.reload();await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);assert.equal(await page.locator('#watch-episode').inputValue(),episode);
  await page.goto(origin+'/login?next='+encodeURIComponent(`/watch?ranking_id=${ranking}&series_id=${series}&episode_id=${episode}`));await page.locator('[name=username]').fill('synthetic-admin');await page.locator('[name=password]').fill('synthetic-password');await page.locator('#login-form button').click();await page.waitForURL('**/watch?**');
  assert.equal(new URL(page.url()).searchParams.get('episode_id'),episode);await page.waitForSelector('#watch-download:not([hidden])');assert.equal(await page.locator('[data-save]').count(),3);
  const download=page.waitForEvent('download');await page.locator('#watch-download').click();const saved=await download;assert.equal(await saved.failure(),null);const bytes=fs.readFileSync(await saved.path());assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),crypto.createHash('sha256').update(fs.readFileSync('data/ui-refactor/synthetic.mp4')).digest('hex'));
  await page.screenshot({path:shot+'player-desktop.png',fullPage:true});
  await page.goto(origin+'/control');await page.waitForSelector('.overview-stat');assert.equal(await page.locator('.control-links a').count(),4);assert.equal(await page.locator('.overview-stat').count(),5);await page.screenshot({path:shot+'control-overview-desktop.png',fullPage:true});
  await page.goto(`${origin}/control/videos?ranking_id=${ranking}&series_id=${series}`);await page.waitForSelector('[data-trial]');assert.equal(await page.locator('#catalogs>section').count(),1);assert.equal(await page.getByRole('link',{name:'保存到电脑',exact:true}).count(),0);
  const before=downloads;await page.locator('[data-trial]').click();await page.waitForFunction(()=>document.querySelector('[data-feedback]').textContent.includes('播放区'));assert.equal(downloads,before);
  await page.locator('[data-video-tab=inspection]').click();assert.equal(await page.locator('[data-trial]').isVisible(),false);await page.locator('[data-media]').last().click();await page.waitForFunction(()=>document.querySelector('#preview').readyState>=2);
  await page.locator('#media-form [name=note]').fill('合成视频抽检输入保护');await page.waitForTimeout(4300);assert.equal(await page.locator('#media-form [name=note]').inputValue(),'合成视频抽检输入保护');
  await page.screenshot({path:shot+'review-desktop.png',fullPage:true});
  await page.locator('#media-form > button').click();await page.waitForFunction(()=>document.querySelector('#media-dialog .modal-error').textContent.includes('抽看'));
  for(const key of ['beginning','middle','ending','sound'])await page.locator(`#media-form [name=${key}]`).check();await page.locator('#media-form > button').click();await page.waitForFunction(()=>!document.querySelector('#media-dialog').open);
  await page.locator('[data-video-tab=catalog]').click();await page.locator('[data-review]').first().click();await page.locator('[data-close=episode-dialog]').click();
  await page.locator('#source-review-tab').click();await page.waitForSelector('#candidate-rows tr');assert.equal(await page.locator('#review-series').inputValue(),String(series));assert.equal(await page.locator('#search-section').isVisible(),false);assert.equal(await page.locator('#candidate-rows tr').count(),3);
  await page.locator('[data-review=series]').first().click();await page.locator('#review-form [name=reason]').fill('尚未提交的来源核验说明');assert.match(await page.locator('#review-source-title').textContent(),/合成星际旅人/);await page.screenshot({path:shot+'source-review-desktop.png',fullPage:true});await page.locator('[data-close=review-dialog]').click();
  const candidates=await (await page.request.get(`${origin}/api/runs/${run}/candidates`)).json();
  const focused=candidates.find(row=>row.series_id===series);
  await page.goto(`${origin}/control/reviews?run_id=${run}&episode_id=${focused.episode_id}`);await page.waitForSelector('#candidate-rows tr');assert.equal(await page.locator('#review-series').inputValue(),String(series));assert.equal(await page.locator('#candidate-rows tr').count(),1);
  await page.goto(`${origin}/control/collection?run_id=${run}&tab=ranking`);await page.waitForSelector('#ranking-content .rank-card');assert.equal(await page.locator('#candidate-section').isVisible(),false);assert.equal(await page.locator('#search-section').isVisible(),false);
  await page.locator('[data-workspace-tab=import]').click();assert.equal(await page.locator('#csv-form').isVisible(),true);assert.equal(await page.locator('#ranking-section').isVisible(),false);await page.locator('[data-workspace-tab=search]').click();assert.equal(await page.locator('#search-form').isVisible(),true);assert.equal(await page.locator('#session-form').count(),0);
  await page.goto(`${origin}/control/system?ranking_id=${ranking}`);await page.waitForSelector('#report-link:not([hidden])');assert.equal(await page.locator('#session-form').isVisible(),true);assert.equal(await page.locator('#delivery-ranking').inputValue(),String(ranking));await page.locator('#export-package').click();await page.waitForSelector('#package-link:not([hidden])');
  await page.screenshot({path:shot+'system-desktop.png',fullPage:true});
  // Old entry points retain the context and use the same control shell.
  await page.goto(`${origin}/downloads?ranking_id=${ranking}&series_id=${series}#series-${series}`);await page.waitForSelector('[data-trial]');assert.equal(await page.locator('#download-series').inputValue(),String(series));
  let releaseOld,receivedOld;const received=new Promise(resolve=>receivedOld=resolve),release=new Promise(resolve=>releaseOld=resolve);
  await page.route(`**/api/downloads/rankings/${ranking}`,async route=>{receivedOld();await release;await route.continue();});await page.locator('#refresh').click();await received;await page.locator('#download-ranking').selectOption(String(previous));await page.waitForFunction(()=>document.querySelector('#ranking-info').textContent.includes('V1'));releaseOld();await page.waitForTimeout(350);assert.match(await page.locator('#ranking-info').textContent(),/V1/);await page.unroute(`**/api/downloads/rankings/${ranking}`);
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:900});
   for(const path of ['/',watch,`/control/videos?ranking_id=${ranking}&series_id=${series}`,'/control',`/control/reviews?run_id=${run}&series_id=${series}`,`/control/collection?run_id=${run}&tab=ranking`,'/control/system']){
    await page.goto(origin+path);if(path.includes('/control/videos'))await page.waitForSelector('[data-trial]');if(path===watch)await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);await overflow();
    if(width<=390&&path.includes('/control/reviews')){await page.locator('[data-review=series]').first().click();await overflow();await page.screenshot({path:shot+`source-review-mobile-${width}.png`,fullPage:true});await page.locator('[data-close=review-dialog]').click();}
    if(width<=390&&path.includes('/control/videos')){await page.locator('[data-media]').first().click();await overflow();await page.locator('[data-close=media-dialog]').click();}
   }
  }
  await page.setViewportSize({width:390,height:844});await page.goto(origin+watch);await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);await page.screenshot({path:shot+'player-mobile.png',fullPage:true});
  await page.goto(origin+'/control');await page.waitForSelector('.overview-stat');await page.locator('.control-menu summary').click();await page.screenshot({path:shot+'control-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);const result={result:'passed',anonymousPlayback:true,anonymousDownload:true,adminDownloadAndSha256:true,loginReturnsToEpisode:true,noControlAutoSave:true,sourceAndMediaReview:true,episodeTodoSelectsWork:true,inputProtectedDuringPolling:true,export:true,staleResponseIgnored:true,widths:[1440,768,390,320],browserErrors:errors};fs.writeFileSync(shot+'browser-acceptance.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){await page.screenshot({path:shot+'failure.png',fullPage:true});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
