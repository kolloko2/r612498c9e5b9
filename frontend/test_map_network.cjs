const {test}=require('node:test');
const assert=require('node:assert/strict');
const {mapGet}=require('./assets/map-network.js');

test('map GET returns data and does not retry',async()=>{
 let calls=0;global.fetch=async(path,options)=>{calls++;assert.equal(options.cache,'no-store');return {ok:true,status:200,json:async()=>({features:[]})};};
 assert.deepEqual(await mapGet('/map'),{features:[]});assert.equal(calls,1);
});
test('network failure is understandable without raw fetch error',async()=>{
 global.fetch=async()=>{throw new TypeError('Failed to fetch');};
 await assert.rejects(mapGet('/map'),/Нет связи с сервером/);
});
test('timeout aborts the underlying request',async()=>{
 let aborted=false;global.fetch=async(path,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>{aborted=true;reject(new DOMException('Aborted','AbortError'));}));
 await assert.rejects(mapGet('/map',{timeoutMs:5}),/долго не отвечает/);assert.ok(aborted);
});
test('cancelled viewport is not confused with a timeout',async()=>{
 global.fetch=async(path,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));
 const controller=new AbortController(),request=mapGet('/map',{signal:controller.signal});controller.abort();
 await assert.rejects(request,{name:'AbortError'});
});
test('invalid JSON and expired login have explicit errors',async()=>{
 global.fetch=async()=>({ok:false,status:502,json:async()=>{throw Error('html');}});
 await assert.rejects(mapGet('/map'),/некорректный ответ/);
 global.fetch=async()=>({status:401});await assert.rejects(mapGet('/map'),{status:401});
});
