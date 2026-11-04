"use strict";
const $ = selector=>document.querySelector(selector);
const esc = value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let busy = false;
let transferConfig = {configured:false}, transferring=false;
async function request(url, body) {
  const response = body === undefined ? await fetch(url) : await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data = await response.json();
  if(!response.ok) throw new Error(data.detail || "操作失败，请重试");
  return data;
}
function selection() {return {ranking_ids:[Number($("#publish-ranking").value)],all_episodes:$("#all-episodes").checked};}
function show(data) {
  $("#publish-result").innerHTML=`<p>版本 ${esc(data.release_id)} · ${data.video_count} 个合格视频 · 榜单 ${data.ranking_ids.join("、")}</p>${data.gaps.length?`<p>以下缺口不会伪装成可播放视频：</p><ul>${data.gaps.map(row=>`<li>${esc(row.series_title)} · 视频 ${esc(row.video_id)}：${esc(row.reason)}</li>`).join("")}</ul>`:"<p>本次范围内没有发现缺口。</p>"}`;
}
async function run(build) {
  if(busy) return;
  busy = true; $("#preview-release").disabled=true; $("#build-release").disabled=true;
  $("#publish-status").textContent = "正在核对数据库和实际视频文件，请稍候…";
  try {
    const data = await request(`/api/publishing/${build?"build":"preview"}`,selection()); show(data);
    $("#publish-status").textContent=build?`发布包已生成：data/publish/${data.release_id}。尚未上传或上线。` : "预览完成。可生成本机发布包，再交管理员核对。";
    $("#build-release").disabled=false;
    if(build) await history();
  } catch(error) {$("#publish-status").textContent=error.message;}
  finally {busy=false;$("#preview-release").disabled=false;}
}
function transferButtons() {const ready=transferConfig.configured&&!transferring&&$("#transfer-release").value;$("#upload-release").disabled=!ready;$("#activate-release").disabled=!ready||!transferConfig.inventory_confirmed||!$("#rights-confirmed").checked;}
async function history() {const data=await request("/api/publishing/history");$("#publish-history").innerHTML=data.bundles.length?data.bundles.map(row=>`<p>${esc(row.release_id)} · ${row.video_count} 个视频 · ${row.gaps.length} 项缺口</p>`).join(""):"尚未生成发布包。";$("#transfer-release").innerHTML=data.bundles.map(row=>`<option>${esc(row.release_id)}</option>`).join("");transferButtons();}
async function transfer(activate) {
  if(transferring) return;
  transferring=true;transferButtons();$("#transfer-status").textContent=activate?"正在上传、核验并发布。完成前请保留本机工作台运行。":"正在上传和预检，线上版本不会切换。";
  try {
    const job=await request(`/api/publishing/bundles/${$("#transfer-release").value}/transfer`,{activate,rights_confirmed:$("#rights-confirmed").checked});
    localStorage.setItem("drama-publish-operation",job.id); await poll(job.id);
  }catch(error){$("#transfer-status").textContent=error.message;transferring=false;transferButtons();}
}
async function poll(id) {
  const job=await request(`/api/publishing/operations/${id}`);
  if(job.status==="running") {transferring=true;transferButtons();setTimeout(()=>poll(id).catch(error=>{$("#transfer-status").textContent=error.message;transferring=false;transferButtons();}),3000);return;}
  localStorage.removeItem("drama-publish-operation");transferring=false;transferButtons();
  $("#transfer-status").textContent=job.status==="completed"?(job.activate?"发布通过自动检查。请打开网站，用电脑和手机确认画面、声音及选集。":"上传与预检通过，尚未上线。") : job.status==="unknown"?"工作台曾重启，发布结果暂无法确认。请管理员核对服务器当前版本，勿重复激活。":"操作失败，请管理员查看本机发布操作日志；未通过验收的内容不会标记为已发布。";
}
$("#upload-release").addEventListener("click",()=>transfer(false));$("#activate-release").addEventListener("click",()=>transfer(true));
$("#rights-confirmed").addEventListener("change",transferButtons);$("#transfer-release").addEventListener("change",transferButtons);
$("#preview-release").addEventListener("click",()=>run(false));$("#build-release").addEventListener("click",()=>run(true));
for(const selector of ["#publish-ranking","#all-episodes"]) $(selector).addEventListener("change",()=>{$("#build-release").disabled=true;$("#publish-result").textContent="";$("#publish-status").textContent="选择已改变，请重新预览。以前生成的发布包仍保留在下方列表。";});
(async()=>{const rows=await request("/api/downloads/rankings");$("#publish-ranking").innerHTML=rows.map(row=>`<option value="${row.id}">${esc(row.name)} · V${row.version} · 榜单 ${row.id}</option>`).join("");$("#preview-release").disabled=!rows.length;transferConfig=await request("/api/publishing/config");$("#transfer-config").textContent=transferConfig.configured?`目标网站：${transferConfig.domain}${transferConfig.inventory_confirmed?"":" · 首次上线前需管理员确认服务器资料盘点"}`:"尚未配置服务器连接。请先由管理员核对 SSH 指纹和项目路径；当前可以预览、生成本机发布包。";await history();const pending=localStorage.getItem("drama-publish-operation");if(pending)await poll(pending);})().catch(error=>{$("#publish-status").textContent=error.message;});
