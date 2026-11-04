// Actual task template and scripts with synthetic responses; no production writes.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin))throw Error('Isolated server only');
const folder='data/finalize-ui';fs.mkdirSync(folder,{recursive:true});
const detail={id:3,run_id:5,run_name:'合成榜单按钮验收',status:'cancelled',stage:'review',message:'合成状态',cards:[],
 draft:{token:'a'.repeat(64),explanation:'合成草案',generation_blockers:[],review_scope:{assessment:'合成核验',pending:0,affecting_count:0,unknown_count:0},items:[],shortfall:'',disclaimer:''},ranking_id:null,catalogs:[],all_scoped_complete:false};
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[],writes=[],checks=[];let responseMode='error',finishPost,failDetail=false;
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await login(page,origin);
  await page.route('**/api/runs',r=>r.fulfill({json:[{id:5,name:detail.run_name,cutoff_at_beijing:'2026-11-03',keywords:[]}]}));
  await page.route('**/api/control/tasks',r=>r.fulfill({json:[{id:3,run_id:5,name:detail.run_name,status:'cancelled'},{id:4,run_id:5,name:detail.run_name,status:'active'}]}));
  await page.route(/\/api\/control\/tasks\/[34]$/,r=>failDetail?r.fulfill({status:503,json:{detail:'合成详情读取失败'}}):r.fulfill({json:detail}));
  await page.route('**/api/control/tasks/*/finalize',async r=>{
   writes.push(r.request().postDataJSON());
   if(responseMode==='error')return r.fulfill({status:400,json:{detail:'草案已变化，请刷新后重新确认'}});
   if(responseMode==='pending')await new Promise(resolve=>finishPost=resolve);
   detail.ranking_id=8;return r.fulfill({json:{ranking_id:8}});
  });
  await page.goto(origin+'/control/tasks?task_id=3');
  await page.waitForFunction(()=>document.querySelector('#task-finalize-status')?.textContent.includes('此任务已取消'));
  assert.ok(await page.locator('#task-finalize').isDisabled());assert.ok(await page.locator('#finalize-confirm').isDisabled());
  assert.equal(await page.locator('#task-finalize-status a').getAttribute('href'),'/control/tasks?task_id=4');checks.push('Cancelled task blocks generation and links to active task');
  detail.id=4;detail.status='active';await page.locator('#task-finalize-status a').click();
  await page.waitForFunction(()=>document.querySelector('#task-title').textContent.startsWith('#4'));
  assert.ok(await page.locator('#task-finalize').isDisabled());assert.ok(!await page.locator('#finalize-confirm').isChecked());
  await page.locator('#finalize-confirm').check();assert.ok(await page.locator('#task-finalize').isEnabled());
  await page.locator('#task-finalize').click();
  await page.waitForFunction(()=>document.querySelector('#task-finalize-status').textContent.includes('草案已变化'));
  assert.equal(writes.length,1);assert.equal(writes[0].acknowledged,true);checks.push('Explicit confirmation required and server error appears beside button');
  for(const status of ['paused','interrupted','complete']){
   detail.status=status;await page.locator('#task-refresh').click();
   await page.waitForFunction(()=>document.querySelector('#task-finalize').disabled);
   assert.ok(await page.locator('#finalize-confirm').isDisabled());
  }checks.push('Paused, interrupted and complete tasks cannot generate');
  detail.status='active';detail.draft.generation_blockers=['仍有候选未完成本批次互动更新'];await page.locator('#task-refresh').click();
  await page.waitForFunction(()=>document.querySelector('#task-finalize-status').textContent.includes('仍有候选'));
  assert.ok(await page.locator('#task-finalize').isDisabled());
  detail.draft.generation_blockers=[];detail.search_job={status:'running'};await page.locator('#task-refresh').click();
  await page.waitForFunction(()=>document.querySelector('#task-finalize-status').textContent.includes('请等待搜索'));
  assert.ok(await page.locator('#task-finalize').isDisabled());checks.push('Refresh blockers and running search remain enforced');
  detail.search_job=null;await page.locator('#task-refresh').click();
  await page.waitForFunction(()=>!document.querySelector('#finalize-confirm').disabled);
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});
   await page.locator('#finalize-confirm').check();
   await page.locator('#task-finalize').click();
   await page.waitForFunction(()=>document.querySelector('#task-finalize-status').textContent.includes('草案已变化'));
   await page.locator('#task-finalize-status').scrollIntoViewIfNeeded();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   assert.ok(await page.locator('#task-finalize-status').isVisible());
   await page.screenshot({path:`${folder}/error-${width}.png`});
  }checks.push('Inline errors and layout verified at four widths');
  responseMode='pending';finishPost=null;await page.locator('#task-finalize').click();
  await page.waitForFunction(()=>document.querySelector('#task-finalize').getAttribute('aria-busy')==='true');
  const before=writes.length;assert.ok(await page.locator('#task-finalize').isDisabled());
  await page.locator('#task-finalize').evaluate(button=>{button.dispatchEvent(new MouseEvent('click'));});
  assert.equal(writes.length,before);for(let n=0;!finishPost&&n<50;n++)await page.waitForTimeout(100);assert.ok(finishPost);finishPost();
  await page.waitForFunction(()=>!document.querySelector('#task-initialize').hidden);
  assert.ok(await page.locator('#task-finalize').isHidden());
  assert.match(await page.locator('#task-finalize-status').textContent(),/榜单 #8/);checks.push('Pending feedback, duplicate-click protection and successful result');
  detail.ranking_id=null;await page.goto(origin+'/control/tasks?task_id=4');
  await page.waitForFunction(()=>!document.querySelector('#task-finalize').hidden);
  await page.locator('#finalize-confirm').check();responseMode='pending';finishPost=null;await page.locator('#task-finalize').click();
  await page.waitForFunction(()=>document.querySelector('#task-finalize').getAttribute('aria-busy')==='true');
  for(let n=0;!finishPost&&n<50;n++)await page.waitForTimeout(100);assert.ok(finishPost);failDetail=true;finishPost();
  await page.waitForFunction(()=>document.querySelector('#task-finalize-status').textContent.includes('详情刷新失败'));
  assert.ok(await page.locator('#task-finalize').isHidden());checks.push('Saved ranking remains fixed when detail refresh fails');
  assert.deepEqual(errors,[]);fs.writeFileSync(`${folder}/acceptance.json`,JSON.stringify({checks,errors,synthetic_posts:writes.length},null,2));console.log(JSON.stringify({checks,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
