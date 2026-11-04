/* Use the isolated two-episode fixture from tests/prepare_publish_fixture.py. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {chromium} = require(process.env.DRAMA_PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.TOP3_SMOKE_URL || 'http://127.0.0.1:8003';
const target = new URL(origin);
if (target.hostname !== '127.0.0.1' || !['8001','8002','8003'].includes(target.port)) throw new Error('Use an isolated local UI service');
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const page = await browser.newPage({acceptDownloads:true, viewport:{width:1440,height:1000}});
  const errors = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => requests.push(new URL(r.url()).pathname));
  async function save(link) {
    const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
    assert.equal(await download.failure(), null);
    const bytes = fs.readFileSync(await download.path());
    const response = await page.request.get(download.url());
    assert.equal(response.status(), 200);
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), crypto.createHash('sha256').update(await response.body()).digest('hex'));
    assert.match(response.headers()['content-disposition'], /attachment/);
    assert.ok(bytes.length > 0);
  }
  try {
    await page.goto(origin);
    const publicSite = await page.locator('body').getAttribute('data-public') === 'true';
    assert.ok(await page.locator('[data-download]').count() >= 1);
    await page.locator('[data-download]').first().click();
    await page.waitForFunction(() => document.querySelector('#watch-video').readyState >= 2);
    await page.waitForSelector('#watch-download:not([hidden])');
    assert.equal(await page.locator('#watch-login').isVisible(), false);
    assert.match(await page.locator('#watch-download').getAttribute('href'), /^\/api\/library\/media\/\d+\/download$/);
    await save(page.locator('#watch-download'));
    const first = await page.locator('#watch-download').getAttribute('href');
    await page.locator('#watch-episode').selectOption({index:1}, {force:true});
    const second = await page.locator('#watch-download').getAttribute('href');
    assert.notEqual(first, second);
    await save(page.locator('#watch-download'));
    await page.reload();
    await page.waitForSelector('#watch-download:not([hidden])');
    assert.equal(await page.locator('#watch-download').getAttribute('href'), second);
    await save(page.locator('[data-save]').first());
    for (const size of [{width:320,height:740},{width:390,height:844},{width:844,height:390}]) {
      await page.setViewportSize(size);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.screenshot({path:`data/visitor-download-${publicSite?'public':'manage'}-mobile.png`,fullPage:true});
    if (publicSite) assert.equal(requests.includes('/api/auth/session'), false);
    assert.equal(requests.some(path => path.startsWith('/api/playback/')), false);
    assert.equal((await page.context().cookies()).some(c => c.name === 'drama_session'), false);
    const control = await page.request.get(new URL('/control', origin).href, {maxRedirects:0});
    assert.equal(control.status(), publicSite ? 403 : 303);
    const management = await page.request.get(new URL('/api/downloads/status', origin).href);
    assert.equal(management.status(), publicSite ? 403 : 401);
    assert.deepEqual(errors, []);
    const result = {passed:true, mode:publicSite?'public':'manage', anonymousDownloads:3, checksumMatched:true, episodeSwitch:true, refresh:true, adminProtected:true, mobileWidths:[320,390,844], errors};
    fs.writeFileSync(`data/visitor-download-${result.mode}-results.json`, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(e => {console.error(e);process.exitCode=1;});
