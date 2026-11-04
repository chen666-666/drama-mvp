/* Management page interactions use mocked release data and never upload anything. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const url=process.env.TOP3_SMOKE_URL;
if(!url||new URL(url).hostname!=='127.0.0.1'||new URL(url).port!=='8004')throw new Error('Use the isolated synthetic management service on port 8004');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1365,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const bundle={release_id:'synthetic-preview',created_at:new Date().toISOString(),video_count:1,ranking_ids:[4],gaps:[{series_title:'合成待检查剧',video_id:'1002',reason:'人工抽检尚未通过'}]};
  let built=false;
  try {
    await page.goto(url+'/login');
    await page.locator('[name=username]').fill('synthetic-admin');
    await page.locator('[name=password]').fill('synthetic-password');
    await page.locator('#login-form button').click();
    await page.waitForURL('**/downloads');
    await page.route('**/api/downloads/rankings',route=>route.fulfill({json:[{id:4,name:'合成发布测试',version:1}]}));
    await page.route('**/api/publishing/**',route=>{
      const path=new URL(route.request().url()).pathname;
      if(path.endsWith('/preview'))return route.fulfill({json:bundle});
      if(path.endsWith('/build')){built=true;return route.fulfill({json:bundle});}
      if(path.endsWith('/history'))return route.fulfill({json:{bundles:built?[bundle]:[],published:null}});
      if(path.endsWith('/config'))return route.fulfill({json:{configured:false,inventory_confirmed:false}});
      throw new Error('Unexpected publishing operation: '+path);
    });
    await page.goto(url+'/publish');
    await page.waitForSelector('#publish-ranking option',{state:'attached'});
    assert.equal(await page.locator('#build-release').isDisabled(),true);
    await page.locator('#preview-release').click();
    await page.waitForFunction(()=>!document.querySelector('#build-release').disabled);
    assert.ok((await page.locator('#publish-result').textContent()).includes('人工抽检尚未通过'));
    await page.locator('#build-release').click();
    await page.waitForFunction(()=>document.querySelector('#publish-history').textContent.includes('synthetic-preview'));
    assert.equal(await page.locator('#upload-release').isDisabled(),true);
    assert.equal(await page.locator('#activate-release').isDisabled(),true);
    await page.locator('#all-episodes').check();
    assert.equal(await page.locator('#build-release').isDisabled(),true);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:'data/publishing-ui/manage-page.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:'passed',preview:true,build:true,gapsVisible:true,unconfiguredTransferDisabled:true,mobile:true,noUpload:true,browserErrors:errors}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
