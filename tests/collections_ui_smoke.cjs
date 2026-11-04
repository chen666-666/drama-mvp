/* Isolated synthetic collection workflow. Never use against a public website. */
const assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin))throw new Error('Isolated loopback server required');
const fixture=JSON.parse(fs.readFileSync('data/collections-ui/fixture.json'));
const out='data/collections-ui/';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 async function overflow(){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page overflows');}
 try{
  await login(page,origin);
  await page.goto(origin+'/control/videos');await page.waitForSelector('[data-trial]');
  await page.locator('#collection-create summary').click();await page.locator('#collection-series').selectOption(String(fixture.series_id));
  assert.match(await page.locator('#collection-form [name=source_url]').inputValue(),/synthetic-author/);
  await page.locator('#collection-form button').click();await page.waitForFunction(()=>!document.querySelector('#collection-create').open);
  assert.equal(await page.locator('#download-ranking option').count(),1);
  assert.equal(await page.locator('#catalogs tbody tr').count(),3);
  await page.locator('[data-discover]').click();await page.waitForFunction(()=>document.querySelector('#catalogs tbody').children.length===4);
  assert.equal(await page.locator('#catalogs tbody tr').count(),4);
  assert.match(await page.locator('[data-feedback]').textContent(),/新增 1 条/);
  assert.equal(await page.locator('#catalogs tbody').getByText('其他短剧第1集').count(),0);
  await page.locator('[data-discover]').click();await page.waitForFunction(()=>document.querySelector('[data-discover]').textContent.includes('重新读取'));
  await page.locator('[data-discovery-keyword]').fill('修改中的筛选词');await page.waitForTimeout(4300);
  assert.equal(await page.locator('[data-discovery-keyword]').inputValue(),'修改中的筛选词');
  // Confirm the discovered episode explicitly; publication time is not required.
  const row=page.locator('#catalogs tr').filter({hasText:'800001'});
  await row.locator('[data-review]').click();
  for(const key of ['ownership','main_episode','not_duplicate'])await page.locator(`#episode-form [name=${key}]`).check();
  await page.locator('#episode-form [name=status]').selectOption('approved');await page.locator('#episode-form [name=access]').selectOption('public');
  await page.locator('#episode-form [name=note]').fill('合成目录：确认原作者本剧第119集正片');
  await page.locator('#episode-form > button').click();await page.waitForFunction(()=>!document.querySelector('#episode-dialog').open);
  await page.locator('[data-batch]').click();await page.waitForFunction(()=>document.querySelector('[data-feedback]').textContent.includes('加入 1 集'));
  execFileSync('.runtime/Scripts/python.exe',['tests/process_collections_fixture.py'],{env:process.env});
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelectorAll('[data-media]').length===4);
  await page.locator('#catalogs tr').filter({hasText:'800001'}).locator('[data-media]').click();
  await page.waitForFunction(()=>document.querySelector('#preview').readyState>=2);
  for(const key of ['beginning','middle','ending','sound'])await page.locator(`#media-form [name=${key}]`).check();
  await page.locator('#media-form [name=note]').fill('合成视频开头中段结尾声音均通过');await page.locator('#media-form > button').click();
  await page.waitForFunction(()=>!document.querySelector('#media-dialog').open);
  await page.locator('[data-publication]').click();await page.waitForFunction(()=>document.querySelector('[data-publication]').textContent.includes('隐藏'));
  await page.screenshot({path:out+'control-desktop.png',fullPage:true});
  // A separate browser has no administrator session.
  const visitor=await browser.newContext({viewport:{width:1440,height:1000}}),guest=await visitor.newPage();
  guest.on('pageerror',error=>errors.push(error.message));
  await guest.goto(origin+'/?area=manual');await guest.waitForSelector('#manual-cards .drama-card');
  assert.equal(await guest.locator('#manual-cards .drama-card').count(),1);
  assert.equal(await guest.locator('#douyin-area').isVisible(),false);
  await guest.screenshot({path:out+'home-desktop.png',fullPage:true});
  await guest.locator('#manual-cards .cover-link').click();await guest.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);
  assert.equal(await guest.locator('.episode-choice').count(),3); // Fourth fixture episode is still pending human inspection.
  assert.equal(await guest.locator('#watch-report').isVisible(),false);
  await guest.locator('.episode-choice').last().click();await guest.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);
  const download=guest.waitForEvent('download');await guest.locator('#watch-download').click();const saved=await download;
  assert.equal(await saved.failure(),null);
  const hash=path=>crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex');
  assert.equal(hash(await saved.path()),hash(out+'synthetic.mp4'));
  await guest.reload();await guest.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);
  await guest.screenshot({path:out+'player-desktop.png',fullPage:true});
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:900});await page.goto(`${origin}/control/videos?catalog_id=${fixture.catalog_id}`);await page.waitForSelector('[data-trial]');await overflow();
   if(width===390)await page.screenshot({path:out+'control-mobile.png',fullPage:true});
   await guest.setViewportSize({width,height:900});await guest.goto(origin+'/?area=manual');await guest.waitForSelector('#manual-cards .drama-card');assert.ok(await guest.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   if(width===390)await guest.screenshot({path:out+'home-mobile.png',fullPage:true});
   await guest.locator('#manual-cards .cover-link').click();await guest.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);assert.ok(await guest.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   if(width===390)await guest.screenshot({path:out+'player-mobile.png',fullPage:true});
  }
  await page.locator('[data-publication]').click();await page.waitForFunction(()=>document.querySelector('[data-publication]').textContent.includes('显示'));
  assert.equal((await guest.request.get(`${origin}/api/library/collections/${fixture.catalog_id}`)).status(),404);
  await guest.goto(origin+'/?area=manual');assert.equal(await guest.locator('#manual-cards .drama-card').count(),0);
  assert.deepEqual(errors,[]);
  const result={result:'passed',noRankingsCreated:true,reusesSeriesAndCatalog:true,pagedDiscovery:true,filtersAndDedup:true,manualDirectoryReview:true,syntheticWorker:true,humanMediaGate:true,explicitPublication:true,anonymousPlaybackAndSave:true,downloadDigest:true,unpublishRevokes:true,inputProtection:true,widths:[1440,768,390,320],errors};
  fs.writeFileSync(out+'acceptance.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){await page.screenshot({path:out+'failure.png',fullPage:true});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
