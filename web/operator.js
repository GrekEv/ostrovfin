const BASE = '/api/v2/intelligence';
const el = id => document.getElementById(id);
const state = {token:'',active:'urban',queue:{},audit:[],plan:null};
const categories = {urban:'Городские записи',building:'История зданий',media:'Фотографии',geo:'Геометки',refresh:'Актуализация',audit:'История решений'};
const note = {urban:'Публикация становится видимой путешественнику только после сверки с источником.',building:'Wikidata QID должен относиться к конкретному зданию, а не к бренду отеля.',media:'Геометка снимка не доказывает вид из гостиничного номера.',geo:'Название и адрес гостиницы необходимо сверить с OSM-объектом.',refresh:'Предпросмотр отсутствующих и устаревших геофактов без запуска внешних API.',audit:'Журнал служебных действий. Изменения хранятся по времени.'};
function node(tag,cls,text) {const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=String(text);return x}
function add(parent,...children){parent.append(...children);return parent}
function empty(parent){parent.replaceChildren()}
function error(message){el('error').textContent=message;el('error').classList.remove('hidden')}
function hideError(){el('error').classList.add('hidden')}
async function request(path,method='GET',body=null){
  const headers={};if(state.token)headers.Authorization='Bearer '+state.token;
  if(body!==null)headers['Content-Type']='application/json';
  const r=await fetch(BASE+path,{method,headers,body:body===null?undefined:JSON.stringify(body),cache:'no-store'});
  if(!r.ok){let message;try{message=(await r.json()).detail}catch{}throw Error(message||`Ошибка HTTP ${r.status}`)}
  return r.json();
}
async function load(){hideError();try{
  const [overview,queue,audit,plan]=await Promise.all([request('/operator/overview'),request('/operator/queue'),request('/operator/audit'),request('/operator/refresh-plan')]);
  state.queue=queue;state.audit=audit.events;state.plan=plan;
  el('workspace').classList.remove('hidden');el('login').classList.add('hidden');
  const cards=[['Отели',overview.counts.hotel_count],['Городские',overview.counts.urban_pending],['Здания',overview.counts.building_pending],['Фото',overview.counts.media_pending],['Координаты',overview.counts.geo_pending]];
  empty(el('summary'));for(const [name,value] of cards){add(el('summary'),add(node('div','metric'),node('strong','',value),node('span','',name)))}render();
}catch(e){el('workspace').classList.add('hidden');el('login').classList.remove('hidden');error(e.message)}}
function actionButton(title,fn,cls){const x=node('button',cls,title);x.addEventListener('click',async()=>{if(!confirm(`Подтвердить действие «${title}»?`))return;x.disabled=true;hideError();try{await fn();await load()}catch(e){error(e.message)}finally{x.disabled=false}});return x}
function row(title,description,label,source,actions=[]){const r=node('div','item'),body=node('div','body'),controls=node('div','actions');
 if(label)add(body,node('span','chip',label));add(body,node('h3','',title),node('p','',description));
 if(source){try{const url=new URL(source);if(url.protocol==='https:'){const anchor=node('a','','Открыть оригинал ↗');anchor.href=url.href;anchor.target='_blank';anchor.rel='noopener noreferrer';add(body,anchor)}}catch{}}
 for(const item of actions)add(controls,item);return add(r,body,controls)}
function buttons(accept,reject){const acts=[actionButton('Одобрить',accept,'approve')];if(reject)acts.push(actionButton('Отклонить',reject,'reject'));return acts}
function render(){const view=state.active,items=el('items');empty(items);el('section-title').textContent=categories[view];el('section-note').textContent=note[view];
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('selected',x.dataset.view===view));
  let rows=[];
  if(view==='urban')rows=(state.queue.urban||[]).map(x=>row(x.title,`${x.kind} · ${x.starts_on} — ${x.ends_on} · ${x.lat}, ${x.lon} · радиус ${x.radius_m} м · ${x.description} · ID ${x.id}`,'Ожидает проверки',x.source_url,buttons(()=>request('/urban/records/'+x.id+'/review','POST',{status:'reviewed'}),()=>request('/urban/records/'+x.id+'/review','POST',{status:'rejected'}))));
  if(view==='building')rows=(state.queue.building||[]).map(x=>row(x.title,`Wikidata ${x.kind}`,'Проверка идентичности',x.source_url,buttons(()=>request(`/hotels/${x.id}/building/identity/review`,'POST',{approved:true}),()=>request(`/hotels/${x.id}/building/identity/review`,'POST',{approved:false}))));
  if(view==='media')rows=(state.queue.media||[]).map(x=>row(x.title,x.hotel_name,'Кандидат Wikimedia',x.source_url,buttons(()=>request(`/hotels/${x.hotel_id}/media/${x.id}/review`,'POST',{approved:true}),()=>request(`/hotels/${x.hotel_id}/media/${x.id}/review`,'POST',{approved:false}))));
  if(view==='geo')rows=(state.queue.geo||[]).map(x=>row(x.title,`${x.kind} · ${x.lat}, ${x.lon} · совпадение ${Number(x.score).toFixed(2)}`,'Кандидат OSM',x.source_url,[actionButton('Подтвердить координаты',()=>request(`/hotels/${x.hotel_id}/geo/approve`,'POST',{candidate_id:x.id}),'approve')]));
  if(view==='refresh')rows=(state.plan?.tasks||[]).map(x=>row(x.name,x.feature_keys.join(' · '),'Нужно проверить',null,[]));
  if(view==='audit')rows=(state.audit||[]).map(x=>row(x.event,`${x.created_at} · ${JSON.stringify(x.detail)}`,'История',null,[]));
  el('item-count').textContent=String(rows.length);
  if(!rows.length)add(items,node('div','empty',view==='refresh'?'Заданий на переобход нет.':'Новых записей нет.'));
  for(const r of rows)add(items,r);
}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{state.active=b.dataset.view;render()}));
el('connect').addEventListener('click',()=>{state.token=el('token').value.trim();el('token').value='';load()});
el('reload').addEventListener('click',load);
load();
