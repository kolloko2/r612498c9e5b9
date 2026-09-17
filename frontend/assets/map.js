'use strict';
const TILE_SIZE=256,MIN_ZOOM=8,MAX_ZOOM=19,MAX_MERCATOR_LAT=85.05112878;
function validSessionId(value){return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value||'');}
function coordinates(latitude,longitude){const numeric=value=>typeof value==='number'||(typeof value==='string'&&value.trim()!=='');if(!numeric(latitude)||!numeric(longitude))return null;const lat=Number(latitude),lon=Number(longitude);return Number.isFinite(lat)&&Number.isFinite(lon)&&lat>=-90&&lat<=90&&lon>=-180&&lon<=180?{latitude:lat,longitude:lon}:null;}
function worldPoint(latitude,longitude,zoom){const lat=Math.max(-MAX_MERCATOR_LAT,Math.min(MAX_MERCATOR_LAT,latitude)),scale=TILE_SIZE*2**zoom,sin=Math.sin(lat*Math.PI/180);return{x:(longitude+180)/360*scale,y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*scale};}
function geographicPoint(x,y,zoom){const scale=TILE_SIZE*2**zoom;return {longitude:Math.max(-180,Math.min(180,x/scale*360-180)),latitude:Math.atan(Math.sinh(Math.PI*(1-2*y/scale)))*180/Math.PI};}
function tilePath(zoom,x,y){const size=2**zoom,wrapped=((x%size)+size)%size;return y<0||y>=size?null:`/assets/map-tiles/${zoom}/${wrapped}/${y}.png`;}
function cardAddress(card={}){const structured=[card.city,card.street,card.house&&`дом ${card.house}`,card.apartment&&`кв. ${card.apartment}`].filter(value=>typeof value==='string'&&value.trim()).join(', '),note=typeof card.address_note==='string'?card.address_note.trim():'';return [structured,note].filter(Boolean).join(' · ');}
if(typeof module!=='undefined')module.exports={validSessionId,coordinates,worldPoint,geographicPoint,tilePath,cardAddress,MAX_MERCATOR_LAT};

if(typeof document!=='undefined'){
 const el=id=>document.getElementById(id),canvas=el('mapCanvas'),ctx=canvas.getContext('2d');
 let point=null,centerPoint={latitude:55.7558,longitude:37.6173},zoom=12,lastViewport='',viewportTimer=null,drag=null;
 function setStatus(text,error=false){el('status').textContent=text;el('status').className=error?'error':'';}
 function fitCanvas(){const ratio=Math.min(devicePixelRatio||1,2),rect=canvas.getBoundingClientRect(),width=Math.max(320,Math.round(rect.width*ratio)),height=Math.max(300,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}return ratio;}
 function marker(x,y,ratio){ctx.save();ctx.translate(x,y);ctx.fillStyle='#c52f25';ctx.strokeStyle='#fff';ctx.lineWidth=3*ratio;ctx.beginPath();ctx.arc(0,0,9*ratio,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.restore();}
 function draw(){
  const ratio=fitCanvas(),width=canvas.width,height=canvas.height,center=worldPoint(centerPoint.latitude,centerPoint.longitude,zoom);
  ctx.fillStyle='#e9eee5';ctx.fillRect(0,0,width,height);
  const ready=window.drawLocalBase?.(ctx,center,zoom,ratio,width,height);
  if(point){const p=worldPoint(point.latitude,point.longitude,zoom);marker((p.x-center.x)*ratio+width/2,(p.y-center.y)*ratio+height/2,ratio);}
  el('zoomLabel').textContent=`Масштаб ${zoom}`;el('zoomOut').disabled=zoom<=MIN_ZOOM;el('zoomIn').disabled=zoom>=MAX_ZOOM;el('tileNotice').hidden=Boolean(ready)&&!window.mapTruncated;
  const nw=geographicPoint(center.x-width/(2*ratio),center.y-height/(2*ratio),zoom),se=geographicPoint(center.x+width/(2*ratio),center.y+height/(2*ratio),zoom);
  const bounds={west:nw.longitude,south:se.latitude,east:se.longitude,north:nw.latitude},key=JSON.stringify([bounds,zoom]);
  if(key!==lastViewport){lastViewport=key;clearTimeout(viewportTimer);viewportTimer=setTimeout(()=>window.loadMapViewport?.(bounds,zoom),120);}
 }
 function showEmpty(text){canvas.hidden=true;el('emptyMap').hidden=false;el('emptyMap').textContent=text;el('tileNotice').hidden=true;}
 async function init(){
  const sid=new URLSearchParams(location.search).get('sid');if(!validSessionId(sid)){setStatus('Некорректная ссылка на карточку.',true);showEmpty('Карта не открыта.');return;}
  try{
   const response=await fetch(`/api/v1/student/sessions/${encodeURIComponent(sid)}`,{headers:{'X-Voice-UI':'1'}});if(response.status===401){location.replace('/login');return;}
   const data=await response.json().catch(()=>({}));if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Не удалось загрузить карточку.');
   const card=data.card||{};el('address').textContent=cardAddress(card)||'Адрес не указан';point=coordinates(card.latitude,card.longitude);
   if(point){centerPoint=point;zoom=16;el('latitude').textContent=point.latitude.toFixed(6);el('longitude').textContent=point.longitude.toFixed(6);setStatus('Место происшествия.');}
   else{setStatus('Найдите адрес, чтобы выбрать место происшествия.');el('geocodeQuery').value=[card.city,card.street,card.house].filter(Boolean).join(' ');}
   draw();
  }catch(error){setStatus(error.message,true);showEmpty('Не удалось открыть карту.');}
 }
 addEventListener('local-map-point',event=>{point=coordinates(event.detail.latitude,event.detail.longitude);if(!point)return;centerPoint=point;canvas.hidden=false;el('emptyMap').hidden=true;el('latitude').textContent=point.latitude.toFixed(6);el('longitude').textContent=point.longitude.toFixed(6);el('address').textContent=event.detail.label;setStatus('Выбранный адрес.');zoom=17;draw();});
 const changeZoom=delta=>{zoom=Math.max(MIN_ZOOM,Math.min(MAX_ZOOM,zoom+delta));draw();};
 el('zoomOut').onclick=()=>changeZoom(-1);el('zoomIn').onclick=()=>changeZoom(1);el('closeMap').onclick=()=>window.close();
 canvas.onpointerdown=event=>{drag={x:event.clientX,y:event.clientY,center:worldPoint(centerPoint.latitude,centerPoint.longitude,zoom)};canvas.setPointerCapture(event.pointerId);};
 canvas.onpointermove=event=>{if(!drag)return;centerPoint=geographicPoint(drag.center.x-(event.clientX-drag.x),drag.center.y-(event.clientY-drag.y),zoom);draw();};
 canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};
 canvas.addEventListener('wheel',event=>{event.preventDefault();changeZoom(event.deltaY<0?1:-1);},{passive:false});
 canvas.onkeydown=event=>{const offsets={ArrowLeft:[-150,0],ArrowRight:[150,0],ArrowUp:[0,-150],ArrowDown:[0,150]};if(offsets[event.key]){event.preventDefault();const p=worldPoint(centerPoint.latitude,centerPoint.longitude,zoom),[x,y]=offsets[event.key];centerPoint=geographicPoint(p.x+x,p.y+y,zoom);draw();}};
 addEventListener('resize',draw);addEventListener('map-data-ready',draw);init();
}
