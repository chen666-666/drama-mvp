"use strict";
window.DiscoveryPanel={
  render(c){
    if(c.ranking_id==null)return '';
    const d=c.discovery||{},e=v=>String(v??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
    const button=(action,label,disabled=false)=>`<button type="button" class="subtle" data-discovery-action="${action}" data-discovery-catalog="${c.id}" ${disabled?'disabled':''}>${label}</button>`;
    const running=['queued','running'].includes(d.status),counts=d.counts||{};
    return `<div class="discovery-panel"><h3>自动补集</h3><p>${e(d.status_text||d.message)}${d.linked?` · 第${d.round}轮 · 已请求${d.requests}/50次`:''}</p>${d.linked?`<p>${e(d.message)}<br>${e(d.sync_message)}</p><p>发现 ${d.candidates.length} 个视频 · 待辨认 ${(counts.clue||0)+(counts.conflict||0)} · 游客可播放 ${d.playable||0} · 同剧候选 ${counts.matched||0} · 多集合集 ${counts.compilation||0} · 宣传 ${counts.promotional||0}</p><ul>${d.sources.map(s=>`<li>${e(({detail:'身份详情',mix:'合集',author:'作者主页',search:'补漏搜索'})[s.kind])} · ${e(({head:'新增检查',history:'历史继续',identity:'公开映射'})[s.mode])}：已读${s.pages}页 · ${e(({ended:'已读到有效末页',boundary:'本次覆盖边界，历史另存',more:'仍有后续',pending:'尚待执行',superseded:'资料变更后停用'})[s.state]||s.state)}</li>`).join('')}</ul>`:''}<p class="muted">全剧完整性未确认。目录核验、允许准备范围和媒体快审分别确认；新发现不会自动加入已确认范围。</p><div class="actions">${d.linked?button('update','更新剧集',running||!d.enabled)+button('continue','继续查找',running||!d.enabled)+button('pause','暂停整部作品共享查找',!running)+button(d.active?'unlink':'link',d.active?'暂停本榜单同步':'恢复本榜单同步')+button('resume-source','处理登录 / 验证后恢复来源'):button('start','为本榜单启动补集',!d.enabled)}</div>${!d.enabled?'<p class="muted">自动补集开关已关闭；开启前须验证真实来源。</p>':''}${d.context_changed?'<p class="error">作品或作者资料已变化，请重新核对来源。</p>':''}${d.linked?`<details id="discovery-aliases-${c.id}"><summary>重新核对身份与维护剧名别名</summary><form data-discovery-recheck="${c.id}"><label>人工确认的剧名别名（每行一个，最多三个）<textarea name="aliases" rows="3" maxlength="303"></textarea></label><p class="muted">先暂停共享查找；保存后重新读取代表视频公开身份。旧来源记录保留。</p><button>确认来源并重新核对</button></form></details><details id="discovery-clues-${c.id}"><summary>发现依据与待辨认线索 ${d.candidates.length} 条</summary><ul>${d.candidates.map(r=>`<li>${e(r.title)} · ${e(({matched:'同剧待核验',clue:'待辨认',conflict:'身份冲突',compilation:'多集合集',promotional:'宣传内容'})[r.kind])}<br>${e(r.basis)} · <a href="${e(r.url)}" target="_blank" rel="noopener noreferrer">核对公开来源 ↗</a></li>`).join('')}</ul><p>泛称或冲突视频需先人工辨认；明确归属后可使用下方手动链接入口添加，再核验目录。</p></details>`:''}</div>`;
  },
  async send(id,action,aliases){
    const url=action==='resume-source'?'/api/control/discovery/resume-source':`/api/control/discovery/catalogs/${id}`;
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(action==='resume-source'?{}:{action,aliases:aliases||[]})});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||'补集操作未完成');return result;
  }
};
