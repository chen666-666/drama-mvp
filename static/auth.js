"use strict";
// One shared fetch wrapper supplies CSRF for both management pages.
const siteFetch = window.fetch.bind(window);
let siteSession;
async function getSiteSession() {
  if (!siteSession) siteSession = siteFetch("/api/auth/session", {cache:"no-store"}).then(async response=>{
    const result=await response.json();
    if(!response.ok) throw new Error(result.detail || "网站登录暂不可用");
    return result;
  }).catch(error=>{siteSession=null;throw error;});
  return siteSession;
}
function showSiteLogin() {
  const link=document.getElementById("site-login-link");
  if(link){link.hidden=false;link.href="/login?next="+encodeURIComponent(location.pathname+location.search+location.hash);}
}
window.fetch=async (input,options={})=>{
  const url=new URL(typeof input==="string"?input:input.url,location.href);
  const method=(options.method||"GET").toUpperCase();
  if(url.origin===location.origin && !["GET","HEAD","OPTIONS"].includes(method)) {
    const session=await getSiteSession();
    if(!session.authenticated){showSiteLogin();throw new Error("请重新登录网站后继续操作");}
    const headers=new Headers(options.headers);headers.set("X-CSRF-Token",session.csrf_token);
    options={...options,headers};
  }
  const response=await siteFetch(input,options);
  if(response.status===401 && url.origin===location.origin){siteSession=null;showSiteLogin();}
  return response;
};
document.addEventListener("DOMContentLoaded",()=>{
  document.getElementById("site-logout")?.addEventListener("click",async()=>{
    try {const response=await fetch("/api/auth/logout",{method:"POST"});if(response.ok)location.href="/login";else showSiteLogin();}
    catch {showSiteLogin();}
  });
});
