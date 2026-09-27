'use strict';
// Address search stays on the local index; MapLibre owns all map rendering.
async function mapRequest(path){return mapGet('/api/v1/student/map/'+path);}
document.getElementById('retryMap').onclick=()=>window.dispatchEvent(new Event('map-retry'));
document.getElementById('geocodeForm').onsubmit=async event=>{
 event.preventDefault();const root=document.getElementById('geocodeResults'),button=event.currentTarget.querySelector('button');root.replaceChildren();button.disabled=true;
 try{
  const {results}=await mapRequest('search?'+new URLSearchParams({q:document.getElementById('geocodeQuery').value}));
  if(!results.length){root.textContent='Адрес не найден.';return;}
  for(const item of results){const row=document.createElement('p'),show=document.createElement('button');show.type='button';show.textContent=item.label;show.onclick=()=>window.dispatchEvent(new CustomEvent('local-map-point',{detail:item}));row.append(show);root.append(row);}
 }catch(error){root.textContent=error.message;}finally{button.disabled=false;}
};
async function loadManifest(){if(!validSessionId(new URLSearchParams(location.search).get('sid')))return;try{const data=await mapRequest('manifest');document.getElementById('mapCoverage').textContent=`Москва и Московская область · локальная карта · ${data.addresses.toLocaleString('ru-RU')} адресов в поиске`;}catch(error){document.getElementById('mapCoverage').textContent=error.message;}}
addEventListener('DOMContentLoaded',loadManifest);addEventListener('map-retry',loadManifest);
addEventListener('online',()=>window.dispatchEvent(new Event('map-retry')));
