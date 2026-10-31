/* Exercise the second-step UI against an isolated SQLite database and synthetic media. */
const assert=require('node:assert/strict');
const{execFileSync}=require('node:child_process');
const{chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.TOP3_SMOKE_URL;
if(!base||new URL(base).port!=='8001')throw new Error('Use isolated port 8001 and data/step2-ui.db');
const origin=new URL(base).origin;
async function api(path,data){const response=await fetch(origin+path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});const result=await response.json();assert.ok(response.ok,JSON.stringify(result));return result;}
const now=Date.now(),time=offset=>new Date(now+offset).toISOString();
(async()=>{
 const run=await api('/api/runs',{name:'合成测试 · 第二步页面验收',cutoff_at:time(-60000)});
 const keys=['video_id','original_url','title','series_key','series_title','author_id','author_name','published_at','sampled_at','likes','comments','favorites','shares'];
 const rows=[['990800','合成第一集',time(-86400000)],['990801','合成旧正片',time(-20*86400000)]].map(([id,title,published])=>[id,`https://www.douyin.com/video/${id}`,title,'step2-ui','合成下载验收剧','step2-ui','合成测试作者',published,time(-10000),100,1,1,1]);
 const imported=await api(`/api/runs/${run.id}/import/csv`,{content:[keys,...rows].map(r=>r.join(',')).join('\n')});assert.equal(imported.imported,2);
 const candidates=await api(`/api/runs/${run.id}/candidates`);
 const common={status:'approved',reason:'合成测试核验',evidence_url:'https://www.douyin.com/video/990800'};
 await api('/api/reviews',{...common,target_type:'series',target_id:candidates[0].series_id,ai_type:'AI漫剧',ai_basis:'合成测试声明',checks:{story:true,ai:true,original_author:true}});
 for(const row of candidates)await api('/api/reviews',{...common,target_type:'episode',target_id:row.episode_id,checks:{main_episode:true,ownership:true,public:true,not_duplicate:true}});
 const ranking=await api(`/api/runs/${run.id}/rankings`,{});
 const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(`${origin}/downloads?ranking_id=${ranking.id}`);await page.waitForSelector('[data-trial]');
  assert.equal(await page.locator('tbody tr').count(),2);assert.equal(await page.locator('[data-batch]').isDisabled(),true);
  assert.equal(await page.locator('[data-trial]').textContent(),'下载');
  assert.equal(await page.getByRole('link',{name:'播放原视频 ↗'}).count(),2);
  await page.locator('[data-review]').last().click();await page.locator('#episode-form [name=note]').fill('合成测试正片归属');await page.locator('#episode-form > button').click();await page.waitForFunction(()=>document.querySelector('#episode-dialog .modal-error').textContent.includes('通过前'));
  for(const key of ['ownership','main_episode','not_duplicate'])await page.locator(`#episode-form [name=${key}]`).check();
  await page.locator('#episode-form > button').click();await page.waitForFunction(()=>!document.querySelector('#episode-dialog').open);
  const queueRoute='**/api/downloads/catalogs/*/queue';
  await page.route(queueRoute,route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'合成测试：下载暂不可用'})}));
  await page.locator('[data-trial]').click();await page.waitForFunction(()=>document.querySelector('[data-feedback]').textContent.includes('下载暂不可用'));
  assert.equal(await page.locator('[data-feedback]').evaluate(n=>{const r=n.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}),true);
  await page.unroute(queueRoute);
  await page.locator('[data-trial]').click();await page.waitForSelector('[data-action=pause]');
  await page.locator('[data-trial]').click();await page.waitForFunction(()=>document.querySelector('[data-feedback]').textContent.includes('无需重复提交'));
  await page.locator('[data-action=pause]').click();await page.waitForSelector('[data-action=resume]');await page.locator('[data-trial]').click();await page.waitForSelector('[data-action=pause]');
  let detail=await api(`/api/downloads/rankings/${ranking.id}`);let job=detail.catalogs[0].episodes.find(e=>e.job)?.job;assert.equal(job.status,'queued');
  execFileSync('.runtime/Scripts/python.exe',['tests/process_download_fixture.py',String(job.id)],{env:{...process.env,DRAMA_DATABASE_URL:'sqlite:///D:/drama-mvp/data/step2-ui.db'},timeout:30000});
  await page.locator('#refresh').click();await page.waitForSelector('[data-media]');
  await page.locator('[data-trial]').click();await page.waitForFunction(()=>document.querySelector('[data-feedback]').textContent.includes('本集已下载'));
  await page.locator('[data-play]').click();await page.waitForFunction(()=>document.querySelector('#preview').readyState>=1);
  assert.ok(await page.locator('#preview').evaluate(n=>n.duration>0&&n.controls));
  await page.locator('#media-form [name=note]').fill('合成视频开头中段结尾及声音测试');await page.locator('#media-form > button').click();await page.waitForFunction(()=>document.querySelector('#media-dialog .modal-error').textContent.includes('抽看'));
  for(const key of ['beginning','middle','ending','sound'])await page.locator(`#media-form [name=${key}]`).check();await page.locator('#media-form > button').click();await page.waitForFunction(()=>!document.querySelector('#media-dialog').open);await page.waitForFunction(()=>!document.querySelector('[data-batch]').disabled);
  await page.locator('[data-batch]').click();await page.waitForSelector('[data-action=pause]');detail=await api(`/api/downloads/rankings/${ranking.id}`);job=detail.catalogs[0].episodes.find(e=>e.job?.status==='queued').job;
  execFileSync('.runtime/Scripts/python.exe',['tests/process_download_fixture.py',String(job.id)],{env:{...process.env,DRAMA_DATABASE_URL:'sqlite:///D:/drama-mvp/data/step2-ui.db'},timeout:30000});
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelectorAll('[data-media]').length===2);
  await page.locator('#export-package').click();await page.waitForSelector('#package-link:not([hidden])');const zip=await fetch(origin+await page.locator('#package-link').getAttribute('href'));assert.equal(zip.status,200);assert.ok((await zip.arrayBuffer()).byteLength>10000);
  await page.screenshot({path:'data/step2-downloads-tested.png',fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'data/step2-downloads-mobile-tested.png',fullPage:true});
  await page.goto(origin);await page.waitForSelector('#douyin-cards .drama-card');await page.locator('[data-area=hongguo]').click();assert.equal(await page.locator('#douyin-area').isVisible(),false);await page.locator('[data-area=douyin]').click();assert.equal(await page.locator('#hongguo-area').isVisible(),false);await page.locator('#drama-search').fill('不存在的合成作品');assert.equal(await page.locator('#douyin-cards .drama-card:visible').count(),0);await page.locator('#drama-search').fill('');assert.equal(await page.locator('#douyin-cards .drama-card:visible').count(),1);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'passed',episodesIncludingOlderThanSevenDays:2,trialAndManualGate:true,pauseResume:true,mediaAndExport:true,regionFilters:true,mobileOverflow:false,browserErrors:errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
