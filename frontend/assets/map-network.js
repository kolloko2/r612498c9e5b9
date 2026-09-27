'use strict';
// GET-only helper: bounds request lifetime without retrying user mutations.
async function mapGet(path, {signal, timeoutMs=12000}={}) {
 const controller=new AbortController();
 const cancel=()=>controller.abort();
 if(signal?.aborted)cancel();else signal?.addEventListener('abort',cancel,{once:true});
 let timedOut=false;
 const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeoutMs);
 try {
  const response=await fetch(path,{headers:{'X-Voice-UI':'1'},signal:controller.signal,cache:'no-store'});
  if(response.status===401){const error=new Error('Срок входа истёк. Войдите в кабинет повторно.');error.status=401;throw error;}
  let data;
  try{data=await response.json();}catch{throw new Error('Сервер вернул некорректный ответ. Повторите загрузку.');}
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Не удалось загрузить карту. Повторите попытку.');
  return data;
 } catch(error) {
  if(timedOut)throw new Error('Сервер долго не отвечает. Повторите загрузку карты.');
  if(signal?.aborted)throw error;
  if(error instanceof TypeError)throw new Error('Нет связи с сервером. Проверьте подключение и повторите загрузку.');
  throw error;
 } finally {clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}
if(typeof module!=='undefined')module.exports={mapGet};
