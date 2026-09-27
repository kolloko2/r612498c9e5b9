'use strict';
const el=id=>document.getElementById(id),sid=new URLSearchParams(location.search).get('sid');
let map,marker,point,originalPoint,canApply=false,selectMode=true,sessionLoaded=false,loading=false;
function status(text,error=false){el('status').textContent=text;el('status').className=error?'error':'';}
function selectPoint(value,label='Выбранная точка'){
 const selected=coordinates(value.latitude,value.longitude);if(!selected||!sessionLoaded)return;
 point=selected;el('latitude').textContent=point.latitude.toFixed(6);el('longitude').textContent=point.longitude.toFixed(6);
 el('selectedCoordinates').value=`${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}`;
 el('copyPoint').disabled=false;el('usePoint').disabled=!canApply;
 if(map){marker??=new maplibregl.Marker({color:'#c52f25'});marker.setLngLat([point.longitude,point.latitude]).addTo(map);}
 status(label);
}
function mode(select){selectMode=select;el('selectPoint').setAttribute('aria-pressed',String(select));el('panMap').setAttribute('aria-pressed',String(!select));el('mapCanvas').classList.toggle('select-point',select);}
el('selectPoint').onclick=()=>mode(true);el('panMap').onclick=()=>mode(false);mode(true);
el('copyPoint').onclick=async()=>{if(!point)return;try{await navigator.clipboard.writeText(el('selectedCoordinates').value);status('Координаты скопированы.');}catch{el('selectedCoordinates').focus();el('selectedCoordinates').select();status('Нажмите Ctrl+C, чтобы скопировать координаты.');}};
el('usePoint').onclick=()=>{if(!point||!canApply||!sessionLoaded)return;const channel=new BroadcastChannel('trainer112-geocoder');channel.postMessage({type:'coordinates',sid,...point,label:`Точка на карте: ${el('selectedCoordinates').value}`});channel.close();status('Подтвердите перенос координат в окне карточки.');};
el('originalPoint').onclick=()=>{if(originalPoint&&map){map.jumpTo({center:[originalPoint.longitude,originalPoint.latitude],zoom:16});selectPoint(originalPoint,'Координаты из карточки.');}};
el('zoomIn').onclick=()=>map?.zoomIn({duration:0});el('zoomOut').onclick=()=>map?.zoomOut({duration:0});
el('closeMap').onclick=()=>{if(parent!==window)parent.postMessage({type:'close-map'},location.origin);else window.close();};
el('mapCanvas').addEventListener('keydown',event=>{if(event.key==='Enter'&&selectMode&&map){event.preventDefault();const p=map.getCenter();selectPoint({latitude:p.lat,longitude:p.lng});}});
addEventListener('local-map-point',event=>{const p=coordinates(event.detail.latitude,event.detail.longitude);if(!p||!map||!sessionLoaded)return;map.jumpTo({center:[p.longitude,p.latitude],zoom:17});selectPoint(p,'На карте: '+event.detail.label);});
function failed(){el('tileNotice').hidden=false;el('tileNotice').textContent='Не удалось загрузить локальную карту. Проверьте соединение с сервером и повторите загрузку.';el('retryMap').hidden=false;}
async function init(){
 if(loading)return;loading=true;
 try{
  if(!validSessionId(sid))throw Error('Некорректная ссылка на карточку.');
  const data=await mapGet(`/api/v1/student/sessions/${sid}`),card=data.card||{};
  el('address').textContent=cardAddress(card)||'Адрес не указан';
  originalPoint=coordinates(card.latitude,card.longitude);point??=originalPoint;
  canApply=data.exercise_mode!=='actions'&&data.status!=='Завершена'&&!data.card_locked;sessionLoaded=true;
  el('usePoint').hidden=!canApply;el('originalPoint').disabled=!originalPoint;
  el('pointPolicy').textContent=canApply?'Выбор точки не меняет карточку. Перенос координат требует подтверждения и сохранения.':'Исходные координаты карточки не меняются. Скопируйте выбранную точку для комментария или уточнения.';
  if(!el('geocodeQuery').value)el('geocodeQuery').value=[card.city,card.street,card.house].filter(Boolean).join(' ');
  const probe=document.createElement('canvas'),gl=probe.getContext('webgl2');
  if(!gl)throw Error('Для карты включите аппаратное ускорение в настройках браузера (WebGL).');
  gl.getExtension('WEBGL_lose_context')?.loseContext();
  const style=await mapGet('/assets/maplibre/style.json');
  style.sprite=location.origin+style.sprite;style.glyphs=location.origin+style.glyphs;
  for(const source of Object.values(style.sources))source.url='pmtiles://'+new URL('/map-data/region.pmtiles',location.origin).href;
  const previous=map?{center:map.getCenter(),zoom:map.getZoom()}:null;
  marker?.remove();marker=null;map?.remove();
  // A failed header promise can remain cached inside PMTiles. Retry with a fresh cache.
  maplibregl.removeProtocol('pmtiles');maplibregl.addProtocol('pmtiles',new pmtiles.Protocol().tile);
  el('emptyMap').hidden=true;el('tileNotice').hidden=false;el('tileNotice').textContent='Загрузка карты…';el('retryMap').hidden=true;
  map=new maplibregl.Map({container:'mapCanvas',style,center:previous?.center||(point?[point.longitude,point.latitude]:[37.6173,55.7558]),zoom:previous?.zoom||(point?16:12),minZoom:7,maxZoom:19,maxBounds:[[35.1,54.2],[40.3,56.95]],renderWorldCopies:false,dragRotate:false,pitchWithRotate:false,attributionControl:true,clickTolerance:5});
  map.touchZoomRotate.disableRotation();
  map.on('click',event=>{if(selectMode)selectPoint({latitude:event.lngLat.lat,longitude:event.lngLat.lng});});
  const zoom=()=>{el('zoomLabel').textContent=`Масштаб ${Math.round(map.getZoom())}`;el('zoomIn').disabled=map.getZoom()>=19;el('zoomOut').disabled=map.getZoom()<=7;};map.on('zoom',zoom);zoom();
  let mapFailed=false;map.on('error',event=>{console.warn('Local map:',event.error?.message);mapFailed=true;failed();});
  map.on('idle',()=>{if(!mapFailed){el('tileNotice').hidden=true;el('mapCanvas').dataset.ready='true';}});
  if(point)selectPoint(point,'Координаты выбранной точки.');else status('Найдите адрес или укажите место на карте.');
 }catch(error){status(error.message,true);el('emptyMap').hidden=false;el('emptyMap').textContent=error.message;failed();}
 finally{loading=false;}
}
maplibregl.setWorkerUrl('/assets/maplibre/maplibre-gl-csp-worker.js');
addEventListener('map-retry',init);init();
