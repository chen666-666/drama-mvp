const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin))throw Error('Isolated server only');
const out=process.env.QUICK_REVIEW_TEST_OUT||'data/quick-reviews-ui/';fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await login(page,origin);await page.goto(origin+'/control/videos?catalog_id=1&view=inspection');await page.waitForSelector('[data-quick-start]');
  const detail=()=>page.request.get(origin+'/api/downloads/collections/1').then(r=>r.json());
  const before=await detail();assert.equal(before.catalogs[0].summary.manual_passed,0);
  await page.locator('[data-quick-start]').click();await page.locator('#quick-now').waitFor({state:'visible',timeout:90000});
  assert.match(await page.locator('#quick-errors').textContent(),/缺失/);
  assert.equal((await detail()).catalogs[0].summary.manual_passed,0);
  const bid=await page.evaluate(()=>sessionStorage.getItem('quick-review-1'));
  await page.locator('#quick-video').evaluate(video=>video.dispatchEvent(new Event('ended')));
  const watched=await (await page.request.get(origin+'/api/downloads/quick-reviews/'+bid)).json();assert.ok(watched.rows.every(row=>!row.watched.length));
  await page.locator('#quick-skip').click();await page.locator('#quick-problem').click();await page.locator('#quick-reason').fill('合成问题集：中段声音需要复查');await page.locator('#quick-problem-save').click();await page.locator('#quick-play').click();
  await page.waitForFunction(()=>document.querySelector('#quick-now').textContent.includes('中段'),{},{timeout:30000});
  await page.locator('#quick-close').click();
  assert.equal((await detail()).catalogs[0].summary.manual_passed,0);
  await page.locator('[data-quick-start]').click();await page.locator('#quick-now').waitFor({state:'visible',timeout:30000});
  assert.match(await page.locator('#quick-now').textContent(),/中段|结尾/);await page.locator('#quick-play').click();
  await page.waitForFunction(()=>!document.querySelector('#quick-summary').hidden,{},{timeout:30000});
  assert.equal((await detail()).catalogs[0].summary.manual_passed,0);
  assert.match(await page.locator('#quick-results').textContent(),/继续待审/);
  assert.match(await page.locator('#quick-results').textContent(),/发现问题/);
  await page.locator('#quick-note').fill('合成说明草稿关闭后保留');await page.locator('#quick-close').click();await page.locator('[data-quick-start]').click();await page.locator('#quick-summary').waitFor({state:'visible',timeout:30000});assert.equal(await page.locator('#quick-note').inputValue(),'合成说明草稿关闭后保留');
  await page.locator('#quick-finish').click();assert.equal(await page.locator('#quick-review-dialog').evaluate(el=>el.open),true);
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   if(width===390)await page.screenshot({path:out+'summary-mobile.png'});
  }
  await page.locator('#quick-confirm').check();await page.locator('#quick-sound').check();await page.locator('#quick-note').fill('合成快审：实际观看三个有声片段；问题集单独复查');
  await page.locator('#quick-finish').click();await page.waitForFunction(()=>!document.querySelector('#quick-review-dialog').open);
  const after=await detail(),rows=after.catalogs[0].episodes;
  assert.equal(rows.filter(row=>row.media?.manual_status==='passed').length,1);
  assert.equal(rows.filter(row=>row.media?.manual_status==='rejected').length,1);
  assert.equal(rows.filter(row=>row.media?.manual_status==='pending').length,2);
  assert.equal(after.catalogs[0].published,false);
  await page.screenshot({path:out+'inspection-mobile.png'});
  await page.locator('[data-media]').first().click();assert.equal(await page.locator('#media-dialog').evaluate(el=>el.open),true);await page.locator('[data-close=media-dialog]').click();
  assert.deepEqual(errors,[]);
  const result={passed:true,realFullDecodeAndClips:true,threeAudioSegments:true,missingFileSeparated:true,pauseAndResume:true,skipAndProblemPreserved:true,noAutomaticHumanPass:true,explicitChecksAndNote:true,onePassedOneRejectedTwoPending:true,noAutomaticPublication:true,originalInspectionWorks:true,widths:[1440,768,390,320],errors};
  fs.writeFileSync(out+'acceptance.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){await page.screenshot({path:out+'failure.png'});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
