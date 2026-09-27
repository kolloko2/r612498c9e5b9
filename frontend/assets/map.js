'use strict';
// Shared validation/coordinate helpers; rendering is owned by MapLibre.
const TILE_SIZE=256,MAX_MERCATOR_LAT=85.05112878;
// Внутри окна карточки у карты своя кнопка «Закрыть карту»: шапка страницы не нужна.
if(typeof window!=='undefined'&&window.parent!==window)document.documentElement.classList.add('embedded');
function validSessionId(value){return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value||'');}
function coordinates(latitude,longitude){const numeric=value=>typeof value==='number'||(typeof value==='string'&&value.trim()!=='');if(!numeric(latitude)||!numeric(longitude))return null;const lat=Number(latitude),lon=Number(longitude);return Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180?{latitude:lat,longitude:lon}:null;}
function worldPoint(latitude,longitude,zoom){const lat=Math.max(-MAX_MERCATOR_LAT,Math.min(MAX_MERCATOR_LAT,latitude)),scale=TILE_SIZE*2**zoom,sin=Math.sin(lat*Math.PI/180);return{x:(longitude+180)/360*scale,y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*scale};}
function geographicPoint(x,y,zoom){const scale=TILE_SIZE*2**zoom;return {longitude:Math.max(-180,Math.min(180,x/scale*360-180)),latitude:Math.atan(Math.sinh(Math.PI*(1-2*y/scale)))*180/Math.PI};}
function tilePath(zoom,x,y){const size=2**zoom,wrapped=((x%size)+size)%size;return y<0||y>=size?null:`/assets/map-tiles/${zoom}/${wrapped}/${y}.png`;}
function cardAddress(card={}){const structured=[card.city,card.street,card.house&&`дом ${card.house}`,card.building&&`корп. ${card.building}`,card.structure&&`стр. ${card.structure}`,card.apartment&&`кв. ${card.apartment}`].filter(value=>typeof value==='string'&&value.trim()).join(', '),note=typeof card.address_note==='string'?card.address_note.trim():'';return [structured,note].filter(Boolean).join(' · ');}
if(typeof module!=='undefined')module.exports={validSessionId,coordinates,worldPoint,geographicPoint,tilePath,cardAddress,MAX_MERCATOR_LAT};
