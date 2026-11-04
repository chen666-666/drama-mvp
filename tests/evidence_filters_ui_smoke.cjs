// Real template/assets with synthetic API responses; no candidate or review writes.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const {login}=require('./ui_auth.cjs');
const origin=process.env.TOP3_SMOKE_URL||'http://127.0.0.1:8002';
if(!/^http:\/\/127\.0\.0\.1:800[12]$/.test(origin))throw Error('Isolated server only');
const out='data/evidence-filters-ui';fs.mkdirSync(out,{recursive:true});
const statuses=['approved','pending','excluded'];
const cards=statuses.map((status,index)=>({series_id:index+1,title:`合成作品${index+1} <测试>`,author_name:`合成作者${index+1}`,
 account_id:String(index+1),ai_basis:'原作者声明',author_url:'https://www.douyin.com/',series_status:status,
 gaps:[],changes:[],series_reviews:[],episodes:statuses.map((episode_status,i)=>({episode_id:index*3+i+1,
 title:`合成正片${i+1}`,video_id:String(index*3+i+1),episode_status,original_url:'https://www.douyin.com/',
 published_at_beijing:'2026-11-01 08:00',sampled_at_beijing:'2026-11-02 08:00',likes:60000,comments:1,
 favorites:null,shares:1,episode_reviews:[],sources:[],token:`synthetic-${index}-${i}`}))}));
const detail={id:1,run_id:1,run_name:'合成筛选验收',status:'paused',stage:'review',message:'仅合成数据',
 cards,draft:{explanation:'合成草案',review_scope:{assessment:'待核验',pending:3,affecting_count:0,unknown_count:0},items:[],shortfall:'',disclaimer:''},
 ranking_id:null,catalogs:[],all_scoped_complete:false};
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[];
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await login(page,origin);
  await page.route('**/api/runs',route=>route.fulfill({json:[{id:1,name:'合成批次',cutoff_at_beijing:'2026-11-01',keywords:['AI短剧']}]}));
  await page.route('**/api/control/tasks',route=>route.fulfill({json:[{id:1,name:detail.run_name,status:'paused'}]}));
  await page.route('**/api/control/tasks/1',route=>route.fulfill({json:detail}));
  await page.route('**/api/control/tasks/1/reviews',route=>{writes.push(route.request().postDataJSON());return route.fulfill({json:{message:'合成核验响应'}});});
  await page.goto(origin+'/control/tasks?task_id=1');
  await page.locator('#evidence-filters').waitFor();
  const ids=()=>page.locator('[data-candidate]').evaluateAll(nodes=>nodes.map(node=>Number(node.dataset.candidate)));
  const choose=async(status,target)=>{
   await page.locator(`[data-evidence-status="${status}"]`).click();
   await page.locator(`[data-evidence-target="${target}"]`).click();
  };
  await page.waitForFunction(()=>document.querySelectorAll('[data-candidate]').length===1);
  assert.deepEqual(await ids(),[2]);
  const cases=[['approved','series',[1,2]],['approved','episode',[1,4]],['approved','both',[1]],
   ['pending','series',[4,5]],['pending','episode',[2]],['pending','both',[5]],
   ['excluded','series',[7,8,9]],['excluded','episode',[3,6,9]],['excluded','both',[9]]];
  for(const [status,target,expected] of cases){
   await choose(status,target);assert.deepEqual(await ids(),expected,`${status}/${target}`);
   assert.match(await page.locator('#evidence-filter-summary').textContent(),new RegExp(`${expected.length} 条候选`));
   assert.equal(await page.locator(`[data-evidence-status="${status}"]`).getAttribute('aria-pressed'),'true');
   if(status==='excluded')assert.match(await page.locator('#evidence-filter-summary').textContent(),/已排除/);
  }
  await page.locator('[data-candidate="9"]').check();
  await choose('pending','both');
  assert.equal(await page.locator('[data-candidate]:checked').count(),0,'Leaving excluded clears selection');
  await page.locator('[data-candidate="5"]').check();
  await page.locator('#evidence-review [name=reason]').fill('保留的核验说明');
  await choose('approved','both');
  assert.equal(await page.locator('[data-candidate]:checked').count(),0);
  assert.equal(await page.locator('#evidence-review [name=reason]').inputValue(),'保留的核验说明');
  await page.locator('[data-candidate="1"]').check();
  await page.locator('#evidence-review [name=evidence_url]').fill('https://www.douyin.com/');
  await page.locator('#evidence-review button').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent==='合成核验响应');
  assert.equal(writes.length,1);assert.deepEqual(writes[0].episode_ids,[1]);
  assert.deepEqual(await ids(),[1],'Saved review retains filters');
  await page.locator('#task-refresh').click();
  assert.deepEqual(await ids(),[1],'Refresh retains filters');
  await page.locator('#evidence-filter-clear').click();assert.deepEqual(await ids(),[1,2,3,4,5,6,7,8,9]);
  assert.equal(await page.locator('[data-evidence-target]:disabled').count(),3);
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});await choose('excluded','episode');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Overflow at ${width}`);
   assert.ok(await page.locator('#evidence-filters button').evaluateAll(buttons=>buttons.every(b=>b.getBoundingClientRect().height>=44)));
   await page.locator('#evidence-filters').screenshot({path:`${out}/filters-${width}.png`});
  }
  await choose('pending','both');
  detail.cards=[cards[0]];
  await page.locator('#task-refresh').click();await page.locator('#evidence-cards .empty').waitFor();
  assert.match(await page.locator('#evidence-cards').textContent(),/当前分类暂无候选/);
  detail.cards=[];await page.locator('#task-refresh').click();
  await page.waitForFunction(()=>document.querySelector('#evidence-cards').textContent.includes('暂无候选。搜索完成后显示'));
  // The separate sources/review workspace uses the exact same classification.
  const candidateRows=cards.flatMap(card=>card.episodes.map(row=>({...card,...row,series_title:card.title,
   author_account_id:card.account_id,ai_type:'合成AI动画',title_note:'',content_kind:'single',content_note:'',missing_reason:''})));
  const run={id:1,name:'合成批次',mode:'sample',cutoff_at_beijing:'2026-11-01',window_hours:168,keywords:['AI短剧'],official_metadata:{}};
  await page.route('**/api/runs',route=>route.fulfill({json:[run,{...run,id:2,name:'合成另一批次'}]}));
  await page.route('**/api/runs/*/candidates',route=>route.fulfill({json:candidateRows}));
  await page.route('**/api/runs/*/rankings',route=>route.fulfill({json:[]}));
  await page.route('**/api/runs/*/search-jobs',route=>route.fulfill({json:[]}));
  await page.route('**/api/source/douk/status',route=>route.fulfill({json:{available:false,session:{configured:false}}}));
  await page.goto(origin+'/control/reviews?run_id=1');
  const tableIds=()=>page.locator('#candidate-rows [data-review=episode]').evaluateAll(nodes=>nodes.map(node=>Number(node.dataset.id)));
  await page.waitForFunction(()=>document.querySelectorAll('#candidate-rows tr').length===1);
  for(const [status,target,expected] of cases){await choose(status,target);assert.deepEqual(await tableIds(),expected,`Review workspace ${status}/${target}`);}
  await choose('approved','series');
  await page.locator('#candidate-search').fill('2');assert.deepEqual(await tableIds(),[2]);
  await page.locator('#candidate-search').fill('');
  await page.locator('#review-series').selectOption('2');assert.deepEqual(await tableIds(),[]);
  await choose('pending','series');assert.deepEqual(await tableIds(),[4,5]);
  await page.locator('#refresh').click();assert.deepEqual(await tableIds(),[4,5]);
  await page.locator('#run-select').selectOption('2');
  await page.waitForFunction(()=>document.querySelector('#run-info').textContent.includes('窗口'));
  await page.locator('#review-series').selectOption('');
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Review workspace overflow at ${width}`);
   await page.locator('#evidence-filters').screenshot({path:`${out}/review-filters-${width}.png`});
  }
  // A link from a todo or a known series must show the requested reviewed row.
  await page.goto(origin+'/control/reviews?run_id=1&episode_id=1');
  await page.waitForFunction(()=>document.querySelectorAll('#candidate-rows tr').length===1);
  assert.deepEqual(await tableIds(),[1]);assert.match(await page.locator('#evidence-filter-summary').textContent(),/全部候选/);
  await page.goto(origin+'/workbench?run_id=1');
  await page.locator('#review-workspace-link').click();await page.waitForURL('**/control/reviews?run_id=1');
  await page.waitForFunction(()=>document.querySelectorAll('#candidate-rows tr').length===1);
  assert.deepEqual(await tableIds(),[2]);
  assert.deepEqual(errors,[]);
  fs.writeFileSync(`${out}/acceptance.json`,JSON.stringify({passed:true,nineCombinations:true,mixedEpisodeStates:true,
   excludedFilters:true,selectionCleared:true,visibleSelectionSubmitted:true,formPreserved:true,refreshRetainsFilter:true,
   emptyStates:true,sharedReviewWorkspace:true,searchAndSeriesCombined:true,focusedLinks:true,workbenchEntry:true,
   widths:[1440,768,390,320],errors},null,2));
  console.log('Evidence filter browser acceptance passed');
 }catch(error){await page.screenshot({path:`${out}/failure.png`});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
