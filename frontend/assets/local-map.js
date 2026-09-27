'use strict';
let localMapData={features:[]},regionalMapManifest=null,requestGeneration=0;
let lastMapRequest=null,viewportController=null;
window.localMapReady=true;
window.drawLocalBase=(ctx,center,zoom,ratio,width,height)=>{
 const labels=new Set(),occupied=[],labelQueue=[];
 for(const feature of localMapData.features){
  const points=feature.coordinates.map(([lon,lat])=>{const p=worldPoint(lat,lon,zoom);return [(p.x-center.x)*ratio+width/2,(p.y-center.y)*ratio+height/2];});
  ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));
  if(feature.kind!=='road'){
   ctx.fillStyle={building:'#d6cec0',water:'#a8d4e9',park:'#c6ddba'}[feature.kind]||'#ddd';ctx.fill();
   ctx.strokeStyle=feature.kind==='building'?'#b4aa99':ctx.fillStyle;ctx.lineWidth=ratio;ctx.stroke();
  }else{
   ctx.strokeStyle='#fff';ctx.lineWidth=(zoom>=16?6:3)*ratio;ctx.stroke();
   if(feature.name&&zoom>=15&&!labels.has(feature.name)&&points.length){
    const p=points[Math.floor(points.length/2)];ctx.font=`${11*ratio}px sans-serif`;
    const size=ctx.measureText(feature.name).width;
    if(p[0]>=0&&p[1]>=0&&p[0]+size<=width&&p[1]<=height&&!occupied.some(([x,y,w])=>Math.abs(y-p[1])<18*ratio&&x<p[0]+size&&x+w>p[0])){
     labelQueue.push({name:feature.name,x:p[0],y:p[1]-4*ratio});labels.add(feature.name);occupied.push([p[0],p[1],size]);
    }
   }
  }
 }
 // Labels must be drawn after every road; later road strokes otherwise erase
 // individual letters and make street names look corrupted.
 ctx.font=`${11*ratio}px sans-serif`;ctx.lineWidth=3*ratio;ctx.lineJoin='round';
 for(const label of labelQueue){ctx.strokeStyle='#f7f9f3';ctx.strokeText(label.name,label.x,label.y);ctx.fillStyle='#394d5d';ctx.fillText(label.name,label.x,label.y);}
 return localMapData.features.length>0;
};
async function mapRequest(path,options){return mapGet('/api/v1/student/map/'+path,options);}
window.loadMapViewport=async(bounds,zoom)=>{
 lastMapRequest={bounds,zoom};
 const generation=++requestGeneration;
 viewportController?.abort();viewportController=new AbortController();
 window.mapLoading=true;window.mapError=false;
 document.getElementById('tileNotice').textContent='Загрузка карты…';
 document.getElementById('tileNotice').hidden=false;
 document.getElementById('retryMap').hidden=true;
 try{const data=await mapRequest('features?'+new URLSearchParams({...bounds,zoom}),{signal:viewportController.signal});if(generation!==requestGeneration)return;if(!Array.isArray(data.features))throw Error('Некорректные данные карты. Повторите загрузку.');data.features.sort((a,b)=>(a.kind==='road'?1:0)-(b.kind==='road'?1:0));localMapData=data;window.mapTruncated=data.truncated;document.getElementById('tileNotice').textContent=data.truncated?'Увеличьте масштаб для подробного отображения.':'Нет объектов в выбранном участке.';}
 catch(error){if(generation!==requestGeneration)return;window.mapError=true;document.getElementById('retryMap').hidden=false;document.getElementById('tileNotice').textContent=error.message+(localMapData.features.length?' Показан ранее загруженный участок.':'');}
 finally{if(generation===requestGeneration){window.mapLoading=false;window.dispatchEvent(new Event('map-data-ready'));}}
};
document.getElementById('retryMap').onclick=()=>window.dispatchEvent(new Event('map-retry'));
addEventListener('map-retry',()=>{loadManifest();if(lastMapRequest)window.loadMapViewport(lastMapRequest.bounds,lastMapRequest.zoom);});
document.getElementById('geocodeForm').onsubmit=async event=>{
 event.preventDefault();const root=document.getElementById('geocodeResults'),button=event.currentTarget.querySelector('button');root.replaceChildren();button.disabled=true;
 try{
  const {results}=await mapRequest('search?'+new URLSearchParams({q:document.getElementById('geocodeQuery').value}));
  if(!results.length){root.textContent='Адрес не найден.';return;}
  for(const item of results){const row=document.createElement('p'),show=document.createElement('button'),use=document.createElement('button');show.type=use.type='button';show.textContent=item.label;use.textContent='Координаты в карточку';
   show.onclick=()=>window.dispatchEvent(new CustomEvent('local-map-point',{detail:item}));
   use.onclick=()=>{const channel=new BroadcastChannel('trainer112-geocoder');channel.postMessage({type:'coordinates',sid:new URLSearchParams(location.search).get('sid'),...item});channel.close();};row.append(show,use);root.append(row);}
 }catch(error){root.textContent=error.message;}finally{button.disabled=false;}
};
async function loadManifest(){if(!validSessionId(new URLSearchParams(location.search).get('sid')))return;try{const data=await mapRequest('manifest');regionalMapManifest=data;document.getElementById('mapCoverage').textContent=`${data.title} · ${data.addresses.toLocaleString('ru-RU')} адресов · офлайн`;window.dispatchEvent(new CustomEvent('map-manifest-ready',{detail:data}));}catch(error){document.getElementById('mapCoverage').textContent=error.message;document.getElementById('retryMap').hidden=false;}}
addEventListener('DOMContentLoaded',loadManifest);
addEventListener('online',()=>window.dispatchEvent(new Event('map-retry')));
