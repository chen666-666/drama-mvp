// Real APIs and templates on a guarded synthetic server; never calls the real platform.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL;
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin||''))throw Error('Isolated server only');
const out='data/candidate-pool-ui';fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const api=(path,data)=>page.evaluate(async({path,data})=>{
  const session=await(await fetch('/api/auth/session')).json();
  const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify(data)});
  return {status:response.status,data:await response.json()};
 },{path,data});
 const waitJob=async status=>{
  await page.waitForFunction(async status=>{
   const jobs=await(await fetch('/api/runs/2/search-jobs')).json();return jobs[0]?.status===status;
  },status,{timeout:30000});
 };
 try{
  await login(page,origin);
  const old=(await api('/api/rankings/1')).data;
  await page.goto(origin+'/control/collection?run_id=2&tab=search');
  await page.waitForFunction(()=>document.querySelector('#candidate-pool-summary').textContent.includes('补入 3 条'));
  await page.locator('#start-search').click();await waitJob('failed');
  await page.goto(origin+'/control/reviews?run_id=2');
  await page.waitForFunction(()=>document.querySelector('#candidate-pool-summary').textContent.includes('全部 4 条'));
  await page.locator('#refresh').click();
  await page.locator('[data-evidence-status=approved]').click();await page.locator('[data-evidence-target=both]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#candidate-rows tr').length===3);
  assert.equal(await page.locator('#candidate-rows tr').count(),3);
  assert.match(await page.locator('#candidate-pool-summary').textContent(),/全部 4 条候选.*补入 3 条.*更新失败 1 条/);
  assert.equal((await api('/api/runs/2/rankings',{})).status,400,'Failed detail refresh cannot replace the public ranking');
  assert.deepEqual((await api('/api/rankings/1')).data,old);
  await page.locator('#retry-candidate-pool').waitFor({state:'visible'});
  await page.locator('#retry-candidate-pool').click();await waitJob('completed');await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#candidate-pool-summary').textContent.includes('互动已取得 4 条'));
  assert.match(await page.locator('#candidate-pool-summary').textContent(),/互动已取得 4 条.*更新失败 0 条/);
  const ranked=await api('/api/runs/2/rankings',{});assert.equal(ranked.status,201);assert.equal(ranked.data.items.length,3);
  assert.deepEqual((await api('/api/rankings/1')).data,old);
  const home=await page.request.get(origin+'/');assert.ok((await home.text()).includes('合成新批次'));
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});await page.goto(origin+'/control/reviews?run_id=2');
   await page.waitForFunction(()=>document.querySelector('#candidate-pool-summary').textContent.includes('全部 4 条'));
   await page.locator('[data-evidence-status=approved]').click();await page.locator('[data-evidence-target=both]').click();
   await page.waitForFunction(()=>document.querySelectorAll('#candidate-rows tr').length===3);
   assert.equal(await page.locator('#candidate-rows tr').count(),3);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`overflow ${width}`);
   await page.screenshot({path:`${out}/reviews-${width}.png`});
  }
  await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/control/tasks');
  await page.locator('#task-run').selectOption('2');await page.locator('#start-task button').click();
  await page.locator('#task-detail').waitFor({state:'visible'});
  assert.match(await page.locator('#candidate-pool-summary').textContent(),/补入 3 条/);
  await page.locator('#refresh-candidate-pool').click();await waitJob('completed');await page.locator('#task-refresh').click();
  assert.match(await page.locator('#candidate-pool-summary').textContent(),/互动已取得 4 条/);
  assert.equal(await page.locator('#task-finalize').isEnabled(),true);
  assert.deepEqual((await api('/api/rankings/1')).data,old);
  await page.screenshot({path:`${out}/task.png`});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(`${out}/acceptance.json`,JSON.stringify({passed:true,threeOmittedVideosKept:true,failedRefreshVisible:true,
   staleRankingBlocked:true,retryWithoutSearch:true,historicalSnapshotUnchanged:true,newestPublicRanking:true,
   taskRepair:true,widths:[1440,768,390,320],errors},null,2));
  console.log('Candidate pool browser acceptance passed');
 }catch(e){await page.screenshot({path:`${out}/failure.png`});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
