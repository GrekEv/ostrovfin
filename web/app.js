const BASE = '/api/v2/intelligence';
const $ = id => document.getElementById(id);
const tg = window.Telegram?.WebApp;
const state = {hotels: [], features: [], results: [], criteria: [], current: null, actor: null,
    saved: new Set(readSaved('ostrovok.saved', [])), compare: new Set(readSaved('ostrovok.compare', [])),
    history: readSaved('ostrovok.history', []), profiles: new Map(), searchNumber: 0};
tg?.ready();
tg?.expand();

function readSaved(key, fallback) {try {const x = JSON.parse(localStorage.getItem(key)); return Array.isArray(x) ? x : fallback} catch {return fallback}}
function persist(key, value) {try {localStorage.setItem(key, JSON.stringify(value))} catch {}}
function node(tag, cls='', content) {const element = document.createElement(tag); if(cls) element.className = cls; if(content !== undefined) element.textContent = String(content); return element}
function clear(element) {element.replaceChildren()}
function add(parent, ...children) {parent.append(...children); return parent}
function div(cls='', content) {return node('div', cls, content)}
function btn(text, onClick, cls='outline') {const element = node('button', cls, text);element.type='button';element.addEventListener('click', onClick);return element}
function showError(target, error) {target.append(div('error', error?.message || String(error)))}
function featureLabel(key) {return state.features.find(f => f.key === key)?.label || key}
function fmt(value, key) {
    if(value === null || value === undefined) return 'Нет данных';
    if(typeof value === 'boolean') return value ? 'Да' : 'Нет';
    if(typeof value === 'number') {let unit = state.features.find(f=>f.key===key)?.unit; return `${value.toLocaleString('ru-RU')} ${unit==='m'?'м':unit==='minutes'?'мин':unit==='kg'?'кг':unit||''}`.trim()}
    if(typeof value === 'object') return value.amount !== undefined ? `${value.amount} ${value.currency||''} / ${value.per==='night'?'ночь':'заезд'}` : JSON.stringify(value);
    return String(value);
}
const statuses = {verified:['Проверено','good'], reported:['Заявлено источником','good'], estimated:['Оценка','warn'], conflicted:['Конфликт','danger'], unknown:['Неизвестно','neutral'], stale:['Устарело','warn'], match:['Совпадает','good'], partial:['Только оценка','warn'], mismatch:['Не подходит','danger']};
function badge(status) {const [label, cls] = statuses[status]||[status,'neutral']; return node('span', 'pill '+cls, label)}
function apiHeaders() {const headers={'Content-Type':'application/json'}; if(tg?.initData) headers.Authorization=`tma ${tg.initData}`;return headers}
async function api(path, options={}) {const response=await fetch(BASE+path,{...options,headers:{...apiHeaders(),...options.headers}});const body=await response.json().catch(()=>({detail:'Сервер вернул некорректный ответ'}));if(!response.ok) throw Error(typeof body.detail==='string'?body.detail:`Ошибка сервера ${response.status}`);return body}
function nav(id) {
    for(const section of document.querySelectorAll('main > section.screen')) section.classList.toggle('hidden',section.id!==id);
    document.querySelectorAll('.bottom button').forEach(button=>button.classList.toggle('active',button.dataset.tab===(['detail','help','quality','hotelier'].includes(id)?'profile':id)));
    if(id==='saved') renderSaved();
    if(id==='compare') renderCompare();
    if(id==='quality') renderMetrics();
    if(id==='hotelier') renderHotelier();
    window.scrollTo({top:0,behavior:'instant'});
}
function hotel(id) {return state.hotels.find(h=>h.id===id)}
function isDemo(h) {return h?.coord_status==='synthetic_fixture'}
function toggleSaved(id) {state.saved.has(id)?state.saved.delete(id):state.saved.add(id);persist('ostrovok.saved',[...state.saved]);refreshCards()}
function toggleCompare(id) {if(state.compare.has(id))state.compare.delete(id);else if(state.compare.size<3)state.compare.add(id);else {alert('Можно сравнить не более трёх отелей.');return} persist('ostrovok.compare',[...state.compare]);refreshCards()}
function refreshCards() {renderCards();if(!$('detail').classList.contains('hidden') && state.current) detail(state.current);if(!$('saved').classList.contains('hidden'))renderSaved()}
function recordHistory(query) {state.history=[query,...state.history.filter(x=>x!==query)].slice(0,5);persist('ostrovok.history',state.history);renderHistory()}
function renderHistory() {const wrap=$('history-wrap');const out=$('history');clear(out);wrap.classList.toggle('hidden',state.history.length===0);for(const query of state.history)out.append(btn(query,()=>{$('query').value=query;search()},'outline'))}
function renderCards() {
    const out=$('cards');clear(out);$('result-count').textContent=state.results.length+' вариантов';
    clear($('criteria'));for(const criterion of state.criteria)$('criteria').append(div('tag',featureLabel(criterion.feature_key)+(criterion.priority==='hard'?' · обязательно':'')));
    if(!state.criteria.length && state.results.length)out.append(div('notice','В запросе не обнаружены поддерживаемые условия. Уточните пожелания: тишина, питомцы, вид на закат, пешком до пляжа. Баллы без критериев не рассчитываются.'));
    if(!state.results.length){out.append(div('empty','Ничего не найдено. Попробуйте изменить запрос.'));return}
    for(const item of state.results){
        const card=div('hotel-card'), banner=div('hotel-banner'+(item.demo?' demo-banner':'')),body=div('hotel-body');
        banner.append(div('flag',item.demo?'ВЫМЫШЛЕННЫЙ ОТЕЛЬ · ДЕМО':'РЕАЛЬНЫЙ ОБЪЕКТ · ПИЛОТ'),div('motif','◒'));
        const header=div('hotel-head'), title=div();title.append(node('h3','',item.name),div('address',item.address||'Адрес не указан'));
        const score=div('score',item.score===null?'—':item.score+'%');score.append(node('small','',item.score===null?'Нет критериев':'Совпадение условий'));
        header.append(title,score);body.append(header);
        if(!item.matches.length)body.append(div('finding','Попробуйте уточнить пожелания для объяснимой оценки.'));
        for(const match of item.matches){const finding=div('finding');finding.append(badge(match.status),node('strong','',featureLabel(match.feature_key)),node('span','',match.explanation));body.append(finding)}
        if(item.caveats.length){const hard=item.caveats.filter(x=>x.startsWith('Обязательное условие')); const extra=item.caveats.filter(x=>!x.startsWith('Обязательное условие')); if(hard.length)body.append(div('card-alert','Не подтверждено: '+hard.map(x=>featureLabel(x.split(': ').at(-1))).join(', '))); for(const text of extra)body.append(div('card-alert',text));}
        if(item.demo)body.append(div('detail-notice','Все характеристики этого отеля учебные, а не проверенные сведения о реальном объекте.'));
        const actions=div('actions');actions.append(btn('Характеристики и источники',()=>detail(item.hotel_id)));
        actions.append(btn(state.saved.has(item.hotel_id)?'♥ В избранном':'♡ Сохранить',()=>toggleSaved(item.hotel_id),state.saved.has(item.hotel_id)?'outline selected':'outline'));
        actions.append(btn(state.compare.has(item.hotel_id)?'✓ В сравнении':'⇄ Сравнить',()=>toggleCompare(item.hotel_id),state.compare.has(item.hotel_id)?'outline selected':'outline'));
        body.append(actions);card.append(banner,body);out.append(card);
    }
}
async function search() {
    const query=$('query').value.trim()||'Тихий отель у моря';$('query').value=query;
    const sequence=++state.searchNumber,find=$('find');find.disabled=true;find.textContent='Исследуем характеристики…';$('cards').replaceChildren(div('skeleton'),div('skeleton'));
    try {const arrival=$('arrival').value,departure=$('departure').value;
        const data=await api('/search',{method:'POST',body:JSON.stringify({query,include_demo:$('include-demo').checked,arrival:arrival||null,departure:departure||null})});if(sequence!==state.searchNumber)return;
        state.results=data.results;state.criteria=data.criteria;recordHistory(query);renderCards();
        $('results').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(error){if(sequence===state.searchNumber){clear($('cards'));showError($('cards'),error)}}finally{if(sequence===state.searchNumber){find.disabled=false;find.replaceChildren(document.createTextNode('Подобрать отели '),node('span','', '↗'))}}
}
async function profile(id) {if(state.profiles.has(id)) return state.profiles.get(id);const value=await api(`/hotels/${id}/profile`);state.profiles.set(id,value);return value}
function evidencePanel(id, fact) {
    const details=node('details'), summary=node('summary','','Показать доказательства');details.append(summary);
    details.addEventListener('toggle',async()=>{if(!details.open||details.dataset.loaded)return;details.dataset.loaded='1';details.append(div('fact-meta','Загружаем источники…'));
        try {const response=await api(`/hotels/${id}/evidence?feature_key=${encodeURIComponent(fact.feature_key)}`);details.lastChild.remove();
            if(!response.assertions.length)details.append(div('fact-meta','Источник ещё не найден.'));
            for(const item of response.assertions) {const card=div('evidence-item');card.append(div('fact-meta',`Источник: ${item.provider} · ${item.provenance} · ${item.retrieved_at}`),div('fact-meta',`Значение: ${fmt(item.value,fact.feature_key)} · метод: ${item.method} · уверенность: ${Math.round(item.confidence*100)}%`));
                if(item.source_note)card.append(div('fact-meta',item.source_note));
                if(/^https:\/\//.test(item.url)){const a=node('a','', 'Открыть первоисточник');a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';card.append(a)}else card.append(div('fact-meta','Учебный либо внутренний источник'));
                details.append(card)}
        }catch(error){details.append(div('error',error.message))}
    });return details;
}
function geoSummary(hotel, facts) {
    const panel=div('geo-panel');panel.append(div('geo-title','География и путь к пляжу'));
    const items=new Map(facts.map(f=>[f.feature_key,f]));
    const overview=div('geo-grid');
    for(const key of ['coast_straight_m','beach_route_m','beach_walk_minutes']){
        const fact=items.get(key),cell=div('geo-metric');
        cell.append(div('geo-label',featureLabel(key)),div('geo-number',fact&&fact.value!==null?fmt(fact.value,key):'Не рассчитано'));
        if(fact)cell.append(badge(fact.status));
        overview.append(cell);
    }
    panel.append(overview);
    const trust=hotel.coord_status==='osm_manually_reviewed'?'Координаты найдены в OSM и подтверждены вручную':
                hotel.coord_status==='third_party_reported'?'Координаты из стороннего каталога; требуют сверки':
                hotel.coord_status==='synthetic_fixture'?'Учебные геоданные':
                hotel.coord_status==='sourced_reviewed'?'Координаты введены по ссылке с ручной проверкой':'Координаты ещё не подтверждены';
    panel.append(div('geo-disclaimer',trust+' · Пешеходный маршрут ведёт к точке пляжа на OSM, а не обязательно к официальному входу. Доступность требуется уточнять.'));
    if(hotel.lat!==null&&hotel.lon!==null&&Number.isFinite(Number(hotel.lat))&&Number.isFinite(Number(hotel.lon))){
        const mapLink=node('a','geo-link','Посмотреть расположение на OpenStreetMap ↗');
        mapLink.href=`https://www.openstreetmap.org/?mlat=${Number(hotel.lat)}&mlon=${Number(hotel.lon)}#map=16/${Number(hotel.lat)}/${Number(hotel.lon)}`;
        mapLink.rel='noopener noreferrer';mapLink.target='_blank';panel.append(mapLink);
    }
    return panel;
}

async function researchPanel(id) {
    const wrap=div('research-panel');wrap.append(node('h2','detail-title','Окружение, история и фотографии'));
    const dates=div('geo-disclaimer','Городские события проверяются только для выбранных дат поездки. Отсутствие записей не гарантирует отсутствие стройки.');wrap.append(dates);
    const arrival=$('arrival').value,departure=$('departure').value;
    if(arrival&&departure){
        const context=div('research-section');context.append(node('h3','','Что происходит рядом'));
        try {const data=await api(`/hotels/${id}/surroundings?arrival=${encodeURIComponent(arrival)}&departure=${encodeURIComponent(departure)}`);
            context.append(div('fact-meta',data.reason));
            if(data.events.length){for(const event of data.events){const item=div('research-event');item.append(node('strong','',event.title),div('fact-meta',`${event.starts_on} — ${event.ends_on} · ${Math.round(event.distance_m)} м · ${event.category}`));const a=node('a','geo-link','Открыть источник');a.href=event.source_url;a.target='_blank';a.rel='noopener noreferrer';item.append(a);context.append(item)}}
            else context.append(div('fact-meta','В доступных проверенных записях нет совпадений для этих дат. Полнота городских источников не установлена.'));
        } catch(error){context.append(div('error',error.message))}wrap.append(context);
    } else {wrap.append(div('notice','Выберите даты заезда и выезда на экране поиска, чтобы проверить городские события.'))}
    const buildings=div('research-section');buildings.append(node('h3','','История здания'));
    try {const identity=await api(`/hotels/${id}/building/identity`);
        if(identity.status==='approved'){
            buildings.append(div('fact-meta',`Идентификатор Wikidata ${identity.qid} проверен оператором. Исторические утверждения требуют отдельных доказательств.`));
            const a=node('a','geo-link','Объект в Wikidata');a.href=`https://www.wikidata.org/wiki/${encodeURIComponent(identity.qid)}`;a.rel='noopener noreferrer';a.target='_blank';buildings.append(a);
        }else buildings.append(div('fact-meta','Связь с историческим зданием пока не подтверждена. Название отеля не является доказательством идентичности здания.'));
    }catch(error){buildings.append(div('error',error.message))}wrap.append(buildings);
    const photos=div('research-section');photos.append(node('h3','','Фото с геометками'));
    try {const data=await api(`/hotels/${id}/media`);
        if(!data.media.length)photos.append(div('fact-meta','Фотографий, прошедших проверку, пока нет. Геометка не подтверждает вид из номера.'));
        for(const item of data.media){const card=div('research-photo');
            if(item.thumbnail_url){const img=node('img');img.src=item.thumbnail_url;img.alt=item.title;img.loading='lazy';img.referrerPolicy='no-referrer';card.append(img)}
            const desc=div('research-description');desc.append(div('fact-name',item.title),div('fact-meta',`${Math.round(item.distance_m)} м от точки отеля · лицензия: ${item.license}`));
            const a=node('a','geo-link','Оригинал, авторство и лицензия');a.href=item.page_url;a.target='_blank';a.rel='noopener noreferrer';desc.append(a);card.append(desc);photos.append(card)}
    }catch(error){photos.append(div('error',error.message))}wrap.append(photos);
    return wrap;
}

async function detail(id) {
    state.current=id;nav('detail');const out=$('detail-content');clear(out);out.append(div('skeleton'));
    try {const data=await profile(id);clear(out);const h=data.hotel, hero=div('detail-hero');hero.append(div('micro-label',isDemo(h)?'УЧЕБНЫЙ ОБЪЕКТ · НЕ СУЩЕСТВУЕТ':'РЕАЛЬНЫЙ ОТЕЛЬ · ПИЛОТ'),node('h1','',data.name),div('address',h.address));
        if(isDemo(h))hero.append(div('detail-notice','Этот отель и характеристики созданы для демонстрации логики. Не используйте их при реальном бронировании.'));
        const actions=div('actions');actions.append(btn(state.saved.has(id)?'♥ В избранном':'♡ Сохранить',()=>toggleSaved(id)),btn(state.compare.has(id)?'✓ В сравнении':'⇄ Сравнить',()=>toggleCompare(id)));hero.append(actions);out.append(hero);
        out.append(geoSummary(h,data.facts));
        out.append(await researchPanel(id));
        out.append(node('h2','detail-title','Характеристики и достоверность'));
        const sorted=[...data.facts].sort((a,b)=>((a.status==='unknown')-(b.status==='unknown'))||a.feature_key.localeCompare(b.feature_key));
        for(const f of sorted){const box=div('fact-card');box.append(div('fact-name',featureLabel(f.feature_key)),div('fact-value',fmt(f.value,f.feature_key)),badge(f.status),div('fact-meta',(f.reason||'')+(f.observed_at?` · дата свидетельства: ${new Date(f.observed_at).toLocaleDateString('ru-RU')}`:'')+(f.confidence!==null&&f.confidence!==undefined?` · уверенность оценки: ${Math.round(f.confidence*100)}%`:'')),evidencePanel(id,f));out.append(box)}
    }catch(error){clear(out);showError(out,error)}
}
function renderSaved() {const out=$('saved-content');clear(out);const selected=state.hotels.filter(h=>state.saved.has(h.id));if(!selected.length){out.append(div('empty','Сохранённых отелей пока нет. Откройте поиск и нажмите «♡ Сохранить».'));return}
    for(const h of selected){const box=div('hotel-card'),body=div('hotel-body'),top=div('hotel-head');top.append(node('h3','',h.name),badge(isDemo(h)?'estimated':'reported'));body.append(top,div('address',h.address));const a=div('actions');a.append(btn('Подробнее',()=>detail(h.id)),btn('Удалить',()=>toggleSaved(h.id)));body.append(a);box.append(body);out.append(box)}
}
async function renderCompare() {const out=$('compare-content');clear(out);const chosen=state.hotels.filter(h=>state.compare.has(h.id));if(chosen.length<2){out.append(div('empty','Добавьте в сравнение минимум два отеля. Для этого нажмите «⇄ Сравнить» в карточках поиска.'));return}
    out.append(div('skeleton'));try{const profiles=await Promise.all(chosen.map(h=>profile(h.id)));clear(out);const toolbar=div('compare-toolbar');toolbar.append(div('count',`Выбрано: ${chosen.length} / 3`),btn('Очистить',()=>{state.compare.clear();persist('ostrovok.compare',[]);renderCompare();renderCards()},'small-button'));out.append(toolbar);
        const wrapper=div('comparison'), table=node('table');const thead=node('tr');thead.append(node('th','','Характеристика'));for(const h of chosen){const th=node('th','',h.name);th.append(node('small','address',isDemo(h)?' · демо':' · пилот'));thead.append(th)}table.append(thead);
        for(const feature of state.features){const row=node('tr');row.append(node('td','',feature.label));for(const data of profiles){const fact=data.facts.find(f=>f.feature_key===feature.key);const td=node('td');if(fact){td.append(div('',fmt(fact.value,feature.key)),badge(fact.status))}row.append(td)}table.append(row)}wrapper.append(table);out.append(wrapper,div('notice','Даже совпадающие данные могут иметь разную степень достоверности. Откройте карточку отеля, чтобы изучить источники.'));
    }catch(error){clear(out);showError(out,error)}
}
async function renderMetrics() {const out=$('metrics');clear(out);try{const info=await api('/metrics');for(const [label,value] of [['Отели',info.hotels_count],['Покрытие',Math.round(info.feature_coverage*100)+'%'],['Конфликты',info.conflicts_open],['Точность',info.precision===null?'Не измерена':info.precision]]){
        const card=div('metric');card.append(node('span','',label),node('strong','',value));out.append(card)}}catch(error){showError(out,error)}}
async function renderHotelier() {const picker=$('hotel-picker'),out=$('hotel-questions');clear(picker);clear(out);
    if(!['hotelier','demo_hotelier'].includes(state.actor?.role) || !state.actor?.hotel_id){out.append(div('empty','Кабинет доступен только подтверждённому представителю отеля.'));return}
    const hotelId=state.actor.hotel_id,h=hotel(hotelId);picker.append(div('notice',h?`Ваш объект: ${h.name}`:'Ваш объект привязан к аккаунту Telegram.'));
    if(state.actor.demo){picker.append(btn('Создать демонстрационный вопрос',async()=>{try{await api('/hotelier/questions',{method:'POST',body:JSON.stringify({hotel_id:hotelId,feature_keys:['dogs_allowed','pet_max_weight_kg','cats_allowed','pet_fee'],question:'Можно ли приехать с собакой или кошкой? Какой лимит веса и доплата?',reason:'unknown'})});renderHotelier()}catch(error){showError(out,error)}},'small-button'))}
    try {const response=await api(`/hotelier/hotels/${hotelId}/questions`);if(!response.questions.length){out.append(div('empty','Открытых вопросов пока нет. Когда агенту потребуются уточнения, они появятся здесь.'));return}
        for(const question of response.questions){const card=div('question');card.append(node('h3','',question.question),div('fact-meta',question.feature_keys.map(featureLabel).join(' · ')),badge(question.status==='open'?'unknown':'reported'));
            if(question.status==='open'){const input=node('textarea');input.rows=3;input.maxLength=5000;input.placeholder='Например: собак принимаем до 12 кг, доплата 1500 рублей за ночь…';card.append(input,btn('Проверить мой ответ',async()=>{
                try {const result=await api(`/hotelier/questions/${question.id}/answer`,{method:'POST',body:JSON.stringify({text:input.value.trim()})});if(!result.candidates.length){card.append(div('notice','Не удалось уверенно выделить значения. Ответ сохранён, необходимо ручное уточнение.'));return}
                    const area=div('question');area.append(node('h3','','Подтвердите извлечённые значения'));const checkboxes=[];
                    for(const candidate of result.candidates){const label=node('label'),check=node('input');check.type='checkbox';check.checked=true;check.value=candidate.id;checkboxes.push(check);label.append(check,document.createTextNode(`${featureLabel(candidate.feature_key)} — ${fmt(candidate.value,candidate.feature_key)}`));area.append(label)}
                    area.append(btn('Подтвердить выбранные',async()=>{try{await api(`/hotelier/answers/${result.answer_id}/confirm`,{method:'POST',body:JSON.stringify({candidate_ids:checkboxes.filter(c=>c.checked).map(c=>c.value)})});state.profiles.clear();renderHotelier()}catch(error){showError(area,error)}},'primary'));card.append(area);
                }catch(error){showError(card,error)}},'primary'))}
            out.append(card)}
    }catch(error){showError(out,error)}
}
async function init() {
    document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>nav(button.dataset.tab)));
    document.querySelectorAll('[data-query]').forEach(button=>button.addEventListener('click',()=>{$('query').value=button.dataset.query;search()}));
    $('find').addEventListener('click',search);
    $('query').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();search()}});
    $('back').addEventListener('click',()=>nav('search'));
    $('header-help').addEventListener('click',()=>nav('help'));
    $('open-help').addEventListener('click',()=>nav('help'));
    $('open-quality').addEventListener('click',()=>nav('quality'));
    $('clear-history').addEventListener('click',()=>{state.history=[];persist('ostrovok.history',[]);renderHistory()});
    renderHistory();
    $('include-demo').addEventListener('change',search);
    try {
        const [hotels,features,actor,health]=await Promise.all([api('/hotels'),api('/features'),api('/me').catch(()=>({role:'traveler',hotel_id:null})),fetch('/health').then(r=>r.json())]);
        state.hotels=hotels.hotels;state.features=features.features;state.actor=actor;
        $('mode').textContent=health.mode==='demo'?'ДЕМО · СОЧИ':'ПИЛОТ · СОЧИ';
        $('actor-message').textContent=actor.display_name?`${actor.display_name}, добро пожаловать. Здесь можно сохранить отели и проверить их характеристики.`:'Путешественник · поиск и сравнение доступны без регистрации.';
        $('open-hotelier').classList.toggle('hidden',!['hotelier','demo_hotelier'].includes(actor.role));
        await search();
    } catch(error) {clear($('cards'));showError($('cards'),error)}
}
init();
