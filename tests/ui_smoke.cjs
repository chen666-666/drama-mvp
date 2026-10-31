/* Run only against an isolated test server; see the required URL below. */
const assert = require("node:assert/strict");
const { chromium } = require(process.env.DRAMA_PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const base = process.env.TOP3_SMOKE_URL;
  if (!base || new URL(base).port !== "8001") throw new Error("Use an isolated test database served on port 8001; set TOP3_SMOKE_URL.");
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const timestamp = offset => new Date(Date.now() + offset).toISOString();
  const beijingInput = offset => new Date(Date.now() + offset + 8*3600000).toISOString().slice(0,19);
  try {
    await page.goto(new URL("/workbench",base).href);
    await page.locator("#new-run").click();
    await page.waitForSelector("#run-form:not([hidden])");
    await page.locator('#run-form [name="name"]').fill("合成测试 · 页面验收");
    await page.locator('#run-form [name="cutoff_at"]').fill(beijingInput(-300000));
    await page.locator('#run-form button').click();
    await page.waitForSelector("#workspace:not([hidden])");
    await page.waitForFunction(()=>document.querySelector('#run-select option:checked')?.textContent.includes('合成测试 · 页面验收') && document.getElementById('run-form').hidden);
    const fields = ["video_id","original_url","title","series_key","series_title","author_id","author_name","published_at","sampled_at","likes","comments","favorites","shares","keyword","sort_order","position"];
    const raw = [["900100","a","同名合成测试剧","a",100,2], ["900101","a","同名合成测试剧","a",300,2], ["900102","b","同名合成测试剧","b",200,""], ["900103","c","合成测试剧三","c",50,2], ["900104","d","合成测试剧四","d",25,2]];
    const rows = raw.map(([id,key,title,author,likes,shares])=>[id,`https://www.douyin.com/video/${id}`,`合成正片${id}`,key,title,author,`合成作者${author}`,timestamp(-2*86400000),timestamp(-60000),likes,1,1,shares,"AI短剧","点赞","前10条"]);
    const csv = [fields,...rows].map(row=>row.join(",")).join("\n");
    await page.locator('#csv-form input[type="file"]').setInputFiles({name:"synthetic.csv",mimeType:"text/csv",buffer:Buffer.from(csv,"utf8")});
    const [importResponse] = await Promise.all([
      page.waitForResponse(response=>response.url().includes('/import/csv')),
      page.locator("#csv-form button").click()
    ]);
    const imported = await importResponse.json();
    assert.equal(imported.imported,5,JSON.stringify(imported));
    await page.waitForFunction(()=>document.querySelectorAll("#candidate-rows tr").length === 5);
    assert.match(await page.locator("#similar-titles").innerText(), /同名/);
    await page.locator('[data-metadata="series"]').first().click();
    await page.locator('#metadata-form [name="title_note"]').fill("合成测试剧名说明");
    await page.locator('#metadata-form [name="reason"]').fill("合成页面资料整理");
    await page.locator('#metadata-form > button').click();
    await page.waitForFunction(()=>!document.getElementById("metadata-dialog").open);
    await page.waitForFunction(()=>document.getElementById("candidate-rows").textContent.includes("合成测试剧名说明"));
    await page.locator('[data-metadata="episode"]').first().click();
    await page.locator('#metadata-form [name="episode_number"]').fill("1");
    await page.locator('#metadata-form [name="content_kind"]').selectOption("single");
    await page.locator('#metadata-form [name="reason"]').fill("合成测试第一集");
    await page.locator('#metadata-form > button').click();
    await page.waitForFunction(()=>!document.getElementById("metadata-dialog").open);
    await page.waitForFunction(()=>document.getElementById("candidate-rows").textContent.includes("单集或独立短片"));
    const seriesIds = await page.locator('[data-review="series"]').evaluateAll(nodes=>[...new Set(nodes.map(node=>node.dataset.id))]);
    let checkedError = false;
    for(const id of seriesIds) {
      await page.locator(`[data-review="series"][data-id="${id}"]`).first().click();
      await page.locator('#review-form [name="ai_type"]').fill("AI漫剧");
      await page.locator('#review-form [name="ai_basis"]').fill("明确合成测试依据，不代表真实作品");
      await page.locator('#review-form [name="reason"]').fill("合成测试原作者声明");
      if(!checkedError) {
        await page.locator('#review-form button[type="submit"], #review-form > button').last().click();
        await page.waitForSelector("#review-dialog .dialog-message");
        checkedError = true;
      }
      for(const checkbox of await page.locator('#review-checks input[type="checkbox"]').all()) await checkbox.check();
      await page.locator('#review-form > button').click();
      await page.waitForFunction(()=>!document.getElementById("review-dialog").open);
      await page.waitForTimeout(100);
    }
    for(const id of ["900100","900101","900102","900103","900104"]) {
      const row = page.locator("#candidate-rows tr").filter({has:page.locator(`a.source-link[href="https://www.douyin.com/video/${id}"]`)});
      await row.locator('[data-review="episode"]').click();
      for(const checkbox of await page.locator('#review-checks input[type="checkbox"]').all()) await checkbox.check();
      await page.locator('#review-form [name="reason"]').fill("合成测试正片归属及公开性");
      await page.locator('#review-form > button').click();
      await page.waitForFunction(()=>!document.getElementById("review-dialog").open);
      await page.waitForTimeout(100);
    }
    await page.locator("#generate-ranking").click();
    await page.waitForSelector(".rank-card");
    assert.equal(await page.locator(".rank-card").count(),3);
    assert.match(await page.locator("#ranking-content").innerText(), /统一按公开点赞/);
    assert.match(await page.locator(".rank-card").first().innerText(), /300/);
    const firstVersion = await page.locator("#ranking-select").inputValue();
    await page.locator("#generate-ranking").click();
    await page.waitForFunction(()=>document.querySelectorAll("#ranking-select option").length === 2);
    await page.locator("#ranking-select").selectOption({index:1});
    await page.waitForFunction(id=>document.querySelector("#report-link").href.includes(`/rankings/${id}/`),firstVersion);
    await page.screenshot({path:"data/ui-desktop.png",fullPage:true});
    const report = await browser.newPage();
    await report.goto(new URL(await page.locator("#report-link").getAttribute("href"),base).href);
    assert.match(await report.locator("body").innerText(), /采样/);
    await report.screenshot({path:"data/ui-report.png",fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:"data/ui-mobile.png",fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:"passed", series:seriesIds.length,episodes:5,selected:3,versions:2,dialogErrorsVisible:true,mobileOverflow:false,browserErrors:errors}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
