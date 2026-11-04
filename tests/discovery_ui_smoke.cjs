/* Real management pages and APIs on an isolated synthetic server, never production. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const base=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(base))throw new Error('Isolated server required');
const out=path.resolve(__dirname,'../data/episode-discovery-ui');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const post=async(route,body={})=>page.evaluate(async({route,body})=>{const s=await(await fetch('/api/auth/session')).json();const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(JSON.stringify(d));return d;},{route,body});
 try{
  await page.goto(base+'/control/videos');assert.match(page.url(),/\/login/);
  await login(page,base);await page.goto(base+'/control/videos?ranking_id=1&series_id=1');
  await page.getByRole('heading',{name:'自动补集',exact:true}).waitFor();
  await post('/api/test/discovery/advance',{turns:2});await page.locator('#refresh').click();
  await page.getByText(/新增 1 条待核验/).first().waitFor();
  const panel=page.locator('.discovery-panel');assert.match(await panel.innerText(),/待辨认 2/);
  await panel.getByText(/发现依据与待辨认线索/).click();
  assert.match(await panel.innerText(),/身份冲突/);assert.match(await panel.innerText(),/多集合集/);
  const pending=await page.evaluate(async()=>{const d=await(await fetch('/api/downloads/rankings/1')).json();return d.catalogs[0].episodes.find(r=>r.video_id==='90002');});
  assert.equal(pending.status,'pending');assert.equal(pending.access,'unknown');assert.equal(pending.job,null);
  await panel.getByRole('button',{name:'暂停整部作品共享查找',exact:true}).click();
  await page.getByText('管理员暂停',{exact:false}).first().waitFor();
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/control/discovery/catalogs/1')&&r.request().method()==='POST'),page.locator('.discovery-panel').getByRole('button',{name:'继续查找',exact:true}).click()]);
  await post('/api/test/discovery/advance',{turns:25});await page.locator('#refresh').click();
  await page.getByText(/本轮计划读取结束/).first().waitFor();
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`videos overflow ${width}`);
   await page.screenshot({path:path.join(out,`videos-${width}.png`),fullPage:true});
  }
  await page.goto(base+'/control/tasks?task_id=1');await page.locator('#task-supplement').waitFor({state:'visible'});
  assert.equal(await page.locator('#task-scope').isVisible(),false);
  await page.locator('#task-supplement').click();await page.waitForURL(/task_id=2/);
  await page.locator('#task-scope').waitFor({state:'visible'});
  const flows=await page.evaluate(async()=> (await fetch('/api/control/tasks')).json());
  assert.equal(flows.find(f=>f.id===1).status,'complete');assert.equal(flows.find(f=>f.id===2).stage,'scope');
  const d=await page.evaluate(async()=> (await fetch('/api/control/tasks/2')).json());
  assert.deepEqual(d.catalogs[0].workflow,{});assert(d.catalogs[0].unconfirmed_count>=2);
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`tasks overflow ${width}`);
   await page.screenshot({path:path.join(out,`tasks-${width}.png`),fullPage:true});
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify({passed:true,widths:[1440,768,390,320],checks:['async pending candidates','clues and conflicts','pause and continue','real end markers','explicit supplement','completed scope retained','no media download','anonymous login'],errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
