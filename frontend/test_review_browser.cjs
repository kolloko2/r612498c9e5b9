// Сквозной разбор попытки и второй фактор при входе: подменённый HTTP, без Backend.
const {chromium,expect}=require('playwright/test');
const fs=require('node:fs/promises'),path=require('node:path');
const origin='http://127.0.0.1:3000',sid='22222222-2222-4222-8222-222222222222',callId='33333333-3333-4333-8333-333333333333';
const wav=Buffer.concat([Buffer.from('RIFF'),Buffer.alloc(40)]).toString('base64');
const review={session_id:sid,number:910501,status:'Завершена',student_name:'Учебный студент',
 task:{title:'Задымление в подъезде',lesson_title:'Занятие 3',mode:'Полный цикл 112: приём вызова',difficulty:'basic',dds_profile:'fire',transport:'sip',practice_with_hints:false},
 times:{created_at:'2026-09-28T10:00:00Z',finished_at:'2026-09-28T10:03:10Z',elapsed_seconds:190,attempt_outcome:null,completed_by:'student'},
 norms:[{label:'Реакция (первое действие)',limit_seconds:30,actual_seconds:12,within:true},{label:'Лимит карточки',limit_seconds:180,actual_seconds:190,within:false}],
 timeline:[{at:'2026-09-28T10:00:00Z',offset_seconds:0,type:'session.created',label:'Карточка поступила',detail:'',actor:'system'},
  {at:'2026-09-28T10:00:12Z',offset_seconds:12,type:'card.saved',label:'Карточка сохранена',detail:'',actor:'student'},
  {at:'2026-09-28T10:03:10Z',offset_seconds:190,type:'session.finished',label:'Попытка завершена',detail:'',actor:'student'}],
 calls:[{call_id:callId,kind:'Вызов заявителя',transcript:[{role:'caller',text:'У нас дым в подъезде'},{role:'student',text:'Назовите адрес'}]}],
 decision:{card:[{label:'Улица',value:'Ленина'}],services:[],criteria:[{label:'Улица',expected:['Ленина'],actual:'Ленина',passed:true,recommendation:''}],dds_checks:[]},
 assessment:{effective:{score_percent:80,passed:true,source:'automatic'},expert:null,automatic_score:80,dds_score:null,policy_result:null},grammar:{errors:0,critical_errors:0,typos:[],mechanical:[]},ai_review:null};
let mfaPassed=false;
async function serve(page){await page.route('**/*',async route=>{const request=route.request(),url=new URL(request.url()),p=url.pathname;if(url.origin!==origin)return route.abort();
 const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 if(p==='/api/v1/auth/me')return json({id:'s1',display_name:'Учебный студент',role:'student',mfa:{enabled:false,required:false}});
 if(p===`/api/v1/student/sessions/${sid}/review`)return json(review);
 if(p===`/api/v1/student/sessions/${sid}/calls/${callId}/recording`)return json({content_type:url.searchParams.get('format')==='wav'?'audio/wav':'audio/mpeg',duration_seconds:1.5,size_bytes:12288,file_base64:wav});
 if(p==='/api/v1/auth/status')return json({bootstrap_required:false});
 if(p==='/api/v1/auth/directory-status')return json({configured:false});
 if(p==='/api/v1/auth/login')return json({mfa_required:true,mfa_token:'t'.repeat(40),expires_in:300});
 if(p==='/api/v1/auth/mfa-login'){const body=request.postDataJSON();if(body.code!=='123456')return json({detail:'Неверный код подтверждения'},401);mfaPassed=true;return json({user:{id:'s1'}});}
 if(p==='/portal')return route.fulfill({contentType:'text/html',body:'<h1>Кабинет</h1>'});
 const file=p==='/review'?'review.html':p==='/login'?'login.html':p.slice(1);if(file.includes('..'))throw Error('Unsafe asset');
 return route.fulfill({body:await fs.readFile(path.join(__dirname,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});});}
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await serve(page);
 await page.goto(`${origin}/review?session=${sid}`);
 await expect(page.locator('#title')).toHaveText('Разбор попытки № 910501');
 for(const step of ['Задание','Карточка','Звонок','Решение','Разбор'])await expect(page.locator('.route')).toContainText(step);
 await expect(page.locator('#norms')).toContainText('Лимит карточки');await expect(page.locator('#norms .status.critical')).toHaveCount(1);
 // Предел норматива 30 с встаёт в хронологию между событиями на +0:12 и +3:10.
 const times=await page.locator('#timeline tbody tr td:first-child').allTextContents();
 if(JSON.stringify(times)!==JSON.stringify(['+0:00','+0:12','+0:30','+3:00','+3:10']))throw Error('Timeline order '+times);
 await expect(page.locator('#callsBody')).toContainText('Назовите адрес');
 await page.getByRole('button',{name:'Прослушать запись'}).click();await expect(page.locator('#callsBody audio')).toHaveCount(1);await expect(page.locator('.record-tools')).toContainText('MP3 12 КБ');
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Скачать WAV'}).click()]);if(!download.suggestedFilename().endsWith('.wav'))throw Error('WAV download name '+download.suggestedFilename());
 await expect(page.locator('#verdictBody')).toContainText('80%');await expect(page.locator('#verdictBody')).toContainText('Зачтено');
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Horizontal overflow');
 await page.setViewportSize({width:390,height:800});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Mobile overflow');
 // Вход со вторым фактором: после пароля — форма кода; неверный код не пускает.
 await page.goto(`${origin}/login`);await page.locator('[name=username]').fill('student1');await page.locator('[name=password]').fill('correct horse battery');await page.getByRole('button',{name:'Продолжить'}).click();
 await expect(page.locator('#mfaForm')).toBeVisible();await page.locator('[name=code]').fill('000000');await page.getByRole('button',{name:'Войти'}).click();
 await expect(page.locator('#status')).toHaveText('Неверный код подтверждения');
 await page.locator('[name=code]').fill('123456');await page.getByRole('button',{name:'Войти'}).click();await page.waitForURL('**/portal');
 if(!mfaPassed)throw Error('MFA login not called');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('Review and MFA browser flow: PASS (mock HTTP)');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
