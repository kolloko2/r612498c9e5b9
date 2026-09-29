'use strict';
// Графики статистики без внешних библиотек (закрытый контур): SVG, подсказка
// при наведении, подпись источника данных, таблица значений и выгрузка CSV под
// каждым графиком — чтобы любое изображение можно было сверить с числами.
(()=>{
const NS='http://www.w3.org/2000/svg';
const COLORS={model:'#2a78d6',last:'#eb6834',mean:'#1baf7a',grid:'#e1e0d9',axis:'#8a8984',text:'#52514e',good:'#0ca30c',warning:'#fab219',critical:'#d03b3b'};
function svgNode(tag,attrs={},text){const x=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))x.setAttribute(k,v);if(text!==undefined)x.textContent=String(text);return x;}
function html(tag,text,className){const x=document.createElement(tag);if(text!==undefined)x.textContent=String(text);if(className)x.className=className;return x;}
let tip;
function tooltip(){if(!tip){tip=html('div',undefined,'chart-tip');tip.setAttribute('role','tooltip');tip.hidden=true;document.body.append(tip);}return tip;}
function hover(target,text){
 target.addEventListener('pointerenter',event=>{const t=tooltip();t.textContent=text;t.hidden=false;move(event);});
 target.addEventListener('pointermove',move);target.addEventListener('pointerleave',()=>{tooltip().hidden=true;});
 target.setAttribute('tabindex','0');target.setAttribute('aria-label',text);
 target.addEventListener('focus',()=>{const t=tooltip(),box=target.getBoundingClientRect();t.textContent=text;t.hidden=false;t.style.left=`${box.left+window.scrollX}px`;t.style.top=`${box.top+window.scrollY-36}px`;});
 target.addEventListener('blur',()=>{tooltip().hidden=true;});
}
function move(event){const t=tooltip();t.style.left=`${event.pageX+12}px`;t.style.top=`${event.pageY-34}px`;}
function csv(columns,rows){const cell=v=>{let s=v===null||v===undefined?'':String(v);if(/^\s*[=+\-@]/.test(s))s="'"+s;return `"${s.replaceAll('"','""')}"`;};return '﻿'+[columns,...rows].map(r=>r.map(cell).join(';')).join('\r\n');}
function download(name,content){const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
// Фигура: заголовок, график, легенда, источник, таблица значений и CSV.
function figure({title,subtitle,chart,legend=[],source,columns,rows,file}){
 const box=html('figure',undefined,'chart');box.append(html('h4',title));if(subtitle)box.append(html('p',subtitle,'chart-sub'));
 if(legend.length>1){const keys=html('div',undefined,'chart-legend');for(const item of legend){const key=html('span');const swatch=html('i');swatch.style.background=item.color;key.append(swatch,item.name);keys.append(key);}box.append(keys);}
 box.append(chart);
 const caption=html('figcaption');caption.append(html('strong','Источник: '),source);box.append(caption);
 if(columns&&rows){
  const details=html('details',undefined,'chart-data'),summary=html('summary','Данные графика');details.append(summary);
  const table=html('table'),head=html('tr');for(const c of columns)head.append(html('th',c));const thead=html('thead');thead.append(head);const body=html('tbody');
  for(const r of rows){const tr=html('tr');for(const v of r)tr.append(html('td',v===null||v===undefined?'—':v));body.append(tr);}
  table.append(thead,body);const wrap=html('div',undefined,'table-wrap');wrap.append(table);
  const button=html('button','Скачать CSV');button.type='button';button.onclick=()=>download(file||'chart.csv',csv(columns,rows));
  details.append(wrap,button);box.append(details);
 }
 return box;
}
function frame(width,height){const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,class:'chart-svg',role:'img'});return svg;}
// Баллы 0–100 по попыткам: линия, точки, прогноз следующей попытки с интервалом и порог.
function scoreLine({points,forecast,interval,threshold,label}){
 const W=640,H=240,L=40,R=64,T=18,B=34,svg=frame(W,H);svg.setAttribute('aria-label',label||'Баллы по попыткам');
 const n=points.length+(forecast!==undefined?1:0),x=i=>L+(n<=1?0:(W-L-R)*i/(n-1)),y=v=>T+(H-T-B)*(1-v/100);
 for(const v of [0,25,50,75,100]){svg.append(svgNode('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:COLORS.grid,'stroke-width':1}));svg.append(svgNode('text',{x:L-6,y:y(v)+4,'text-anchor':'end','font-size':11,fill:COLORS.text},v));}
 if(threshold!==undefined){svg.append(svgNode('line',{x1:L,x2:W-R,y1:y(threshold),y2:y(threshold),stroke:COLORS.axis,'stroke-width':1}));svg.append(svgNode('text',{x:W-R,y:y(threshold)-5,'text-anchor':'end','font-size':11,fill:COLORS.text},`порог ${threshold}`));}
 for(let i=0;i<n;i++)svg.append(svgNode('text',{x:x(i),y:H-12,'text-anchor':'middle','font-size':11,fill:COLORS.text},i+1));
 svg.append(svgNode('text',{x:(L+W-R)/2,y:H-1,'text-anchor':'middle','font-size':11,fill:COLORS.text},'попытка'));
 if(forecast!==undefined&&interval){const i=points.length;svg.append(svgNode('rect',{x:x(i)-9,y:y(interval[1]),width:18,height:Math.max(2,y(interval[0])-y(interval[1])),fill:COLORS.model,'fill-opacity':0.12,rx:4}));}
 if(points.length>1)svg.append(svgNode('polyline',{points:points.map((p,i)=>`${x(i)},${y(p.value)}`).join(' '),fill:'none',stroke:COLORS.model,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'}));
 points.forEach((p,i)=>{const dot=svgNode('circle',{cx:x(i),cy:y(p.value),r:5,fill:COLORS.model,stroke:'#fff','stroke-width':2});hover(dot,`Попытка ${i+1}: ${p.value} баллов${p.note?' · '+p.note:''}`);svg.append(dot);});
 if(forecast!==undefined){const i=points.length,last=points.at(-1);
  if(last)svg.append(svgNode('line',{x1:x(i-1),y1:y(last.value),x2:x(i),y2:y(forecast),stroke:COLORS.model,'stroke-width':2,'stroke-opacity':0.45}));
  const dot=svgNode('circle',{cx:x(i),cy:y(forecast),r:6,fill:'#fff',stroke:COLORS.model,'stroke-width':2});hover(dot,`Прогноз попытки ${i+1}: ${forecast} баллов, интервал ${interval[0]}–${interval[1]}`);svg.append(dot);
  svg.append(svgNode('text',{x:x(i),y:y(forecast)-12,'text-anchor':'middle','font-size':12,fill:COLORS.text},`прогноз ${forecast}`));}
 return svg;
}
// Горизонтальные полосы: одна или несколько серий на строку, значения подписаны у конца полосы.
function bars({rows,series,max,unit='',label}){
 const band=series.length*16+14,W=640,L=230,R=70,H=rows.length*band+10,svg=frame(W,H);svg.setAttribute('aria-label',label||'Сравнение');
 const scale=v=>(W-L-R)*Math.max(0,v)/(max||1);
 svg.append(svgNode('line',{x1:L,x2:L,y1:0,y2:H,stroke:COLORS.axis,'stroke-width':1}));
 rows.forEach((row,r)=>{const top=r*band+6;
  const name=svgNode('text',{x:L-8,y:top+band/2,'text-anchor':'end','font-size':12,fill:COLORS.text,'dominant-baseline':'middle'},row.label.length>34?row.label.slice(0,33)+'…':row.label);name.append(svgNode('title',{},row.label));svg.append(name);
  series.forEach((s,i)=>{const value=row.values[i];if(value===null||value===undefined)return;const w=Math.max(2,scale(value)),yy=top+i*16;
   const bar=svgNode('path',{d:`M${L},${yy} h${w-4} q4,0 4,4 v4 q0,4 -4,4 h-${w-4} z`,fill:s.color});hover(bar,`${row.label} · ${s.name}: ${value}${unit}`);svg.append(bar);
   svg.append(svgNode('text',{x:L+w+6,y:yy+6,'font-size':11,fill:COLORS.text,'dominant-baseline':'middle'},`${value}${unit}`));});
 });
 return svg;
}
// Прогноз против факта по диапазонам прогноза: точки на диагонали означают совпадение.
function calibration({bins,label}){
 const W=360,H=300,L=40,R=14,T=14,B=38,svg=frame(W,H);svg.setAttribute('aria-label',label||'Прогноз и факт');
 const x=v=>L+(W-L-R)*v/100,y=v=>T+(H-T-B)*(1-v/100);
 for(const v of [0,25,50,75,100]){svg.append(svgNode('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:COLORS.grid,'stroke-width':1}));svg.append(svgNode('text',{x:L-6,y:y(v)+4,'text-anchor':'end','font-size':11,fill:COLORS.text},v));svg.append(svgNode('text',{x:x(v),y:H-20,'text-anchor':'middle','font-size':11,fill:COLORS.text},v));}
 svg.append(svgNode('line',{x1:x(0),y1:y(0),x2:x(100),y2:y(100),stroke:COLORS.axis,'stroke-width':1}));
 svg.append(svgNode('text',{x:(L+W-R)/2,y:H-3,'text-anchor':'middle','font-size':11,fill:COLORS.text},'прогноз, баллы'));
 const axis=svgNode('text',{x:12,y:(T+H-B)/2,'text-anchor':'middle','font-size':11,fill:COLORS.text,transform:`rotate(-90 12 ${(T+H-B)/2})`},'факт, баллы');svg.append(axis);
 for(const b of bins){const r=Math.min(14,5+Math.sqrt(b.count)*1.5),dot=svgNode('circle',{cx:x(b.forecast_mean),cy:y(b.actual_mean),r,fill:COLORS.model,'fill-opacity':0.85,stroke:'#fff','stroke-width':2});hover(dot,`Прогноз ${b.range}: в среднем ${b.forecast_mean}, факт ${b.actual_mean}, попыток ${b.count}`);svg.append(dot);}
 return svg;
}
window.trainerCharts={figure,scoreLine,bars,calibration,COLORS};
})();
