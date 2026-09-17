'use strict';
let localMapData={features:[]},regionalMapManifest=null,requestGeneration=0;
window.localMapReady=true;
window.drawLocalBase=(ctx,center,zoom,ratio,width,height)=>{
 for(const feature of localMapData.features){
  const points=feature.coordinates.map(([lon,lat])=>{const p=worldPoint(lat,lon,zoom);return [(p.x-center.x)*ratio+width/2,(p.y-center.y)*ratio+height/2];});
  ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));
  if(feature.kind!=='road'){
   ctx.fillStyle={building:'#d6cec0',water:'#a8d4e9',park:'#c6ddba'}[feature.kind]||'#ddd';ctx.fill();
   ctx.strokeStyle=feature.kind==='building'?'#b4aa99':ctx.fillStyle;ctx.lineWidth=ratio;ctx.stroke();
  }else{
   ctx.strokeStyle='#fff';ctx.lineWidth=(zoom>=16?6:3)*ratio;ctx.stroke();
   if(feature.name&&zoom>=15){const p=points[Math.floor(points.length/2)];ctx.fillStyle='#394d5d';ctx.font=`${11*ratio}px sans-serif`;ctx.fillText(feature.name,p[0],p[1]-4*ratio);}
  }
 }
 return localMapData.features.length>0;
};
async function mapRequest(path){const response=await fetch('/api/v1/student/map/'+path,{headers:{'X-Voice-UI':'1'}});if(response.status===401){location.replace('/login');throw Error('Требуется вход');}const data=await response.json();if(!response.ok)throw Error(data.detail||'Карта недоступна');return data;}
window.loadMapViewport=async(bounds,zoom)=>{
 const generation=++requestGeneration;
 try{const data=await mapRequest('features?'+new URLSearchParams({...bounds,zoom}));if(generation!==requestGeneration)return;data.features.sort((a,b)=>(a.kind==='road'?1:0)-(b.kind==='road'?1:0));localMapData=data;window.mapTruncated=data.truncated;document.getElementById('tileNotice').textContent=data.truncated?'Увеличьте масштаб для подробного отображения.':'Нет объектов в выбранном участке.';window.dispatchEvent(new Event('map-data-ready'));}
 catch(error){if(generation!==requestGeneration)return;localMapData={features:[]};document.getElementById('tileNotice').textContent=error.message;window.dispatchEvent(new Event('map-data-ready'));}
};
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
addEventListener('DOMContentLoaded',()=>{if(!validSessionId(new URLSearchParams(location.search).get('sid')))return;mapRequest('manifest').then(data=>{regionalMapManifest=data;document.getElementById('mapCoverage').textContent=`${data.title} · ${data.addresses.toLocaleString('ru-RU')} адресов · офлайн`;window.dispatchEvent(new CustomEvent('map-manifest-ready',{detail:data}));}).catch(error=>{document.getElementById('mapCoverage').textContent=error.message;});});
