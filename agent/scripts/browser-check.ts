/** Offline Chrome smoke: verifies chat recovery and artifact polling without invoking a model. */
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { strict as assert } from 'node:assert';
import { createServer } from '../src/server';
import { createChatHandler } from '../src/chat/server';
import { createCopilotHandler } from '../src/runtime';
import { loadConfig } from '../src/config';
const resume = 'a'.repeat(32), artifact = 'b'.repeat(32), workspace = 'browser-fixture';
const cookie = `avicv_session=${'s'.repeat(32)}`;
let polls = 0;
const backend = Bun.serve({ hostname: '127.0.0.1', port: 0, fetch(req) {
  const path = new URL(req.url).pathname;
  if (req.headers.get('cookie') !== cookie) return new Response(null,{status:401});
  if (path === '/auth/session') return Response.json({workspace_id:workspace});
  if (path === `/resumes/${resume}/tailored/${artifact}`) { polls++; return Response.json({id:artifact,status:'ready',download_url:`/resumes/${resume}/tailored/${artifact}/cv.pdf`}); }
  return new Response(null,{status:404});
}});
const port = 20000 + Math.floor(Math.random()*3000), origin=`http://127.0.0.1:${port}`;
const config=loadConfig({PORT:String(port), APP_ORIGIN:origin, API_URL:`http://127.0.0.1:${backend.port}`, MODEL:'anthropic:fixture', ANTHROPIC_API_KEY:'offline-fixture'});
const app=createServer(config,{chat:createChatHandler(config),copilot:(req,session,lease)=>createCopilotHandler(config,session,lease)(req)});
const profile=mkdtempSync(join(tmpdir(),'avicv-agent-browser-')); const chromePort=24000+Math.floor(Math.random()*3000);
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new',`--remote-debugging-port=${chromePort}`,`--user-data-dir=${profile}`,'--no-first-run','--hide-scrollbars','about:blank'],{stdio:'ignore'});
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms)); let ws:WebSocket|undefined;
try {
  let targets:any[]=[]; for(let n=0;n<100;n++){try{targets=await(await fetch(`http://127.0.0.1:${chromePort}/json/list`)).json();if(targets.some(x=>x.type==='page'))break;}catch{} await sleep(100);}
  ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl); await new Promise(r=>ws!.addEventListener('open',r,{once:true}));
  let seq=0;const pending=new Map<number,{resolve:(v:any)=>void,reject:(e:any)=>void}>();const errors:string[]=[];
  ws.addEventListener('message',event=>{const msg=JSON.parse(String(event.data));if(msg.id){const p=pending.get(msg.id);if(msg.error)p?.reject(msg.error);else p?.resolve(msg.result);pending.delete(msg.id);}if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails.exception?.description??'exception');});
  const send=(method:string,params={})=>new Promise<any>((resolve,reject)=>{pending.set(++seq,{resolve,reject});ws!.send(JSON.stringify({id:seq,method,params}));});
  const evaluate=async(expression:string)=>(await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true})).result?.value;
  await send('Page.enable');await send('Runtime.enable');await send('Network.setCookie',{name:'avicv_session',value:'s'.repeat(32),url:origin,httpOnly:true});
  await send('Page.navigate',{url:`${origin}/chat`});
  for(let n=0;n<100;n++){if(await evaluate('document.body.innerText.includes("Your job search")'))break;await sleep(100);}
  const key=`avicv:chat:v1:${workspace}:home`;
  const messages=[
    {id:'u1',role:'user',content:'Fixture CV search'},
    {id:'a1',role:'assistant',content:'Saved chat survives refresh'},
    {id:'a2',role:'assistant',toolCalls:[{id:'call-matches',type:'function',function:{name:'avicv__list_matches',arguments:'{}'}}]},
    {id:'t1',role:'tool',toolCallId:'call-matches',content:JSON.stringify({items:[{job_key:'job',title:'Fixture Engineer',company:'Example',category:'good',url:'https://jobs.example/apply'}]})},
    {id:'a3',role:'assistant',toolCalls:[{id:'call-tailor',type:'function',function:{name:'avicv__tailor_cv',arguments:'{}'}}]},
    {id:'t2',role:'tool',toolCallId:'call-tailor',content:JSON.stringify({id:artifact,resume_id:resume,status:'tailoring'})},
  ];
  await evaluate(`localStorage.setItem(${JSON.stringify(key)},${JSON.stringify(JSON.stringify({threadId:'restore-thread',messages}))})`);
  await send('Page.reload');
  let text='';for(let n=0;n<100;n++){text=await evaluate('document.body.innerText')??'';if(text.includes('Download tailored CV'))break;await sleep(100);}
  assert(text.includes('Saved chat survives refresh'),text); assert(text.includes('Good matches'),text);assert(text.includes('Fixture Engineer'),text);assert(text.includes('Download tailored CV'),text);assert(polls>0);assert.equal(errors.length,0,errors.join('\n'));
  const link=await evaluate(`document.querySelector('.avicv-download')?.getAttribute('href')`);assert.equal(link,`${origin}/resumes/${resume}/tailored/${artifact}/cv.pdf`);
  const shot=await send('Page.captureScreenshot',{format:'png'});const screenshots = resolve(import.meta.dir, '../../.context/avicv-mcp'); mkdirSync(screenshots, { recursive: true }); await Bun.write(join(screenshots, 'agent-browser.png'),Buffer.from(shot.data,'base64'));
  console.log(JSON.stringify({restored:true,jobCard:true,tailoredPolling:true,downloadRoute:true,browserErrors:errors.length}));
} finally {ws?.close();chrome.kill();app.stop(true);backend.stop(true);rmSync(profile,{recursive:true,force:true});}
