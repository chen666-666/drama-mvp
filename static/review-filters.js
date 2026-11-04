"use strict";
// Shared by the task evidence cards and the candidate review table.
window.CandidateReviewFilter=class {
 constructor(root,onChange,status='pending'){
  this.root=root;this.value={status,target:'episode'};
  root.addEventListener('click',event=>{
   const button=event.target.closest('button');if(!button||button.disabled)return;
   const previous={...this.value};
   if(button.dataset.evidenceStatus)this.value.status=button.dataset.evidenceStatus;
   else if(button.dataset.evidenceTarget)this.value.target=button.dataset.evidenceTarget;
   else if(button.id==='evidence-filter-clear')this.value.status='all';
   else return;
   if(previous.status!==this.value.status||previous.target!==this.value.target)onChange();
  });
 }
 reset(status='pending'){this.value={status,target:'episode'};}
 matches(seriesStatus,episodeStatus){
  const {status,target}=this.value;
  if(status==='all')return true;
  if(status!=='excluded'&&(seriesStatus==='excluded'||episodeStatus==='excluded'))return false;
  const seriesMatches=seriesStatus===status,episodeMatches=episodeStatus===status;
  if(status==='pending'&&target==='episode')return seriesStatus==='approved'&&episodeMatches;
  return target==='series'?seriesMatches:target==='episode'?episodeMatches:seriesMatches&&episodeMatches;
 }
 update(workCount,rowCount){
  const {status,target}=this.value;
  const description=status==='all'?'全部候选（含已排除）':status==='pending'&&target==='episode'?'作品已通过 · 正片待核验':({series:'作品',episode:'正片',both:'作品、正片均'}[target]+{pending:'待核验',approved:'已核验',excluded:'已排除'}[status]);
  this.root.querySelector('#evidence-filter-summary').textContent=`${description} · ${workCount} 部作品 / ${rowCount} 条候选 · 点赞从高到低，未知置后`;
  this.root.querySelectorAll('[data-evidence-status]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.evidenceStatus===status)));
  this.root.querySelectorAll('[data-evidence-target]').forEach(button=>{
   button.setAttribute('aria-pressed',String(button.dataset.evidenceTarget===target));button.disabled=status==='all';
  });
  this.root.querySelector('#evidence-filter-clear').setAttribute('aria-pressed',String(status==='all'));
 }
};

// Unknown likes stay distinct from a real zero; ties retain the existing order.
window.compareCandidateLikes=(a,b)=>{
 const left=Number.isFinite(a.likes)?a.likes:null,right=Number.isFinite(b.likes)?b.likes:null;
 return left===null?(right===null?0:1):right===null?-1:right-left;
};

window.candidateSummaryText=(rows,scopeLabel='本批全部')=>{
 const carried=rows.filter(r=>r.membership?.source_run_id&&r.membership.source_run_id!==r.run_id).length;
 const states=rows.map(r=>r.membership?.refresh_status||(r.metric_snapshot_id?'refreshed':'pending'));
 const count=s=>states.filter(v=>v===s).length;
 return `${scopeLabel} ${rows.length} 条候选 · 从已有批次补入 ${carried} 条 · 本批其他候选 ${rows.length-carried} 条；互动已取得 ${count('refreshed')} 条，待更新 ${count('pending')} 条，更新失败 ${count('failed')} 条，无法公开访问 ${count('unavailable')} 条，未超过点赞门槛 ${count('below_threshold')} 条。`;
};
window.candidateOriginText=row=>{
 const m=row.membership;
 if(!m)return '';
 return `${m.source_run_id!==row.run_id?`从批次 #${m.source_run_id} 补入`:'本批已有候选'} · ${m.refresh_note||''}`;
};
