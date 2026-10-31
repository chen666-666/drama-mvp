/* Login-state UI test uses an isolated server and mocked search responses only. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.DRAMA_PLAYWRIGHT_MODULE || "playwright");
(async()=>{
  const base = process.env.TOP3_SMOKE_URL;
  if(!base || new URL(base).port !== "8001") throw new Error("Use isolated port 8001");
  const browser = await chromium.launch({channel:"chrome",headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  let job=null, saved=false, resumes=0;
  const progress = {pages_done:0,imported:0,skipped:0,requests:1,history:[{keyword:"AI短剧",sort:"likes",page:1,status:"needs_login",finished_at:new Date().toISOString()}]};
  try {
    await page.route("**/api/source/douk/status",route=>route.fulfill({json:{available:true,session:{configured:saved,saved_at:null}}}));
    await page.route("**/api/source/douk/session",async route=>{
      saved=route.request().method()==="POST";
      await route.fulfill({json:{configured:saved}});
    });
    await page.route(/\/api\/runs\/\d+\/search-jobs$/,async route=>{
      if(route.request().method()==="POST") {
        job={id:999,status:"needs_login",message:"抖音要求登录",progress,config:{budget:180},created_at:new Date().toISOString()};
        return route.fulfill({json:job});
      }
      await route.fulfill({json:job?[job]:[]});
    });
    await page.route("**/api/search-jobs/999/resume",route=>{resumes++;job.status="completed";job.message="合成 UI 测试完成";return route.fulfill({json:job});});
    await page.goto(base);
    await page.locator("#new-run").click();
    await page.locator('#run-form [name="name"]').fill("合成测试 · 自动搜索页面");
    await page.locator('#run-form [name="cutoff_at"]').fill(new Date(Date.now()-300000+8*3600000).toISOString().slice(0,19));
    await page.locator("#run-form button").click();
    await page.waitForSelector("#search-section:not([hidden])");
    await page.locator("#start-search").click();
    await page.waitForFunction(()=>document.querySelector("#login-settings").open);
    assert.match(await page.locator("#search-progress").innerText(),/需要登录/);
    assert.equal(await page.locator('#session-form [name="cookie"]').getAttribute("type"),"password");
    await page.locator('#session-form [name="cookie"]').fill("sessionid=synthetic-ui-secret");
    await page.locator("#session-form button").first().click();
    await page.waitForFunction(()=>document.querySelector('#session-form [name="cookie"]').value === "");
    assert(saved);
    assert(!(await page.locator("body").innerText()).includes("synthetic-ui-secret"));
    await page.locator("#resume-search").click();
    await page.waitForFunction(()=>document.querySelector("#search-progress").textContent.includes("采集完成"));
    assert.equal(resumes,1);
    await page.locator("#clear-session").click();
    await page.waitForFunction(()=>document.querySelector("#session-status").textContent.includes("尚未配置"));
    assert(!saved);
    await page.screenshot({path:"data/search-ui-desktop.png",fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:"data/search-ui-mobile.png",fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors,[]);
    process.stdout.write(JSON.stringify({result:"passed",loginPause:true,resume:true,credentialCleared:true,mobileOverflow:false,browserErrors:errors})+"\n");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
