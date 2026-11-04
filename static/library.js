"use strict";
const esc = value => String(value ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const query = document.getElementById("drama-search");
let area = document.body.dataset.active || "all";
const publicSite = document.body.dataset.public === "true";
let rankingRequest = 0, rankingController;
const hongguoList = document.getElementById("drama-list");
const hongguoCards = Array.from(hongguoList.querySelectorAll(".hongguo-card"), card => ({
  card, title: card.dataset.title, category: card.dataset.category,
  tags: JSON.parse(card.dataset.tags), score: Number(card.dataset.score),
  collectedAt: card.dataset.collectedAt, id: Number(card.dataset.id)
}));
const categoryFilter = document.getElementById("category-filter");
const tagFilter = document.getElementById("tag-filter");
const scoreFilter = document.getElementById("score-filter");
const hongguoSort = document.getElementById("hongguo-sort");
const titleOrder = new Intl.Collator("zh-Hans-CN-u-co-pinyin", {numeric:true, sensitivity:"base"});
function sortHongguo() {
  const mode = hongguoSort.value;
  const recent = (a, b) => b.collectedAt.localeCompare(a.collectedAt);
  hongguoCards.sort((a, b) => {
    if(mode === "title") return titleOrder.compare(a.title, b.title) || a.id - b.id;
    const score = mode === "score-asc" ? a.score - b.score : b.score - a.score;
    return (mode === "recent" ? recent(a, b) || score : score || recent(a, b)) || a.id - b.id;
  });
  const fragment = document.createDocumentFragment();
  hongguoCards.forEach(({card}) => fragment.appendChild(card));
  hongguoList.appendChild(fragment);
  document.getElementById("hongguo-recent-note").hidden = mode !== "recent";
}
async function api(url, signal) {
  const controller = new AbortController(), cancel = () => controller.abort();
  let timedOut = false;
  if(signal?.aborted) cancel();
  signal?.addEventListener("abort", cancel, {once:true});
  const timeout = setTimeout(() => { timedOut = true; cancel(); }, 15000);
  try {
    const response = await fetch(url, {signal:controller.signal});
    if(!response.ok) throw new Error("资料读取失败，请刷新重试");
    return await response.json();
  } catch(error) {
    if(timedOut) throw new Error("网络响应较慢，请重新选择榜单或刷新重试。");
    if(error instanceof TypeError) throw new Error("暂时无法加载榜单，请检查网络后重试。");
    throw error;
  } finally { clearTimeout(timeout); signal?.removeEventListener("abort", cancel); }
}
function filter() {
  document.querySelectorAll("[data-source]").forEach(section=>section.hidden = area !== "all" && section.dataset.source !== area);
  document.querySelectorAll("[data-area]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.area === area)));
  const value = query.value.trim().toLocaleLowerCase();
  document.querySelectorAll("#douyin-cards .drama-card, #manual-cards .drama-card").forEach(card=>{card.hidden = !card.dataset.title.toLocaleLowerCase().includes(value);});
  const manualEmpty=document.getElementById('manual-empty');
  if(manualEmpty){manualEmpty.hidden=!!document.querySelector('#manual-cards .drama-card:not([hidden])');manualEmpty.textContent='没有找到可播放的指定收录作品。';}
  let count = 0;
  hongguoCards.forEach(row => {
    const matchesScore = !scoreFilter.value || (scoreFilter.value === "below7" ? row.score < 7 : row.score >= Number(scoreFilter.value));
    row.card.hidden = !(row.title.toLocaleLowerCase().includes(value) &&
      (!categoryFilter.value || row.category === categoryFilter.value) &&
      (!tagFilter.value || row.tags.includes(tagFilter.value)) && matchesScore);
    if(!row.card.hidden) count++;
  });
  document.getElementById("hongguo-count").textContent = `共找到 ${count} 部`;
  document.getElementById("no-results").hidden = count > 0;
  const empty = document.getElementById("douyin-empty");
  if(document.querySelector("#douyin-cards .drama-card")) { empty.hidden = !!document.querySelector("#douyin-cards .drama-card:not([hidden])"); empty.textContent = "没有找到匹配的抖音 Top3短剧。"; }
}
function images() { document.querySelectorAll(".cover img").forEach(img=>{img.addEventListener("error",()=>img.classList.add("broken"),{once:true}); if(img.complete && !img.naturalWidth) img.classList.add("broken");}); }
async function loadRanking() {
  const id = document.getElementById("library-ranking").value, request = ++rankingRequest;
  rankingController?.abort();
  rankingController = new AbortController();
  const {ranking,catalogs} = await api(`/api/library/rankings/${id}`, rankingController.signal);
  if(request !== rankingRequest || id !== document.getElementById("library-ranking").value) return;
  const url = new URL(location); url.searchParams.set("ranking_id", id); history.replaceState(null, "", url);
  document.getElementById("douyin-context").textContent = `${ranking.run_name} · V${ranking.version} · 截止 ${ranking.cutoff_at_beijing}`;
  document.getElementById("douyin-notice").textContent = `${ranking.disclaimer} ${ranking.shortfall || ""}`;
  document.getElementById("douyin-cards").innerHTML = ranking.items.filter(row=>row.selected).map(row=>{
    const catalog = catalogs.find(c=>c.series_id === row.series_id), summary = catalog.summary;
    const url = `/watch?ranking_id=${id}&series_id=${row.series_id}&download=1`;
    const playable = catalog.episodes?.length > 0;
    const actions = `${playable ? `<a class="button" data-watch="${row.series_id}" href="/watch?ranking_id=${id}&series_id=${row.series_id}">▶ 播放${catalog.episodes.length>1?" / 选集":""}</a>` : ""}${playable || !publicSite ? `<a class="button subtle" data-download="${row.series_id}" href="${url}">${playable?"下载":"查看作品"}</a>` : ""}`;
    return `<article id="series-${row.series_id}" class="drama-card" data-title="${esc(row.series_title)} ${esc(row.author_name)}">${playable ? `<a class="cover-link" href="/watch?ranking_id=${id}&series_id=${row.series_id}" aria-label="播放 ${esc(row.series_title)}">` : ""}<div class="cover">${playable ? '<span class="cover-play" aria-hidden="true">▶</span>' : ""}<div class="poster-title">${esc(row.series_title)}</div>${catalog.cover_url ? `<img loading="lazy" decoding="async" src="${esc(catalog.cover_url)}" alt="${esc(row.series_title)}封面">` : ""}<span class="cover-badge">抖音 Top3短剧 · ${esc(row.ai_type || "AI 短剧")}</span><span class="score rank">TOP ${row.position}</span></div>${playable ? "</a>" : ""}<div class="card-body"><h3>${esc(row.series_title)}</h3><p class="muted">原作者 · ${esc(row.author_name)}</p><p class="card-summary">${esc(row.title_note || row.ai_basis || row.title)}</p><div class="tags"><span>#${esc(row.ai_type || "AI短剧")}</span><span>${ranking.scoring_mode === "official" ? "官方名次" : ranking.scoring_mode === "likes" ? "公开点赞" : "公开互动加权"} ${esc(row.score ?? row.position)}</span></div><p class="download-summary">${playable ? `本站可播放 ${catalog.episodes.length} 集` : "本站暂未收录视频"}<br>${esc(summary.full_series)}</p><div class="card-actions">${actions}</div><details class="card-information"><summary>作品信息</summary><p>榜单 V${ranking.version} · 名次固定</p><a href="${esc(row.original_url)}" target="_blank" rel="noopener noreferrer">观看抖音原视频 ↗</a><br><a href="/rankings/${id}/report" target="_blank" rel="noopener">查看榜单报告 ↗</a></details></div></article>`;
  }).join("");
  const empty = document.getElementById("douyin-empty"); empty.hidden = !!catalogs.length; empty.textContent = "本版本尚无合格入选作品，请查看其他榜单版本。";
  images(); filter();
}
document.querySelectorAll("[data-area]").forEach(button=>button.addEventListener("click",()=>{area=button.dataset.area;filter();}));
query.addEventListener("input",filter);
[categoryFilter, tagFilter, scoreFilter].forEach(control => control.addEventListener("change", filter));
hongguoSort.addEventListener("change", () => {sortHongguo(); filter();});
document.getElementById("hongguo-reset").addEventListener("click", () => {
  categoryFilter.value = tagFilter.value = scoreFilter.value = query.value = "";
  hongguoSort.value = "score-desc";
  sortHongguo(); filter();
});
sortHongguo();
document.getElementById("library-ranking").addEventListener("change",()=>loadRanking().catch(showError));
function showError(error) { if(error.name === "AbortError") return; const empty = document.getElementById("douyin-empty"); empty.hidden=false; empty.textContent=error.message; }
(async()=>{filter();images();if(document.body.dataset.rankingReady === "true") return;const rankings=await api("/api/library/rankings"); const select=document.getElementById("library-ranking"); select.innerHTML=rankings.length?rankings.map(row=>`<option value="${row.id}">${esc(row.name)} · V${row.version} · 榜单 ${row.id}</option>`).join(""):'<option value="">尚未生成榜单</option>'; const requested=new URL(location).searchParams.get("ranking_id");if(rankings.some(row=>String(row.id)===requested))select.value=requested;if(rankings.length) await loadRanking(); else {document.getElementById("douyin-context").textContent="暂时没有可展示的榜单。";showError(new Error("暂时没有可展示的榜单。"));}})().catch(showError);

// Close the phone management menu after choosing a destination or tapping outside.
document.addEventListener("click", event => {const menu=document.querySelector(".mobile-admin[open]");if(menu && (!menu.contains(event.target) || event.target.closest("a"))) menu.open=false;});
