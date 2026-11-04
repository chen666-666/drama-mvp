/* Real isolated app, synthetic video: daily dropdown, inherited playback and saves. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8005';
assert.equal(base,'http://127.0.0.1:8005');
const out=path.resolve(__dirname,'../data/resource-reuse-ui'),fixture=JSON.parse(fs.readFileSync(path.join(out,'fixture.json')));
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto(base+'/');
  assert.deepEqual(await page.locator('#library-ranking option').evaluateAll(nodes=>nodes.map(n=>Number(n.value))),[fixture.latest,fixture.second]);
  assert.equal(await page.locator('#library-ranking').inputValue(),String(fixture.latest));
  const play=page.locator(`#series-${fixture.series_id} [data-watch]`);
  await play.click();
  await page.waitForFunction(()=>document.getElementById('watch-video').readyState>=2);
  assert.equal(await page.locator('.episode-choice').count(),2);
  await page.locator('.episode-choice').last().click();
  const saved=page.waitForEvent('download');
  await page.locator('#watch-download').click();
  const download=await saved;
  assert.equal(await download.failure(),null);
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(await download.path())).digest('hex'),fixture.sha256);
  await page.goto(`${base}/watch?ranking_id=${fixture.first}&series_id=${fixture.series_id}`);
  await page.waitForFunction(()=>document.getElementById('watch-video').readyState>=2);
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('synthetic-admin');
  await page.locator('[name=password]').fill('synthetic-password');
  await page.locator('#login-form button').click();
  await page.waitForURL('**/control');
  await page.goto(`${base}/control/videos?ranking_id=${fixture.latest}&series_id=${fixture.series_id}`);
  await page.waitForSelector('.stats');
  assert.match(await page.locator('#catalogs').innerText(),/已沿用 2 集已审核资源/);
  assert.equal(await page.locator('[data-media]').count(),0);
  const csrf=(await (await page.request.get(base+'/api/auth/session')).json()).csrf_token;
  const api=async(url,data)=>{
   const response=data===undefined?await page.request.get(base+url):await page.request.post(base+url,{data,headers:{Origin:base,'X-CSRF-Token':csrf}});
   assert.ok(response.ok(),await response.text());return response.json();
  };
  const before=await api(`/api/downloads/rankings/${fixture.latest}`);
  const catalog=before.catalogs.find(c=>c.series_id===fixture.series_id);
  assert.equal(catalog.summary.reused,2);
  await api(`/api/downloads/catalogs/${catalog.id}/queue`,{episode_ids:catalog.episodes.map(e=>e.id),trial:false});
  const after=await api(`/api/downloads/rankings/${fixture.latest}`);
  assert.deepEqual(after.catalogs.find(c=>c.id===catalog.id).episodes.map(e=>e.job.id),catalog.episodes.map(e=>e.job.id));
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:950});
   await page.goto(base+'/');
   assert.equal(await page.locator('#library-ranking option').count(),2);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.screenshot({path:path.join(out,`home-${width}.png`),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({dailyLatest:true,playback:true,downloadSha256:true,history:true,adminReuse:true,noDuplicateJobs:true,widths:[1440,768,390,320],errors},null,2));
  console.log('Resource reuse browser acceptance passed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
