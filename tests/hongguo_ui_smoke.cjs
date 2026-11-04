/* Use a new DB seeded by prepare_hongguo_fixture.py and tests/run_ui_server.py. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.DRAMA_PLAYWRIGHT_MODULE || "playwright");
const {createApi} = require("./ui_auth.cjs");
const base = process.env.TOP3_SMOKE_URL;
if(!base || !["8001", "8002"].includes(new URL(base).port) || new URL(base).hostname !== "127.0.0.1") {
  throw new Error("Use an isolated loopback UI server on port 8001 or 8002");
}
const origin = new URL(base).origin;

(async () => {
  const api = await createApi(origin);
  const time = offset => new Date(Date.now() + offset).toISOString();
  const run = await api("/api/runs", {name:"合成红果筛选回归", cutoff_at:time(-60000)});
  const fields = ["video_id","original_url","title","series_key","series_title","author_id","author_name","published_at","sampled_at","likes","comments","favorites","shares"];
  const rows = [1,2,3].map(i => [String(996700+i),`https://www.douyin.com/video/${996700+i}`,`合成正片${i}`,`hongguo-ui-${i}`,`抖音对照作品${i}`,`hongguo-ui-${i}`,`合成作者${i}`,time(-86400000),time(-10000),100-i,1,1,1]);
  assert.equal((await api(`/api/runs/${run.id}/import/csv`, {content:[fields,...rows].map(row=>row.join(",")).join("\n")})).imported,3);
  const candidates = await api(`/api/runs/${run.id}/candidates`);
  const common = {status:"approved", reason:"合成测试依据", evidence_url:"https://www.douyin.com/"};
  for(const row of candidates) {
    await api("/api/reviews", {...common, target_type:"series", target_id:row.series_id, ai_type:"AI漫剧", ai_basis:"合成制作声明", checks:{story:true, ai:true, original_author:true}});
    await api("/api/reviews", {...common, target_type:"episode", target_id:row.episode_id, checks:{main_episode:true, ownership:true, public:true, not_duplicate:true}});
  }
  const ranking = await api(`/api/runs/${run.id}/rankings`, {});
  const browser = await chromium.launch({channel:"chrome", headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const visibleIds = () => page.locator(".hongguo-card:not([hidden])").evaluateAll(nodes=>nodes.map(n=>Number(n.dataset.id)));
  const select = (id, value) => page.locator(`#${id}`).selectOption(value);
  const reset = () => page.locator("#hongguo-reset").click();
  const output = path.join(process.cwd(), "data/hongguo-ui");
  fs.mkdirSync(output, {recursive:true});
  try {
    // Server-rendered cards must already have the default order without JavaScript.
    const plain = await browser.newContext({javaScriptEnabled:false});
    const plainPage = await plain.newPage();
    await plainPage.goto(`${origin}/legacy`);
    assert.deepEqual(await plainPage.locator(".hongguo-card").evaluateAll(nodes=>nodes.map(n=>Number(n.dataset.id))), [3,2,8,1,4,5,6,7]);
    await plain.close();

    await page.goto(`${origin}/?ranking_id=${ranking.id}`);
    await page.locator("[data-area=hongguo]").click();
    assert.deepEqual(await visibleIds(), [3,2,8,1,4,5,6,7]);
    const douyinBefore = await page.locator("#douyin-cards").innerHTML();
    await select("hongguo-sort", "score-asc");
    assert.deepEqual(await visibleIds(), [7,6,5,1,4,8,2,3]);
    await select("hongguo-sort", "recent");
    assert.deepEqual(await visibleIds(), [7,8,6,5,3,2,1,4]);
    assert.equal(await page.locator("#hongguo-recent-note").isVisible(), true);
    await select("hongguo-sort", "title");
    assert.deepEqual(await visibleIds(), [3,1,4,6,2,7,8,5]);
    assert.equal(await page.locator("#hongguo-recent-note").isVisible(), false);

    const tags = await page.locator("#tag-filter option").allTextContents();
    assert.equal(tags.filter(tag=>tag==="悬疑").length, 1);
    assert.equal(tags.includes("悬疑题材"), false);
    assert.equal(tags.includes("穿越题材"), false);
    for(const [value, expected] of [["9",[3,2]], ["8",[3,2,8,1,4]], ["7",[3,2,8,1,4,5]], ["below7",[6,7]]]) {
      await reset(); await select("score-filter", value);
      assert.deepEqual(await visibleIds(), expected);
    }
    await reset();
    await select("category-filter", "漫剧");
    await select("tag-filter", "悬疑");
    await select("score-filter", "8");
    await page.locator("#drama-search").fill("白昼");
    assert.deepEqual(await visibleIds(), [1,4]);
    assert.equal(await page.locator("#hongguo-count").innerText(), "共找到 2 部");
    await page.locator("#drama-search").fill("");
    await select("tag-filter", "温情");
    assert.deepEqual(await visibleIds(), []);
    assert.equal(await page.locator("#no-results").isVisible(), true);
    assert.equal(await page.locator("#hongguo-count").innerText(), "共找到 0 部");
    assert.equal(await page.locator("#douyin-cards").innerHTML(), douyinBefore);
    assert.equal(await page.locator("#douyin-cards .drama-card:not([hidden])").count(), 3);
    await reset();
    assert.deepEqual(await visibleIds(), [3,2,8,1,4,5,6,7]);
    for(const tag of ["O'Reilly", '双"引号', "<img src=x onerror=bad()>"]) {
      await select("tag-filter", tag);
      assert.deepEqual(await visibleIds(), [8]);
    }
    assert.equal(await page.locator("#tag-filter img, .hongguo-card script, .tags img").count(), 0);
    await reset();
    await page.locator('[data-area="hongguo"]').click();
    assert.equal(await page.locator("#douyin-area").isVisible(), false);
    await select("category-filter", "漫剧");
    await page.locator('[data-area="douyin"]').click();
    assert.equal(await page.locator("#douyin-cards .drama-card:not([hidden])").count(), 3);
    await page.locator('[data-area="hongguo"]').click();
    assert.deepEqual(await visibleIds(), [3,1,4]);
    await reset();
    await page.locator("#hongguo-area").scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,"desktop.png"), fullPage:true});
    for(const width of [320,390,600,768]) {
      await page.setViewportSize({width,height:900});
      await page.locator("#hongguo-area").scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
      const boxes = await page.locator(".hongguo-filters select, #hongguo-reset").evaluateAll(nodes=>nodes.map(n=>({height:n.getBoundingClientRect().height, fontSize:parseFloat(getComputedStyle(n).fontSize)})));
      assert.ok(boxes.every(box=>box.height >= 44));
      if(width<=600) assert.ok(boxes.slice(0,4).every(box=>box.fontSize>=16));
      if(width<=600) assert.ok(await page.locator(".hongguo-card .score").evaluateAll(nodes=>nodes.every(n=>n.scrollWidth<=n.clientWidth)), `score label overflow at ${width}`);
      if(width===390) {
        await page.screenshot({path:path.join(output,"mobile.png"), fullPage:true});
        await page.locator(".hongguo-filters").screenshot({path:path.join(output,"mobile-filters.png")});
      }
    }
    await page.goto(`${origin}/legacy`);
    assert.equal(await page.locator('[data-area="hongguo"]').getAttribute("aria-pressed"), "true");
    assert.deepEqual(await visibleIds(), [3,2,8,1,4,5,6,7]);
    await page.route("**/legacy?synthetic_empty=1", route=>route.fulfill({contentType:"text/html", body:fs.readFileSync(path.join(output,"empty.html"),"utf8")}));
    await page.goto(`${origin}/legacy?synthetic_empty=1`);
    assert.deepEqual(await visibleIds(), []);
    assert.equal(await page.locator("#hongguo-count").innerText(), "共找到 0 部");
    assert.equal(await page.locator("#no-results").innerText(), "暂未收录红果短剧。");
    await select("hongguo-sort", "recent");
    await reset();
    assert.equal(await page.locator("#no-results").isVisible(), true);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output,"acceptance.json"), JSON.stringify({passed:true, ranking:ranking.id, viewports:[320,390,600,768,1440], errors},null,2));
    console.log("Red Fruit UI passed: four sorts, score boundaries, combined filters, safe tags, reset, Top3 isolation, SSR, mobile layout.");
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
