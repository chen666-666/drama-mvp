/* Real browser decode, dedicated playback page and mobile layout. Synthetic public service only. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const url=process.env.TOP3_SMOKE_URL;
if(!url||new URL(url).hostname!=='127.0.0.1'||new URL(url).port!=='8003')throw new Error('Use the synthetic public service at 127.0.0.1:8003');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  // Real platform titles may be much wider than the mobile viewport.
  await page.route('**/api/library/rankings/*',async route=>{
    const response=await route.fetch(),data=await response.json();
    for(const catalog of data.catalogs)for(const episode of catalog.episodes)episode.title+=' · 这是用于检查手机选集栏的长标题'.repeat(12);
    await route.fulfill({response,json:data});
  });
  try {
    await page.goto(url);
    await page.waitForSelector('[data-watch]');
    assert.equal(await page.locator('[data-watch]').count(),1);
    assert.equal(await page.getByRole('link',{name:'下载管理',exact:true}).count(),0);
    const initial=page.url();
    await page.locator('[data-watch]').click();
    await page.waitForFunction(()=>{const video=document.querySelector('#watch-video');return video.readyState>=2&&video.videoWidth>0&&video.currentTime>0;});
    assert.notEqual(page.url(),initial);
    assert.equal(new URL(page.url()).pathname,'/watch');
    assert.ok(await page.locator('#watch-video').evaluate(video=>video.clientWidth>=innerWidth*0.6&&video.clientHeight>innerHeight*0.5));
    await page.locator('#watch-video').evaluate(video=>{video.pause();video.currentTime=4;});
    await page.waitForFunction(()=>Math.abs(document.querySelector('#watch-video').currentTime-4)<0.3);
    assert.equal(await page.locator('#watch-episode option').count(),2);
    const firstSource=await page.locator('#watch-video').getAttribute('src');
    await page.locator('[data-episode]').nth(1).click();
    await page.waitForFunction(old=>document.querySelector('#watch-video').getAttribute('src')!==old&&document.querySelector('#watch-video').readyState>=2,firstSource);
    const secondSource=await page.locator('#watch-video').getAttribute('src');
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);
    assert.equal(await page.locator('#watch-video').getAttribute('src'),secondSource);
    await page.locator('#watch-fullscreen').click();
    await page.waitForFunction(()=>document.fullscreenElement===document.querySelector('#watch-video'));
    await page.evaluate(()=>document.exitFullscreen());
    await page.locator('#watch-back').click();
    await page.waitForSelector('[data-watch]');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.locator('[data-watch]').click();
    await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:'data/publishing-ui/mobile-playback.png',fullPage:true});
    await page.setViewportSize({width:844,height:390});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.ok(await page.locator('#watch-video').evaluate(video=>video.clientHeight>innerHeight*0.65));
    // Browsers may require another gesture for sound after page navigation.
    await page.addInitScript(()=>{HTMLMediaElement.prototype.play=()=>Promise.reject(new DOMException('synthetic gesture required','NotAllowedError'));});
    await page.route('**/watch?**',async route=>{
      const response=await route.fetch();
      await route.fulfill({response,body:(await response.text()).replace('playsinline autoplay','playsinline')});
    });
    await page.reload();
    await page.waitForSelector('#watch-start:not([hidden])');
    await page.locator('#watch-start').click();
    await page.waitForSelector('#watch-start:not([hidden])');
    await page.goto(new URL('/watch?ranking_id=invalid&series_id=1',url).href);
    await page.waitForSelector('#watch-error:not([hidden])');
    assert.equal(await page.locator('#watch-episode').isDisabled(),true);
    assert.equal(await page.locator('#watch-video').getAttribute('src'),null);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:'passed',dedicatedPlaybackPage:true,decodedVideo:true,seek:true,episodeSelection:true,reload:true,fullscreen:true,mobile:true,landscape:true,autoplayFallback:true,invalidLink:true,browserErrors:errors}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
