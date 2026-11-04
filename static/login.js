"use strict";
document.getElementById("login-form").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.target,button=form.querySelector("button"),message=document.getElementById("login-message");button.disabled=true;
  try {
    const response=await fetch("/api/auth/session",{cache:"no-store"}),session=await response.json();
    if(!response.ok)throw new Error(session.detail);
    if(!session.authenticated){
      const result=await fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":session.csrf_token},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
      const body=await result.json();if(!result.ok)throw new Error(body.detail);
    }
    const target=new URLSearchParams(location.search).get("next");
    const path=target?.split(/[?#]/)[0];
    const allowed=path&&(["/watch","/downloads","/downloads/watch","/workbench","/publish","/control"].includes(path)||path.startsWith("/control/"));
    location.href=allowed&&!target.includes("\\")&&!/[\x00-\x1f]/.test(target)?target:"/control";
  }catch(error){message.textContent=error.message||"登录暂不可用，请稍后重试";}finally{button.disabled=false;}
});
