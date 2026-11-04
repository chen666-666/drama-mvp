/* Actual watch template and assets with synthetic responses; no live database or platform. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'data/episode-grid-ui');
fs.mkdirSync(out,{recursive:true});
let origin;
const clipPath=path.join(out,'synthetic.mp4');
if(!fs.existsSync(clipPath))execFileSync(path.join(root,'.runtime/Scripts/python.exe'),['-X','utf8','-c',
 "import sys,subprocess; from services.media import executable; subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=navy:s=320x240:d=3','-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-shortest',sys.argv[1]],check=True,capture_output=True)",clipPath],{cwd:root});
const clip=fs.readFileSync(clipPath);
let rows=[
 {id:201,title:'鳄鱼缅北65-66补发 #鳄鱼缅北 #剧情 #ai电影 #ai短剧'},
 {id:202,title:'鳄鱼缅北26-27（补发） #鳄鱼缅北 #剧情 #ai电影'},
 {id:203,title:'鳄鱼缅北1-13集 #鳄鱼缅北 #悬疑 #ai电影'},
 {id:204,title:'鳄鱼缅北1-5合集 #AI #悬疑 #ai电影 #短剧'},
 ...Array.from({length:90},(_,i)=>({id:i+1,episode_number:i+1,title:`另一部短剧第${i+1}集 #剧情`})).reverse(),
 {id:205,title:'预告 <img src=x onerror=alert(1)> #2026-2027集'},
 {id:206,title:'2026-11-01更新'},
].map(row=>({...row,video_id:String(row.id),url:`/api/library/media/${row.id}`,download_url:`/api/library/media/${row.id}/download`}));
let manage=false,publicSite=false,empty=false,authenticated=false,adminRows=null;
function detail(){return {ranking:{items:[{series_id:129,series_title:'合成短剧 · 网格选集预览',author_name:'合成作者',selected:true,video_id:'1',original_url:'https://www.douyin.com/video/1'}]},catalogs:[{series_id:129,summary:{full_series:'全剧完整性未确认'},episodes:empty?[]:rows.map(row=>manage?{...row,file_url:row.download_url,media:{id:row.id}}:row)}]};}
function html(){return execFileSync(path.join(root,'.runtime/Scripts/python.exe'),['-X','utf8','-c',
 "import sys,json; from jinja2 import Environment,FileSystemLoader,select_autoescape; d=json.load(sys.stdin); env=Environment(loader=FileSystemLoader('templates'),autoescape=select_autoescape()); print(env.get_template('watch.html').render(static_url=lambda name:'/static/'+name,manage_player=d['manage'],public_site=d['public'],initial_playback=d['detail'] if not d['manage'] else None))"],
 {cwd:root,input:JSON.stringify({manage,public:publicSite,detail:detail()}),encoding:'utf8'});}
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[],requests=[],downloads=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('download',download=>downloads.push(download));
 const server=require('node:http').createServer(async(req,res)=>{
  const route={request:()=>({url:()=>origin+req.url,method:()=>req.method}),fulfill:async(options)=>{
   let body=options.json?JSON.stringify(options.json):options.path?fs.readFileSync(options.path):options.body||'';
   const headers={'content-type':options.json?'application/json':options.contentType||'text/plain',...options.headers};
   res.writeHead(options.status||200,headers);res.end(body);
  }};
  const url=new URL(route.request().url());requests.push({path:url.pathname,method:route.request().method()});
  if(url.pathname.startsWith('/static/'))return route.fulfill({path:path.join(root,url.pathname),contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css'});
  if(url.pathname==='/watch'||url.pathname==='/downloads/watch')return route.fulfill({body:html(),contentType:'text/html'});
  if(url.pathname==='/api/auth/session')return route.fulfill({json:{authenticated}});
  if(url.pathname.startsWith('/api/playback/'))return route.fulfill({json:{episodes:(adminRows||rows).map(row=>({...row,file_url:row.download_url}))}});
  if(url.pathname.startsWith('/api/downloads/collections/')||url.pathname.startsWith('/api/downloads/rankings/'))return route.fulfill({json:detail()});
  if(url.pathname.includes('/media/'))return route.fulfill({body:clip,contentType:'video/mp4',headers:url.pathname.endsWith('/download')?{'content-disposition':`attachment; filename="synthetic-${url.pathname.split('/').at(-2)}.mp4"`,'cache-control':'no-store'}:{}});
  return route.fulfill({status:404,body:'Synthetic test: unknown route'});
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
 const visibleIds=()=>page.locator('.episode-choice:visible').evaluateAll(nodes=>nodes.map(node=>Number(node.dataset.episode)));
 async function ready(url){await page.goto(origin+url);await page.waitForFunction(()=>document.querySelector('#watch-video').readyState>=2);}
 try{
  await ready('/watch?catalog_id=4&series_id=129&episode_id=1');
  assert.equal(await page.locator('.episode-choice').count(),96);
  assert.deepEqual(await page.locator('.episode-range').allTextContents(),['1–30','31–60','61–90','待定集号']);
  let ids=await visibleIds();
  for(const [anchor,id] of [[5,204],[13,203],[27,202]])assert.equal(ids[ids.indexOf(anchor)+1],id);
  assert.equal((await page.locator('[data-episode="204"]').innerText()).replace(/\s/g,''),'合集1–5');
  await page.locator('[data-episode="204"]').click();
  assert.equal(new URL(page.url()).searchParams.get('episode_id'),'204');
  assert.equal(await page.locator('[aria-current=true][data-episode]').getAttribute('data-episode'),'204');
  assert.equal(await page.locator('#watch-download').getAttribute('href'),'/api/library/media/204/download');
  const saved=page.waitForEvent('download');await page.locator('#watch-download').click();assert.equal(await (await saved).failure(),null);
  await page.reload();await page.waitForSelector('[data-episode="204"][aria-current=true]');
  assert.equal(await page.locator('#episode-current').innerText(),'正在播放 · 第 1–5 集 · 合集');
  await page.locator('[data-episode-group="61"]').click();
  ids=await visibleIds();assert.equal(ids[ids.indexOf(66)+1],201);assert.equal(ids[ids.indexOf(201)+1],67);
  // Browsing a segment must not change the current video or selected download.
  assert.equal(new URL(page.url()).searchParams.get('episode_id'),'204');
  await page.locator('[data-episode="201"]').click();
  assert.equal(await page.locator('[data-episode-group="61"]').getAttribute('aria-pressed'),'true');
  await page.reload();await page.waitForSelector('[data-episode="201"][aria-current=true]');
  await page.locator('#watch-episode').selectOption('32',{force:true});
  assert.equal(await page.locator('[data-episode-group="31"]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-episode-group="unknown"]').click();assert.deepEqual(await visibleIds(),[205,206]);
  assert.equal(await page.locator('#episode-list img').count(),0);
  // Missing singles use the position before the range; later additions move the collection.
  const originalRows=[...rows];
  for(const available of [[],[26],[27],[26,27]]){
   rows=originalRows.filter(row=>![26,27,65,66].includes(row.episode_number)||available.includes(row.episode_number));
   await ready('/watch?catalog_id=4&series_id=129&episode_id=1');
   ids=await visibleIds();const anchor=available.length?Math.max(...available):25;
   assert.equal(ids[ids.indexOf(anchor)+1],202,`26–27 after ${anchor}`);
   const downloadLabels=await page.locator('.watch-file-tile').evaluateAll(nodes=>nodes.map(node=>node.title));
   assert.match(downloadLabels[downloadLabels.findIndex(text=>text.startsWith(`第 ${anchor} 集`))+1],/^第 26–27 集/);
   await page.locator('[data-episode-group="61"]').click();
   ids=await visibleIds();assert.equal(ids[ids.indexOf(64)+1],201);assert.equal(ids[ids.indexOf(201)+1],67);
  }
  // Opening collections with no singles stay at the beginning of the first segment.
  rows=originalRows.filter(row=>!row.episode_number||row.episode_number>13);
  await ready('/watch?catalog_id=4&series_id=129&episode_id=204');
  ids=await visibleIds();assert.deepEqual(ids.slice(0,3),[203,204,14]);
  assert.equal(await page.locator('[data-episode-group="1"]').getAttribute('aria-pressed'),'true');
  rows=originalRows;
  // Download selection is independent of playback and survives segment changes.
  await ready('/watch?catalog_id=4&series_id=129&episode_id=1');
  const playerSource=await page.locator('#watch-video').getAttribute('src'),playerUrl=page.url();
  assert.equal(await page.locator('#watch-files-download').isDisabled(),true);
  const before=downloads.length;
  await page.locator('[data-download-select="1"]').check();
  assert.equal(await page.locator('.watch-file-tile.is-selected').count(),1);
  await page.locator('[data-file-group="31"]').click();
  await page.locator('[data-download-select="32"]').check();
  assert.equal(await page.locator('#watch-files-download').innerText(),'下载所选（2）');
  await page.locator('[data-file-group="1"]').click();
  assert.equal(await page.locator('[data-download-select="1"]').isChecked(),true);
  assert.equal(await page.locator('#watch-video').getAttribute('src'),playerSource);
  assert.equal(page.url(),playerUrl);
  assert.equal(downloads.length,before,'selection must not start downloads');
  await page.locator('#watch-files-download').click();
  assert.equal(await page.locator('#watch-files-download').isDisabled(),true);
  await page.waitForFunction(()=>document.querySelector('#watch-files-stop').hidden);
  assert.equal(downloads.length,before+2);
  const batch=downloads.slice(before);
  assert.deepEqual(batch.map(download=>download.suggestedFilename()),['synthetic-1.mp4','synthetic-32.mp4']);
  for(const download of batch){
   assert.equal(await download.failure(),null);
   assert.equal(crypto.createHash('sha256').update(fs.readFileSync(await download.path())).digest('hex'),crypto.createHash('sha256').update(clip).digest('hex'));
  }
  assert.match(await page.locator('#watch-batch-message').innerText(),/已发起 2\/2/);
  assert.equal(page.url(),playerUrl);
  await page.locator('#watch-files-clear').click();
  assert.equal(await page.locator('#watch-files-download').isDisabled(),true);
  await page.locator('#watch-files-all').click();
  const groupCount=originalRows.filter(row=>row.episode_number>=1&&row.episode_number<=30).length+3;
  assert.equal(await page.locator('input[data-download-select]:checked').count(),groupCount);
  assert.equal(await page.locator('#watch-files-all').isDisabled(),true);
  await page.locator('[data-file-group="unknown"]').click();
  assert.equal(await page.locator('#watch-file-rows img').count(),0);
  await page.locator('#watch-files-all').click();
  assert.equal(await page.locator('input[data-download-select]:checked').count(),groupCount+2);
  await page.locator('#watch-files-download').click();
  await page.locator('#watch-files-stop').click();
  assert.match(await page.locator('#watch-batch-message').innerText(),/已停止继续发起/);
  const stoppedCount=downloads.length;await page.waitForTimeout(1100);assert.equal(downloads.length,stoppedCount);
  await page.locator('#watch-files-clear').click();
  await page.locator('[data-file-group="1"]').click();
  const single=page.waitForEvent('download');await page.locator('[data-save]').first().click();assert.equal(await (await single).failure(),null);
  // Leaving the page cancels future launches; a new visit starts with no selection.
  await page.locator('#watch-files-all').click();await page.locator('#watch-files-download').click();
  await page.goto(origin+'/watch?catalog_id=4&series_id=129&episode_id=1');
  await page.waitForSelector('[data-download-select="1"]');
  const leftCount=downloads.length;await page.waitForTimeout(1100);assert.equal(downloads.length,leftCount);
  assert.equal(await page.locator('input[data-download-select]:checked').count(),0);
  // Administrator-only downloadable and unavailable files retain their permissions.
  authenticated=true;adminRows=[...originalRows.slice(-3),{id:302,episode_number:2,title:'管理员待抽检',download_url:'/api/downloads/media/302/download'},{id:303,episode_number:3,title:'未准备好'}];
  await ready('/watch?ranking_id=9&series_id=129&episode_id=1');
  await page.waitForSelector('[data-download-select="303"]');
  assert.equal(await page.locator('[data-download-select="303"]').isDisabled(),true);
  assert.equal(await page.locator('[data-download-select="303"]').locator('..').locator('..').locator('[data-save]').count(),0);
  await page.locator('#watch-files-all').click();
  assert.equal(await page.locator('input[data-download-select]:checked').count(),2);
  const adminBatchStart=downloads.length;await page.locator('#watch-files-download').click();
  await page.waitForFunction(()=>document.querySelector('#watch-files-stop').hidden);
  assert.ok(downloads.slice(adminBatchStart).some(download=>new URL(download.url()).pathname==='/api/downloads/media/302/download'));
  authenticated=false;adminRows=null;
  // Subsequent additions and a different work use the same rendering and automatic grouping.
  rows.push({id:121,episode_number:121,title:'新作品第121集',url:'/api/library/media/121',download_url:'/api/library/media/121/download'});
  await ready('/watch?ranking_id=9&series_id=129&episode_id=121');
  assert.equal(await page.locator('[data-episode-group="121"]').getAttribute('aria-pressed'),'true');
  assert.ok(await page.locator('[data-episode-group="121"]').evaluate(el=>{const box=el.getBoundingClientRect(),parent=el.parentElement.getBoundingClientRect();return box.left>=parent.left-1&&box.right<=parent.right+1;}));
  assert.deepEqual(await visibleIds(),[121]);
  manage=true;await ready('/downloads/watch?ranking_id=9&series_id=129&episode_id=32');
  assert.equal(await page.locator('[data-episode="32"]').innerText(),'32');
  assert.equal(await page.locator('#watch-download').count(),0);
  manage=false;publicSite=true;await ready('/watch?catalog_id=4&series_id=129&episode_id=1');
  assert.equal(await page.locator('[data-episode="1"]').getAttribute('aria-current'),'true');
  const publicBatchStart=downloads.length,publicRequestStart=requests.length;
  await page.locator('[data-download-select="1"]').check();await page.locator('[data-download-select="2"]').check();
  await page.locator('#watch-files-download').click();await page.waitForFunction(()=>document.querySelector('#watch-files-stop').hidden);
  assert.equal(downloads.length,publicBatchStart+2);
  assert.ok(downloads.slice(publicBatchStart).every(download=>new URL(download.url()).pathname.startsWith('/api/library/media/')));
  assert.equal(requests.slice(publicRequestStart).some(request=>request.path.startsWith('/api/playback/')||request.path==='/api/auth/session'),false);
  await page.locator('#watch-files-clear').click();
  for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`overflow at ${width}`);
   assert.equal(await page.locator('#episode-list').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),5);
   assert.equal(await page.locator('#watch-file-rows').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),5);
   assert.ok(await page.locator('.watch-file-tile:visible').evaluateAll(nodes=>nodes.every(node=>node.scrollWidth<=node.clientWidth+1)),`download tile overflow at ${width}`);
   assert.ok(await page.locator('.episode-choice:visible').evaluateAll(nodes=>nodes.every(node=>node.scrollWidth<=node.clientWidth+1)),`tile overflow at ${width}`);
   await page.screenshot({path:path.join(out,`player-${width}.png`),fullPage:true});
   if(width===390){
    await page.locator('[data-download-select="1"]').check();await page.locator('[data-download-select="2"]').check();
    await page.locator('#watch-files').screenshot({path:path.join(out,'download-picker-mobile.png')});
    await page.locator('#watch-files-clear').click();
   }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('.episode-picker').screenshot({path:path.join(out,'picker-preview.png')});
  empty=true;await page.goto(origin+'/watch?catalog_id=4&series_id=129');await page.waitForSelector('#episode-empty:not([hidden])');
  assert.equal(await page.locator('.episode-choice').count(),0);
  assert.equal(await page.locator('#watch-files-download').isDisabled(),true);
  assert.equal(await page.locator('#watch-files-all').isDisabled(),true);
  assert.match(await page.locator('#watch-file-rows').innerText(),/暂无可下载/);
  assert.deepEqual(errors,[]);assert.ok(requests.every(row=>row.method==='GET'));
  const result={passed:true,下载五格网格:true,跨分段多选:true,全选与清空:true,分别保存且摘要一致:true,批量停止与离页取消:true,未就绪不可选:true,管理员下载权限沿用:true,合集跟随范围内已有最后单集:true,缺失单集合集排在范围之前:true,仅有部分单集:true,新增单集自动重排:true,起始合集无单集:true,所有作品共用:true,后续新增集数:true,分段切换:true,切集与下载:true,刷新保留:true,管理预览:true,独立公开模式:true,空列表:true,widths:[1440,768,390,320],errors};
  fs.writeFileSync(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});throw error;}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
