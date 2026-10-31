"use strict";
const $ = (selector) => document.querySelector(selector);
const state = { runs: [], runId: null, rows: [], ranking: null, searchJob: null, searchTimer: null };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const statusName = {pending:"待核验", approved:"已通过", excluded:"已排除"};
const badge = (status) => `<span class="badge ${esc(status)}">${esc(statusName[status] || status)}</span>`;
const formObject = (form) => Object.fromEntries(new FormData(form));
function localNow() { return new Date(Date.now() + 8 * 3600000).toISOString().slice(0,19); }
function displayTime(value) { return value ? new Date(value).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}) + " 北京时间" : "未记录"; }
function notify(message, error = false) {
  const node = $("#message"); node.textContent = message; node.classList.toggle("error", error); node.hidden = false;
  const dialog = document.querySelector("dialog[open]");
  if(error && dialog) {
    let alert = dialog.querySelector(".dialog-message");
    if(!alert) { alert = document.createElement("p"); alert.className = "notice dialog-message"; alert.setAttribute("role","alert"); dialog.querySelector(".section-heading").after(alert); }
    alert.textContent = message; alert.scrollIntoView({block:"nearest"});
  }
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail || result));
  return result;
}
function task(handler) { return async (event) => { event?.preventDefault(); const button = event?.submitter || (event?.currentTarget?.tagName === "BUTTON" ? event.currentTarget : null); if(button) button.disabled = true; try { await handler(event); } catch(error) { notify(error.message, true); } finally { if(button) button.disabled = false; } }; }
function run() { return state.runs.find((item) => item.id === state.runId); }
async function loadRuns(selected) {
  state.runs = await api("/api/runs");
  $("#run-select").innerHTML = state.runs.length ? state.runs.map(item => `<option value="${item.id}">${esc(item.name)} · ${item.mode === "sample" ? "样本榜" : "官方榜"}</option>`).join("") : '<option value="">尚无批次，请先创建</option>';
  state.runId = selected || state.runs[0]?.id || null;
  $("#run-select").value = state.runId || "";
  $("#run-form").hidden = !!state.runId;
  await loadWorkspace();
}
async function loadWorkspace() {
  clearTimeout(state.searchTimer); state.searchJob = null;
  $("#workspace").hidden = !state.runId;
  if(!state.runId) return;
  const current = run();
  $("#run-info").textContent = `截止 ${current.cutoff_at_beijing} · ${current.mode === "sample" ? `窗口 ${current.window_hours} 小时` : "周期以核验的官方来源为准"} · 计划关键词：${current.keywords.join("、")}`;
  $("#official-section").hidden = current.mode !== "official";
  $("#ranking-intro").textContent = current.mode === "official" ? "核对官方来源及作品后，保留原官方名次。统计周期以来源为准，原第 4 名及以后不补入前三。" : "核验通过后，按窗口内最高分正片选出每部剧的代表集，再比较作品。指标不齐时，全体统一按点赞数排名。";
  $(".formula").hidden = current.mode === "official";
  $("#official-status").textContent = current.official_metadata.verified ? "已人工核验" : "待核验";
  $("#official-form").reset();
  for (const [key, value] of Object.entries(current.official_metadata)) { const field = $("#official-form").elements.namedItem(key); if(field) { if(field.type === "checkbox") field.checked = !!value; else if(field.type === "datetime-local") field.value = new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(0,16); else field.value = value; } }
  await loadCandidates();
  await loadRankings();
  await loadSearch();
}
async function loadCandidates() {
  state.rows = await api(`/api/runs/${state.runId}/candidates`);
  $("#candidate-count").textContent = state.rows.length;
  const titles = new Map();
  for(const row of state.rows) { if(!row.series_id) continue; if(!titles.has(row.series_title)) titles.set(row.series_title,new Set()); titles.get(row.series_title).add(row.series_id); }
  const similar = [...titles].filter(([, ids])=>ids.size > 1).map(([title])=>title);
  $("#similar-titles").hidden = !similar.length;
  $("#similar-titles").textContent = `发现同名作品：${similar.join("、")}。请核对作者及归属；同名不会自动归并。`;
  renderCandidates();
}
function renderCandidates() {
  const query = $("#candidate-search").value.trim().toLocaleLowerCase();
  const selected = $("#status-filter").value;
  const rows = state.rows.filter(row => {
    const overall = row.series_status === "excluded" || row.episode_status === "excluded" ? "excluded" : row.series_status === "approved" && row.episode_status === "approved" ? "approved" : "pending";
    return (!selected || overall === selected) && `${row.series_title} ${row.author_name} ${row.video_id}`.toLocaleLowerCase().includes(query);
  });
  $("#candidate-empty").hidden = !!rows.length;
  $("#candidate-empty").textContent = state.rows.length ? "没有匹配的候选。" : "还没有候选。导入链接或 CSV，开始核验。";
  $("#candidate-rows").innerHTML = rows.map(row => `<tr><td><strong>${esc(row.series_title)}</strong><p>${esc(row.author_name)}</p><small>账号 ${esc(row.author_account_id || "待补全")} · 作品 ${row.series_id || "待补全"}</small><small>${esc(row.ai_type || "AI 类型待核验")}</small><small>${esc(row.title_note || "")}</small></td><td>${esc(row.title || "详情待补全")}<small>视频 ${esc(row.video_id || "待补全")} · ${row.episode_number ? `第 ${row.episode_number} 集` : "集号未知"}</small><small>${esc(row.published_at_beijing)}</small><small>${esc(row.content_kind === "compilation" ? "多集合集 · 不参与单集排名" : row.content_kind === "single" ? "单集或独立短片" : "视频形式未单独标注")}</small><small>${esc(row.content_note || "")}</small><a class="source-link" href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">查看原链接 ↗</a><details><summary>检索与核验记录</summary>${row.sources.map(source=>`<p>${esc(source.keyword || "关键词未记录")} / ${esc(source.sort_order || "排序未记录")} / ${esc(source.position || "页码未记录")}</p><small>${esc(displayTime(source.searched_at))} · <a href="${esc(source.source_url)}" target="_blank" rel="noopener">检索入口 ↗</a></small>`).join("")}${[...row.series_reviews,...row.episode_reviews].map(record=>`<p>${esc(statusName[record.status] || ({merged:"已归并",reassigned:"已纠正归属",metadata_updated:"资料已整理"}[record.status]) || record.status)} · ${esc(record.reason)} <a href="${esc(record.evidence_url)}" target="_blank" rel="noopener">证据 ↗</a></p>`).join("")}</details></td><td>点赞 ${row.likes ?? "未知"} · 评论 ${row.comments ?? "未知"}<br>收藏 ${row.favorites ?? "未知"} · 分享 ${row.shares ?? "未知"}<small>${esc(row.sampled_at_beijing)}</small><small>${esc(row.missing_reason)}</small></td><td><p>作品 ${badge(row.series_status)}</p><p>正片 ${badge(row.episode_status)}</p></td><td>${row.series_id ? `<button class="secondary" data-review="series" data-id="${row.series_id}">核验作品</button><button class="secondary" data-metadata="series" data-id="${row.series_id}">整理作品</button>` : "请导入 CSV 补全"}${row.episode_id ? `<button class="secondary" data-review="episode" data-id="${row.episode_id}">核验正片</button><button class="secondary" data-assign="${row.episode_id}">纠正归属</button><button class="secondary" data-metadata="episode" data-id="${row.episode_id}">整理视频</button>` : ""}</td></tr>`).join("");
}
function showImport(result) {
  $("#import-result").hidden = false;
  $("#import-result").textContent = `已导入 ${result.imported} 条。${result.message || ""}${result.errors.length ? "\n" + result.errors.map(error=>`第 ${error.line} 行：${error.message}`).join("\n") : "\n所有非空行均已通过校验。"}`;
}
function openReview(kind, id) {
  const row = state.rows.find(item=>kind === "series" ? item.series_id === id : item.episode_id === id);
  if(!row) return;
  const form = $("#review-form"); form.reset();
  form.querySelector(".dialog-message")?.remove();
  form.elements.target_type.value = kind; form.elements.target_id.value = id;
  form.elements.status.value = (kind === "series" ? row.series_status : row.episode_status) === "excluded" ? "excluded" : "approved";
  $("#review-title").textContent = kind === "series" ? `作品核验 · ${row.series_title}` : `正片核验 · ${row.video_id}`;
  $("#ai-fields").hidden = kind !== "series";
  form.elements.ai_type.value = row.ai_type; form.elements.ai_basis.value = row.ai_basis;
  form.elements.evidence_url.value = row.original_url;
  const checks = kind === "series" ? [["story","确认有剧情，属于短剧"],["ai","确认有 AI 制作证据"],["original_author","确认该账号为原作者"]] : [["main_episode","确认是正片，排除教程、广告与预告"],["ownership","确认该集属于当前作品及原作者"],["public","确认当前公开可访问"],["not_duplicate","确认不是搬运或重复上传"]];
  $("#review-checks").innerHTML = checks.map(([key,label])=>`<label class="check"><input type="checkbox" name="${key}">${label}</label>`).join("");
  const history = kind === "series" ? row.series_reviews : row.episode_reviews;
  $("#review-history").textContent = history.length ? `上次核验：${history.at(-1).reason}` : "首次核验。所有结论与证据会保留。";
  $("#review-dialog").showModal();
}
function openMetadata(kind,id) {
  const row = state.rows.find(item=>kind === "series" ? item.series_id === id : item.episode_id === id);
  if(!row) return;
  const form = $("#metadata-form"); form.reset(); form.querySelector(".dialog-message")?.remove();
  form.elements.kind.value = kind; form.elements.target_id.value = id;
  $("#metadata-title").textContent = kind === "series" ? "整理作品名称与类型" : "整理集号与视频形式";
  $("#metadata-series").hidden = kind !== "series"; $("#metadata-episode").hidden = kind !== "episode";
  for(const key of ["title_note","ai_type","ai_basis","episode_number","content_kind","content_note"]) form.elements[key].value = row[key] ?? "";
  form.elements.title.value = row.series_title; form.elements.evidence_url.value = row.original_url;
  $("#metadata-dialog").showModal();
}
$("#metadata-form").addEventListener("submit",task(async()=>{
  const form = $("#metadata-form"), raw = formObject(form);
  const data = raw.kind === "series" ? {title:raw.title,title_note:raw.title_note,ai_type:raw.ai_type,ai_basis:raw.ai_basis} : {episode_number:raw.episode_number ? Number(raw.episode_number) : null,content_kind:raw.content_kind,content_note:raw.content_note};
  const result = await api(`/api/${raw.kind === "series" ? "series" : "episodes"}/${raw.target_id}/metadata`,{...data,reason:raw.reason,evidence_url:raw.evidence_url});
  $("#metadata-dialog").close(); await loadCandidates(); notify(result.message);
}));
function seriesOptions() { return [...new Map(state.rows.filter(row=>row.series_id).map(row=>[row.series_id,row])).values()].map(row=>`<option value="${row.series_id}">${esc(row.series_title)} · ${esc(row.author_name)} · ID ${row.series_id}</option>`).join(""); }
async function loadRankings(selected) {
  const versions = await api(`/api/runs/${state.runId}/rankings`);
  $("#ranking-select").innerHTML = versions.length ? versions.map(item=>`<option value="${item.id}">V${item.version} · ${esc(item.created_at_beijing)} · ${esc({weighted:"互动加权",likes:"公开点赞",official:"官方名次"}[item.scoring_mode])}</option>`).join("") : '<option value="">尚未生成榜单</option>';
  if(selected) $("#ranking-select").value = selected;
  if(versions.length) await loadRanking(Number($("#ranking-select").value));
  else { state.ranking = null; $("#report-link").hidden = true; $("#export-link").hidden = true; $("#ranking-content").className = "empty"; $("#ranking-content").textContent = "补全并核验候选后，生成第一份 Top3。合格作品不足三部时，会展示实际数量和原因。"; }
}
async function loadRanking(id) {
  const ranking = await api(`/api/rankings/${id}`); state.ranking = ranking;
  $("#report-link").hidden = false; $("#report-link").href = `/rankings/${id}/report`;
  $("#export-link").hidden = false; $("#export-link").href = `/api/rankings/${id}/export`;
  $("#ranking-content").className = "";
  $("#ranking-content").innerHTML = `<h3>${esc(ranking.title)}</h3><p class="muted">${esc(ranking.disclaimer)}</p><p class="muted">${esc(ranking.explanation)}</p>${ranking.review_scope ? `<p class="notice">${esc(ranking.review_scope.assessment)}<br>通过 ${ranking.review_scope.approved} 条 · 待核验 ${ranking.review_scope.pending} 条 · 排除 ${ranking.review_scope.excluded} 条 · 多集合集 ${ranking.review_scope.compilations} 条</p>` : ""}${ranking.representative_policy ? `<p class="muted">${esc(ranking.representative_policy)}</p>` : ""}${ranking.shortfall ? `<p class="notice">${esc(ranking.shortfall)}</p>` : ""}<div class="ranking-grid">${ranking.items.filter(item=>item.selected).map(item=>`<article class="rank-card"><span class="rank-number">${String(item.position).padStart(2,"0")}</span><h3>${esc(item.series_title)}</h3><p>${esc(item.author_name)} · ${esc(item.ai_type)}</p><p class="score">${item.score ?? "官方名次"}</p><small>${ranking.scoring_mode === "likes" ? "公开点赞数" : ranking.scoring_mode === "weighted" ? "自定义互动分" : "保留来源排名"}</small><p>${esc(item.ai_basis)}</p><small>${esc(item.title_note || "")}</small><a href="${esc(item.original_url)}" target="_blank" rel="noopener">查看代表集 ↗</a><small>视频 ${esc(item.video_id)}<br>${esc(item.sampled_at_beijing)}</small></article>`).join("")}</div><details open><summary>候补与比较范围</summary><p>${ranking.items.filter(item=>!item.selected).map(item=>`第 ${item.position} 名 · ${esc(item.series_title)} · ${esc(item.author_name)} · ${item.score ?? "官方名次"}`).join("<br>") || "暂无候补。"}</p><p class="muted">候选 ${ranking.candidate_count} 条，可排名作品 ${ranking.qualified_series_count} 部。实际检索记录 ${ranking.coverage.search_hit_count} 条。详见报告和 CSV 的来源及排除原因。</p></details>`;
}
$("#run-form").elements.cutoff_at.value = localNow();
$("#links-form").elements.searched_at.value = localNow();
$("#new-run").addEventListener("click",()=>{ $("#run-form").hidden = false; $("#run-form").elements.cutoff_at.value = localNow(); $("#run-form").elements.name.focus(); });
$("#run-form").addEventListener("submit", task(async()=>{ const data = formObject($("#run-form")); data.cutoff_at += "+08:00"; data.keywords = data.keywords.split(/[,，、]/); const result = await api("/api/runs",data); await loadRuns(result.id); notify("批次已创建，截止时间与规则已固定。"); }));
$("#run-select").addEventListener("change",task(async()=>{ state.runId = Number($("#run-select").value); await loadWorkspace(); }));
$("#links-form").addEventListener("submit",task(async()=>{ const data = formObject($("#links-form")); if(data.searched_at) data.searched_at += "+08:00"; showImport(await api(`/api/runs/${state.runId}/import/links`,data)); await loadCandidates(); }));
$("#csv-form").addEventListener("submit",task(async()=>{ const file = $("#csv-form").elements.file.files[0]; if(file.size > 5000000) throw new Error("CSV 文件不能超过 5 MB"); const content = await file.text(); showImport(await api(`/api/runs/${state.runId}/import/csv`,{content})); await loadCandidates(); }));
$("#official-form").addEventListener("submit",task(async()=>{ const data = formObject($("#official-form")); data.confirmed = $("#official-form").elements.confirmed.checked; for(const key of ["period_start","period_end","updated_at"]) data[key] += "+08:00"; await api(`/api/runs/${state.runId}/official-verification`,data); await loadRuns(state.runId); notify("官方来源核验已保存。"); }));
$("#refresh").addEventListener("click",task(loadCandidates));
$("#candidate-search").addEventListener("input",renderCandidates);
$("#status-filter").addEventListener("change",renderCandidates);
$("#candidate-rows").addEventListener("click",(event)=>{ const button = event.target.closest("button"); if(!button) return; if(button.dataset.metadata) openMetadata(button.dataset.metadata,Number(button.dataset.id)); if(button.dataset.review) openReview(button.dataset.review,Number(button.dataset.id)); if(button.dataset.assign) { const form = $("#assign-form"); form.reset(); form.elements.episode_id.value = button.dataset.assign; form.elements.series_id.innerHTML = seriesOptions(); $("#assign-dialog").showModal(); } });
$("#review-form").addEventListener("submit",task(async()=>{ const form = $("#review-form"); const data = formObject(form); data.target_id = Number(data.target_id); data.checks = Object.fromEntries([...form.querySelectorAll('input[type="checkbox"]')].map(input=>[input.name,input.checked])); await api("/api/reviews",data); $("#review-dialog").close(); await loadCandidates(); notify("核验结论已保存。需要更新榜单时，请生成新版本。"); }));
$("#open-merge").addEventListener("click",()=>{ const options = seriesOptions(); $("#merge-form").elements.source_id.innerHTML = options; $("#merge-form").elements.target_id.innerHTML = options; $("#merge-dialog").showModal(); });
$("#merge-form").addEventListener("submit",task(async()=>{ const data = formObject($("#merge-form")); data.source_id = Number(data.source_id); data.target_id = Number(data.target_id); const result = await api("/api/series/merge",data); $("#merge-dialog").close(); await loadCandidates(); notify(result.message); }));
$("#assign-form").addEventListener("submit",task(async()=>{ const data = formObject($("#assign-form")); data.series_id = Number(data.series_id); const result = await api(`/api/episodes/${data.episode_id}/assign`,data); $("#assign-dialog").close(); await loadCandidates(); notify(result.message); }));
$("#generate-ranking").addEventListener("click",task(async()=>{ const result = await api(`/api/runs/${state.runId}/rankings`,{}); await loadRankings(result.id); notify(`榜单 V${result.version} 已保存；旧版本可随时查看和导出。`); }));
$("#ranking-select").addEventListener("change",task(async()=>{ if($("#ranking-select").value) await loadRanking(Number($("#ranking-select").value)); }));
const searchNames = {queued:"等待采集",running:"正在搜索",completed:"采集完成",needs_login:"需要登录",blocked:"需要验证",failed:"采集中断",interrupted:"服务重启后待继续"};
async function loadSource() {
  const source = await api("/api/source/douk/status");
  $("#source-status").textContent = source.available ? "本机采集服务已就绪" : "采集服务未启动";
  $("#session-status").textContent = source.session.configured ? `已保存在本机 · ${displayTime(source.session.saved_at)}` : "尚未配置登录状态。";
}
async function loadSearch() {
  clearTimeout(state.searchTimer);
  const runId = state.runId;
  $("#search-section").hidden = run()?.mode !== "sample";
  if(run()?.mode !== "sample") return;
  const jobs = await api(`/api/runs/${runId}/search-jobs`);
  if(runId !== state.runId) return;
  const old = state.searchJob;
  state.searchJob = jobs[0] || null;
  const job = state.searchJob;
  const active = job && ["queued","running"].includes(job.status);
  $("#start-search").disabled = !!active;
  $("#resume-search").hidden = !job || !["needs_login","blocked","failed","interrupted"].includes(job.status);
  $("#search-progress").textContent = job ? `${searchNames[job.status] || job.status} · ${job.message}\n已完成 ${job.progress.pages_done} 页，导入 ${job.progress.imported} 条窗口内候选记录，跳过 ${job.progress.skipped} 条；请求 ${job.progress.requests} 次。预算最多 ${job.config.budget} 条结果。` : "尚未开始自动搜索。";
  $("#search-history-content").innerHTML = jobs.map(item=>`<p>任务 ${item.id} · ${esc(searchNames[item.status])} · ${esc(displayTime(item.created_at))}</p>${item.progress.history.map(page=>`<p>${esc(page.keyword)} / ${esc({general:"综合",likes:"点赞",latest:"最新"}[page.sort])} / 第 ${page.page} 页 · ${esc(page.status === "ok" ? `返回 ${page.returned}，导入 ${page.imported}，跳过 ${page.skipped}` : searchNames[page.status])}<br><small>${esc(displayTime(page.finished_at))}</small>${(page.reasons || []).map(reason=>`<br><small>第 ${reason.position} 条：${esc(reason.reason)}</small>`).join("")}</p>`).join("")}`).join("");
  if(job?.status === "needs_login") $("#login-settings").open = true;
  if(job && (job.id !== old?.id || job.progress.imported !== old?.progress.imported)) await loadCandidates();
  if(active) state.searchTimer = setTimeout(()=>loadSearch().catch(error=>notify(error.message,true)),2500);
}
$("#search-form").addEventListener("submit",task(async()=>{
  const form = $("#search-form");
  const sorts = [...form.querySelectorAll('input[name="sorts"]:checked')].map(input=>input.value);
  await api(`/api/runs/${state.runId}/search-jobs`,{sorts,pages:Number(form.elements.pages.value)});
  await loadSearch();
}));
$("#resume-search").addEventListener("click",task(async()=>{ await api(`/api/search-jobs/${state.searchJob.id}/resume`,{}); await loadSearch(); }));
$("#session-form").addEventListener("submit",task(async()=>{
  const field = $("#session-form").elements.cookie;
  try { await api("/api/source/douk/session",{cookie:field.value}); notify("登录状态已保存在本机，可点击“从上次位置继续”。"); }
  finally { field.value = ""; }
  await loadSource();
}));
$("#clear-session").addEventListener("click",task(async()=>{ const response = await fetch("/api/source/douk/session",{method:"DELETE"}); if(!response.ok) throw new Error("清除失败，请重试"); await loadSource(); notify("本机保存的登录状态已清除。"); }));
loadSource().catch(error=>notify(error.message,true));
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>document.getElementById(button.dataset.close).close()));
loadRuns().catch(error=>notify(error.message,true));
