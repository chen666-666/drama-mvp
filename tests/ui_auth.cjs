/* Synthetic credentials are accepted only by tests/run_ui_server.py on port 8001. */
const assert=require('node:assert/strict');
const credentials={username:'synthetic-admin',password:'synthetic-password'};
async function login(page,base){
  const origin=new URL(base).origin;if(!['8001','8002'].includes(new URL(origin).port))throw new Error('Use isolated port 8001');
  await page.goto(origin+'/login');
  await page.locator('[name=username]').fill(credentials.username);
  await page.locator('[name=password]').fill(credentials.password);
  await page.locator('#login-form button').click();
  await page.waitForURL('**/control');
}
async function createApi(origin){
  const {request}=require(process.env.DRAMA_PLAYWRIGHT_MODULE||'playwright');
  const context=await request.newContext({baseURL:origin,extraHTTPHeaders:{Origin:origin}});
  const initial=await (await context.get('/api/auth/session')).json();
  const result=await context.post('/api/auth/login',{data:credentials,headers:{'X-CSRF-Token':initial.csrf_token}});assert.ok(result.ok(),await result.text());
  const session=await (await context.get('/api/auth/session')).json();
  return async(path,data)=>{const response=data===undefined?await context.get(path):await context.post(path,{data,headers:{'X-CSRF-Token':session.csrf_token}});const result=await response.json();assert.ok(response.ok(),JSON.stringify(result));return result;};
}
module.exports={login,createApi};
