/* Контроль качества: тот же период и авторизация, независимая загрузка от продаж. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusNames = {yes:'Да',no:'Нет',na:'Неприменимо',unknown:'Недостаточно данных'};
  const criterionNames = ['','Установил контакт','Выяснил потребность','Представил автомобиль','Выявил трейд-ин','Предложил оценку','Пригласил на встречу','Зафиксировал следующий шаг'];
  const deliveryNames = {pending:'Ожидает отправки',sending:'Отправляется',sent:'Доставлен',ambiguous:'Доставку нужно проверить',blocked:'Отправка заблокирована'};
  let offset = 0, total = 0, generation = 0, timer, initialized = false;
  const limit = 25;
  const percent = v => v == null ? '—' : `${Number(v).toLocaleString('ru-RU')}%`;
  const when = ts => new Date(ts*1000).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  const ts = value => value == null ? '' : `${Math.floor(value/60).toString().padStart(2,'0')}:${Math.floor(value%60).toString().padStart(2,'0')}`;
  function query() {
    const q = new URLSearchParams();
    if(el('dateFrom').value) q.set('start',el('dateFrom').value);
    if(el('dateTo').value) q.set('end',el('dateTo').value);
    if(el('cqManager').value) q.set('manager',el('cqManager').value);
    if(el('cqCategory').value) q.set('category',el('cqCategory').value);
    q.set('calibration',String(el('cqCalibration').checked));
    return q;
  }
  async function get(url) {
    const r = await fetch(url,{credentials:'same-origin',cache:'no-store'});
    if(r.status === 401){location.href='/login';throw new Error('Нужно войти');}
    if(!r.ok) throw new Error(`Не удалось получить звонки (${r.status})`);
    return r.json();
  }
  function card(row) {
    const score = row.is_scored ? `${row.yes_count} из ${row.applicable_count} · ${percent(row.score)}` : row.state_name;
    return `<details class="cq-call" data-call-id="${Number(row.id)}"><summary><span>${esc(when(row.occurred_at))}</span><strong>${esc(row.manager_name)}</strong><span class="cq-tag">${esc(row.category_name)}</span><span class="cq-score">${esc(score)}</span></summary><div class="cq-detail" data-detail>Открываем разбор…</div></details>`;
  }
  function metric(value,label) {return `<div class="cq-metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;}
  async function load() {
    const gen = ++generation;
    el('cqStatus').textContent='Обновляем звонки…';
    try {
      const q = query();
      const pageq = new URLSearchParams(q);pageq.set('offset',offset);pageq.set('limit',limit);
      const [s,page] = await Promise.all([get(`/api/calls/summary?${q}`),get(`/api/calls?${pageq}`)]);
      if(gen !== generation) return;
      total=page.total;
      if(!initialized){
        for(const m of s.manager_options){const o=document.createElement('option');o.value=m.id;o.textContent=m.name;el('cqManager').append(o);}
        for(const [key,name] of Object.entries(s.categories)){const o=document.createElement('option');o.value=key;o.textContent=name;el('cqCategory').append(o);}
        initialized=true;
      }
      el('cqMetrics').innerHTML=metric(s.processed,'Обработано разговоров')+metric(s.scored,'Оценено по критериям')+metric(percent(s.average_score),'Среднее выполнение')+metric(percent(s.meeting_rate),'Согласовали встречу · среди оценённых');
      let note=`Всего: ${s.total}. В очереди: ${s.pending}. Требуют проверки: ${s.needs_review}. Без записи или с ошибкой: ${s.unavailable}.`;
      if(!s.processing_enabled) note+=' Анализ новых записей пока выключен.';
      if(s.worker.calls_last_error) note+=' Обработчик требует проверки.';
      if(!s.amo_enabled) note+=' Примечания пока не отправляются.';
      if(!s.telegram_enabled || !s.telegram_configured) note+=' Telegram пока не подключён.';
      if(el('cqCalibration').checked) note+=' Калибровка показана отдельно и не включена в рабочую статистику.';
      el('cqStatus').textContent=note;
      el('cqManagers').innerHTML=s.managers.length ? `<div class="cq-table-wrap"><table class="cq-table"><thead><tr><th>Менеджер</th><th>Разговоров</th><th>Оценено</th><th>Среднее</th><th>Частые пропуски</th></tr></thead><tbody>${s.managers.map(m=>`<tr><td>${esc(m.manager_name)}</td><td>${m.calls}</td><td>${m.scored}</td><td>${esc(percent(m.average_score))}</td><td>${m.top_misses.map(x=>`${esc(x.name)} (${x.count})`).join('<br>')||'—'}</td></tr>`).join('')}</tbody></table></div>` : '<p class="cq-muted">За этот период данных пока нет.</p>';
      el('cqList').innerHTML=page.items.map(card).join('') || '<p class="cq-muted">Звонков за выбранный период пока нет.</p>';
      el('cqPage').textContent=total ? `${offset+1}–${Math.min(offset+limit,total)} из ${total}` : '0 звонков';
      el('cqPrev').disabled=offset===0;el('cqNext').disabled=offset+limit>=total;
    } catch(e){if(gen===generation) el('cqStatus').textContent=e.message;}
  }
  function evidenceHTML(item) {
    let evidence=item.evidence || [];
    if(!evidence.length) evidence=(item.conditions || []).flatMap(c=>c.evidence || []);
    const seen=new Set();
    return evidence.filter(e=>{if(seen.has(e.quote))return false;seen.add(e.quote);return true;}).map(e=>`<blockquote>${esc(ts(e.start))} ${esc(e.quote)}</blockquote>`).join('');
  }
  async function openDetail(details) {
    if(details.dataset.loaded) return;
    const target=details.querySelector('[data-detail]');
    try {
      const row=await get(`/api/calls/${Number(details.dataset.callId)}`);
      const a=row.analysis || {},out=a.outcome || {};
      const meeting=out.meeting_agreed===true?'согласована':out.meeting_agreed===false?'не согласована':'не выяснено';
      const trade={interest:'интерес',refused:'отказ',no_car:'нет автомобиля',not_discussed:'не выяснено'}[out.trade_in] || 'не выяснено';
      let html=row.binding_ambiguous?'<p class="cq-warning">Связь со сделкой неоднозначна. Автоматическое примечание заблокировано.</p>':'';
      if(row.state==='needs_review') html+='<p class="cq-warning">Результат требует проверки. В среднюю оценку не включён.</p>';
      if(row.last_error==='nexara_submit_http_402') html+='<p class="cq-warning">Nexara отклонила задание: нужно проверить баланс или доступ к платной обработке. Расшифровка не выполнена.</p>';
      html+=`<p>${esc(a.summary || row.category_reason)}</p><p>Встреча: ${esc(meeting)}${out.meeting_when?' · '+esc(out.meeting_when):''}. Трейд-ин: ${esc(trade)}.</p>`;
      if(out.next_contact) html+=`<p>Следующий контакт: ${esc(out.next_contact)}</p>`;
      html+=(a.criteria || []).map(c=>`<div class="cq-criterion"><strong>${Number(c.id)}. ${esc(criterionNames[c.id])}: ${esc(statusNames[c.status])}</strong><p>${esc(c.explanation)}</p>${evidenceHTML(c)}</div>`).join('');
      if(a.recommendation) html+=`<p><strong>Приоритет:</strong> ${esc(a.recommendation)}</p>`;
      html+=`<p>${row.lead_ids.map(id=>`<a href="https://divomotors.amocrm.ru/leads/detail/${Number(id)}" target="_blank" rel="noopener">Открыть сделку ${Number(id)}</a>`).join(' · ')}</p>`;
      const tr=row.transcript || {},segments=tr.segments || [];
      const transcript=segments.length?segments.map(s=>`<div class="cq-segment"><time>${esc(ts(s.start))}</time><strong>${esc(s.speaker || 'Спикер')}:</strong> ${esc(s.text)}</div>`).join(''):`<p>${esc(tr.text || 'Транскрипт пока недоступен.')}</p>`;
      html+=`<details class="cq-transcript"><summary>Запись и полный транскрипт</summary><audio controls preload="none" src="/api/calls/${Number(row.id)}/recording" aria-label="Запись звонка"></audio>${transcript}</details>`;
      html+=`<p class="cq-muted">${row.deliveries.map(d=>`${d.channel==='amo'?'amoCRM':'Telegram'}: ${deliveryNames[d.state] || d.state}`).join(' · ') || 'Отправок пока нет'} · Версия правил ${esc(row.rule_version || '—')}</p>`;
      if(row.validation_errors.length) html+='<p class="cq-warning">Доказательства или структура оценки не прошли проверку.</p>';
      const errors=[row.last_error,...row.validation_errors,...row.deliveries.map(d=>d.last_error)].filter(Boolean);
      if(errors.length) html+=`<details class="cq-transcript"><summary>Причины ошибок и проверки</summary>${[...new Set(errors)].map(e=>`<p class="cq-muted">${esc(e)}</p>`).join('')}</details>`;
      target.innerHTML=html;details.dataset.loaded='true';
    } catch(e){target.textContent=e.message;}
  }
  el('cqList').addEventListener('toggle',e=>{if(e.target.matches('.cq-call')&&e.target.open)openDetail(e.target);},true);
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(load,100);};
  document.addEventListener('divo:range',()=>{offset=0;schedule();});
  for(const id of ['cqManager','cqCategory','cqCalibration']) el(id).addEventListener('change',()=>{offset=0;schedule();});
  el('cqRefresh').addEventListener('click',load);
  el('cqPrev').addEventListener('click',()=>{offset=Math.max(0,offset-limit);load();});
  el('cqNext').addEventListener('click',()=>{offset+=limit;load();});
  setInterval(load,60000);
  schedule();
  const linked=Number(new URLSearchParams(location.search).get('call'));
  if(Number.isSafeInteger(linked)&&linked>0){
    get(`/api/calls/${linked}`).then(row=>{
      const box=document.createElement('div');box.innerHTML=card(row);
      el('callQuality').querySelector('.panel').prepend(box);
      const details=box.querySelector('details');details.open=true;openDetail(details);
      el('callQuality').scrollIntoView({behavior:'smooth'});
    }).catch(e=>{el('cqStatus').textContent=e.message;});
  }
})();
