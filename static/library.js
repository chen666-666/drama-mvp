"use strict";
const esc = value => String(value ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const query = document.getElementById("drama-search");
let area = document.body.dataset.active || "all";
async function api(url) { const response = await fetch(url); if(!response.ok) throw new Error("资料读取失败，请刷新重试"); return response.json(); }
function filter() {
  document.querySelectorAll("[data-source]").forEach(section=>section.hidden = area !== "all" && section.dataset.source !== area);
  document.querySelectorAll("[data-area]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.area === area)));
  const value = query.value.trim().toLocaleLowerCase();
  const category = document.getElementById("category-filter").value;
  document.querySelectorAll(".drama-card").forEach(card=>{card.hidden = !(card.dataset.title.toLocaleLowerCase().includes(value) && (!card.classList.contains("hongguo-card") || !category || card.dataset.category === category));});
  document.getElementById("no-results").hidden = !!document.querySelector(".hongguo-card:not([hidden])");
  const empty = document.getElementById("douyin-empty");
  if(document.querySelector("#douyin-cards .drama-card")) { empty.hidden = !!document.querySelector("#douyin-cards .drama-card:not([hidden])"); empty.textContent = "没有找到匹配的抖音 Top3 作品。"; }
}
function images() { document.querySelectorAll(".cover img").forEach(img=>{img.addEventListener("error",()=>img.classList.add("broken"),{once:true}); if(img.complete && !img.naturalWidth) img.classList.add("broken");}); }
async function loadRanking() {
  const id = document.getElementById("library-ranking").value;
  const {ranking,catalogs} = await api(`/api/downloads/rankings/${id}`);
  if(id !== document.getElementById("library-ranking").value) return;
  document.getElementById("douyin-context").textContent = `${ranking.run_name} · V${ranking.version} · 截止 ${ranking.cutoff_at_beijing}`;
  document.getElementById("douyin-notice").textContent = `${ranking.disclaimer} ${ranking.shortfall || ""}`;
  document.getElementById("douyin-cards").innerHTML = ranking.items.filter(row=>row.selected).map(row=>{
    const catalog = catalogs.find(c=>c.series_id === row.series_id), summary = catalog.summary;
    const url = `/downloads?ranking_id=${id}#series-${row.series_id}`;
    return `<article class="drama-card" data-title="${esc(row.series_title)} ${esc(row.author_name)}"><a class="cover-link" href="${url}"><div class="cover"><div class="poster-title">${esc(row.series_title)}</div>${catalog.cover_url ? `<img src="${esc(catalog.cover_url)}" alt="${esc(row.series_title)}封面">` : ""}<span class="cover-badge">抖音 · ${esc(row.ai_type || "AI 短剧")}</span><span class="score rank">TOP ${row.position}</span></div></a><div class="card-body"><h3><a href="${url}">${esc(row.series_title)}</a></h3><p class="muted">原作者 · ${esc(row.author_name)}</p><p class="card-summary">${esc(row.title_note || row.ai_basis || row.title)}</p><div class="tags"><span>#${esc(row.ai_type || "AI短剧")}</span><span>${ranking.scoring_mode === "official" ? "官方名次" : ranking.scoring_mode === "likes" ? "公开点赞" : "公开互动加权"} ${esc(row.score ?? row.position)}</span></div><p class="download-summary">已确认公开 ${summary.public} 集 · 校验通过 ${summary.verified} 集<br>${esc(summary.full_series)}</p><div class="card-actions"><a class="button" href="${url}">剧集与下载</a><a class="button subtle" href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">代表集 ↗</a></div><div class="card-footer"><span>榜单 V${ranking.version} · 名次固定</span><a href="/rankings/${id}/report" target="_blank" rel="noopener">榜单报告 ↗</a></div></div></article>`;
  }).join("");
  const empty = document.getElementById("douyin-empty"); empty.hidden = !!catalogs.length; empty.textContent = "本版本尚无合格入选作品，可前往筛选与排名补全核验。";
  images(); filter();
}
document.querySelectorAll("[data-area]").forEach(button=>button.addEventListener("click",()=>{area=button.dataset.area;filter();}));
query.addEventListener("input",filter); document.getElementById("category-filter").addEventListener("change",filter);
document.getElementById("library-ranking").addEventListener("change",()=>loadRanking().catch(showError));
function showError(error) { const empty = document.getElementById("douyin-empty"); empty.hidden=false; empty.textContent=error.message; }
(async()=>{filter();images();const rankings=await api("/api/downloads/rankings"); const select=document.getElementById("library-ranking"); select.innerHTML=rankings.length?rankings.map(row=>`<option value="${row.id}">${esc(row.name)} · V${row.version} · 榜单 ${row.id}</option>`).join(""):'<option value="">尚未生成榜单</option>'; if(rankings.length) await loadRanking(); else {document.getElementById("douyin-context").textContent="先完成候选核验，生成第一份 Top3。";showError(new Error("还没有榜单，前往“筛选与排名”开始。"));}})().catch(showError);
