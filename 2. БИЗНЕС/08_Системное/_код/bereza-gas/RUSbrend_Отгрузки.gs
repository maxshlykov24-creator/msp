/**
 * ============================================================================
 *  RUSbrend × МойСклад — создание ОТГРУЗОК из таблицы «Склад Белград»
 *  Лист «План работы», флажок в столбце I → Demand в МойСклад.
 *  Версия 2.0
 *
 *  Что нового в 2.0:
 *    - номер отгрузки НЕ задаётся скриптом — его присваивает сама МойСклад
 *      по порядку (00001, 00002, ...);
 *    - каждая отгрузка получает доп-поля «Наименование» = «артикул — наименование»
 *      и «Комплектов» (кол-во в коробе) — чтобы было удобно ориентироваться
 *      в списке всех отгрузок;
 *    - «Тип короба» подтягивается из карточки товара (атрибут «Часто
 *      используемый короб»), при отсутствии — короб S;
 *    - организация выбирается по бренду (RUSbrend → ИП Ткачев, GripOn → GripOn);
 *    - дата moment в формате МойСклад, ретраи сети, лог ошибок + Telegram.
 *
 *  Установка: Script Properties → MS_TOKEN (обязательно). Запусти setup() один раз.
 *  Все остальные id ниже уже подставлены из боевого аккаунта.
 * ============================================================================
 */

var MS_BASE = 'https://api.moysklad.ru/api/remap/1.2';
var SHEET_NAME = 'План работы';
var RETRIES = 3;

// Колонки листа «План работы» (1-based). Ключевые для скрипта:
//   D(4) Артикул | F(6) Оплачено комплектов | U(21) Всего коробов | I(9) флажок | J(10) ID МС
var COL = {
  SHOP:       3,  // C — Магазин / Бренд
  ARTICLE:    4,  // D — Артикул
  NAME:       5,  // E — Наименование
  KITS_PAID:  6,  // F — Оплачено комплектов (всего комплектов к отгрузке)
  BOXES_PAID: 7,  // G — Оплачено коробов (справочно, скрипт не использует)
  BOXES_TOTAL:21, // U — Всего коробов (сколько отгрузок создать)
  FLAG:       9,  // I — Отгрузить (флажок) — ТРИГГЕР
  MS_IDS:     10, // J — ID в МойСклад (заполняет скрипт)
};
var FIRST_DATA_ROW = 2;

// Предохранитель: если расчёт даёт больше коробов, чем это — скрипт остановится
// (защита от ошибочных данных / незаполненной вместимости короба в карточке).
var MAX_BOXES_PER_ROW = 500;

// ─── Матрица (лист «База») — источник «кол-ва в коробе» и типа короба ────────
// Артикул из Склада (План работы, столбец D) ищем в Матрице по столбцу C.
var MATRIX_SHEET = 'Матрица (техническое)';
var MATRIX_COL = {
  ARTICLE:  3,  // C — Артикул
  BOX_TYPE: 7,  // G — Часто используемый короб (S/M/L)
  BOX_QTY:  8,  // H — Количество в выбранном коробе
};
// Справочник «Короб» (значения S/M/L) — те же id, что и в скрипте «База».
var BOX_MAP = {
  S: '106993b0-4fa4-11f1-0a80-07550014b96a',
  M: '12b42bc3-4fa4-11f1-0a80-134700142471',
  L: '1475f29d-4fa4-11f1-0a80-056700138e7e',
};

// ─── ID из боевого аккаунта МойСклад ────────────────────────────────────────
var STORE_HREF  = MS_BASE + '/entity/store/bad1b6d6-4f99-11f1-0a80-07550012fedb'; // Основной склад
var AGENT_HREF  = MS_BASE + '/entity/counterparty/2f5dbcee-52c5-11f1-0a80-17d5007f6f31'; // Ozon
var ORG = {
  RUSBREND: MS_BASE + '/entity/organization/bacfb4c2-4f99-11f1-0a80-07550012fed8', // ИП Ткачев
  GRIPON:   MS_BASE + '/entity/organization/1c401361-4f9e-11f1-0a80-16560012de2e', // GripOn
};

// Статус новой отгрузки («Новая») + документ создаётся НЕпроведённым (applicable:false)
var STATE_NEW_HREF = MS_BASE + '/entity/demand/metadata/states/660e2016-4f9e-11f1-0a80-1c9700143ddb';

// Доп-поля ОТГРУЗКИ
var D_ATTR = {
  BOX:  'b2243b74-4fa3-11f1-0a80-0d080013d856', // Тип короба (customentity, обязательный)
  NAME: '3ab984f2-6eca-11f1-0a80-02f3003e352c', // Наименование (строка)
  KITS: 'b2243885-4fa3-11f1-0a80-0d080013d855', // Комплектов (число)
};
var CE_BOX     = '87b0e62a-4fa3-11f1-0a80-13470014140f'; // справочник «Короб»
var BOX_S_HREF = MS_BASE + '/entity/customentity/' + CE_BOX + '/106993b0-4fa4-11f1-0a80-07550014b96a';

// Атрибут ТОВАРА «Часто используемый короб» (чтобы взять короб для отгрузки)
var P_ATTR_BOX   = '5b730858-4fa4-11f1-0a80-1d2e0015579d';
// Атрибут ТОВАРА «Количество в выбранном коробе» (= комплектов в коробе)
var P_ATTR_BOXQTY = 'e8810f2c-4f9d-11f1-0a80-15600014179f';

// ============================================================================
//  МЕНЮ
// ============================================================================
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Отгрузки')
    .addItem('Создать отгрузки по флажкам (ручной запуск)', 'createFlaggedDemands')
    .addItem('ТЕСТ: создать 1 отгрузку в МойСклад', 'createTestDemand')
    .addSeparator()
    .addItem('Установить триггер (онлайн по флажку)', 'setup')
    .addToUi();
}

function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'onFlagChange') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('onFlagChange').forSpreadsheet(ss).onEdit().create();
  SpreadsheetApp.getActive().toast('Триггер установлен. Проверь Script Properties: MS_TOKEN.');
}

/**
 * ДИАГНОСТИКА. Запусти из редактора (выбрать diag → Выполнить).
 * Пишет каждый шаг в лист «Errors» и в Логи — видно причину без всплывающих окон.
 */
function diag() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var log = function (m) {
    Logger.log(m);
    var sh = ss.getSheetByName('Errors') || ss.insertSheet('Errors');
    sh.appendRow([new Date(), 'DIAG', m]);
  };
  try {
    log('1. Старт диагностики.');

    var tok = PropertiesService.getScriptProperties().getProperty('MS_TOKEN');
    log('2. MS_TOKEN: ' + (tok ? ('задан, длина ' + tok.length) : 'НЕ ЗАДАН'));
    if (!tok) { log('СТОП: задай MS_TOKEN в Свойствах скрипта.'); return; }

    var sheet = ss.getSheetByName(SHEET_NAME);
    log('3. Лист "' + SHEET_NAME + '": ' + (sheet ? 'найден' : 'НЕ НАЙДЕН — проверь SHEET_NAME'));
    if (!sheet) return;

    var last = sheet.getLastRow();
    var flagged = 0, firstFlaggedRow = 0;
    for (var row = FIRST_DATA_ROW; row <= last; row++) {
      if (sheet.getRange(row, COL.FLAG).getValue() === true) {
        flagged++;
        if (!firstFlaggedRow) firstFlaggedRow = row;
      }
    }
    log('4. Строк с галочкой в столбце «Отгрузить» (FLAG=' + COL.FLAG + ', т.е. столбец I): ' + flagged);

    if (firstFlaggedRow) {
      var art = String(sheet.getRange(firstFlaggedRow, COL.ARTICLE).getDisplayValue() || '').trim();
      var nm  = String(sheet.getRange(firstFlaggedRow, COL.NAME).getValue() || '').trim();
      log('5. Первая отмеченная строка ' + firstFlaggedRow + ': артикул="' + art + '", наименование="' + nm + '"');
    }

    // Проверка связи с МойСклад: ищем товар 106
    var p = findProductByArticle_('106');
    log('6. Связь с МойСклад (поиск товара 106): ' + (p ? ('OK, ' + p.name) : 'НЕ НАЙДЕН/нет связи'));
    if (!p) { log('СТОП: товар не найден или токен неверный.'); return; }

    // Пробуем создать тестовую отгрузку
    var id = createDemand_({
      article: '106', posName: p.name, boxLabel: ' (DIAG)',
      product: p, quantity: p.boxQty || 1, shop: 'RUSbrend',
    });
    log('7. Создание тестовой отгрузки: ' + (id ? ('УСПЕХ, id=' + id) : 'ОШИБКА (см. строку выше/Логи)'));
    log('ИТОГ: ' + (id ? 'всё работает — отгрузка создана в МойСклад.' : 'не удалось создать, причина выше.'));
  } catch (ex) {
    log('ИСКЛЮЧЕНИЕ: ' + ex.message);
  }
}

/**
 * РУЧНОЙ запуск (не зависит от триггера): проходит лист «План работы»,
 * создаёт отгрузки по всем строкам с флажком в столбце FLAG и пустым ID.
 * Показывает итог и ошибки в окне — удобно для демо и отладки.
 */
function createFlaggedDemands() {
  var ui = SpreadsheetApp.getUi();
  try {
    getToken_(); // упадёт с понятной ошибкой, если нет MS_TOKEN
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(SHEET_NAME);
    if (!sheet) { ui.alert('Нет листа «' + SHEET_NAME + '». Проверь имя вкладки в коде (SHEET_NAME).'); return; }

    var last = sheet.getLastRow();
    var done = 0, skipped = 0;
    for (var row = FIRST_DATA_ROW; row <= last; row++) {
      var flag = sheet.getRange(row, COL.FLAG).getValue();
      if (flag !== true) continue;
      var existing = sheet.getRange(row, COL.MS_IDS).getValue();
      if (existing && existing.toString().trim() !== '') { skipped++; continue; }
      processRow_(ss, sheet, row);
      done++;
    }
    ui.alert('Готово. Создано отгрузок по строкам: ' + done +
             (skipped ? ('\nПропущено (уже есть ID): ' + skipped) : '') +
             '\nЕсли где-то пусто — смотри лист Errors.');
  } catch (ex) {
    ui.alert('Ошибка: ' + ex.message);
  }
}

/** ТЕСТ для демо: создаёт одну отгрузку по товару с артикулом из ячейки A1 листа
 *  (или по умолчанию «106»). Не зависит от структуры листа. */
function createTestDemand() {
  var ui = SpreadsheetApp.getUi();
  try {
    getToken_();
    var art = '106';
    var product = findProductByArticle_(art);
    if (!product) { ui.alert('Товар ' + art + ' не найден в МойСклад.'); return; }
    var id = createDemand_({
      article: art, posName: product.name, boxLabel: ' (ТЕСТ)',
      product: product, quantity: product.boxQty || 1, shop: 'RUSbrend',
    });
    if (id) ui.alert('Отгрузка создана в МойСклад. Открой раздел «Отгрузки» — последняя по номеру. id=' + id);
    else ui.alert('Не удалось создать (смотри Логи выполнения в Apps Script).');
  } catch (ex) {
    ui.alert('Ошибка: ' + ex.message);
  }
}

function getToken_() {
  var t = PropertiesService.getScriptProperties().getProperty('MS_TOKEN');
  if (!t) throw new Error('Не задан MS_TOKEN в Script Properties.');
  return t;
}

// ============================================================================
function onFlagChange(e) {
  if (!e || !e.range) return;
  var sheet = e.range.getSheet();
  if (sheet.getName() !== SHEET_NAME) return;
  if (e.range.getColumn() !== COL.FLAG) return;
  if (e.range.getRow() < FIRST_DATA_ROW) return;

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var row = e.range.getRow();

  // читаем галочку из ячейки (надёжнее, чем e.value)
  if (sheet.getRange(row, COL.FLAG).getValue() !== true) return;

  var existing = sheet.getRange(row, COL.MS_IDS).getValue();
  if (existing && existing.toString().trim() !== '') {
    logError_(ss, row, 'Флаг повторно. ID уже есть: ' + existing);
    return;
  }
  processRow_(ss, sheet, row);
}

function processRow_(ss, sheet, row) {
  var article   = String(sheet.getRange(row, COL.ARTICLE).getDisplayValue() || '').trim();
  var name      = String(sheet.getRange(row, COL.NAME).getValue() || '').trim();
  var shop      = String(sheet.getRange(row, COL.SHOP).getValue() || '').trim();
  var totalKits = parseInt(sheet.getRange(row, COL.KITS_PAID).getValue(), 10) || 0;

  if (!article || !name) { logError_(ss, row, 'Пустой артикул или наименование.'); return; }
  if (totalKits <= 0) {
    logError_(ss, row, 'Не заполнено «Оплачено комплектов» (столбец F) — нечего отгружать.');
    return;
  }

  var product = findProductByArticle_(article);
  if (!product) { logError_(ss, row, 'Товар не найден по артикулу: ' + article); return; }

  // Вместимость короба берём из МАТРИЦЫ (лист «База», столбец H), по артикулу (столбец C).
  // Карточка МойСклад — запасной источник, если в Матрице пусто.
  var matrix = getMatrixInfo_(article);
  var kitsPerBox = (matrix && matrix.boxQty > 0) ? matrix.boxQty : (product.boxQty || 0);
  if (kitsPerBox <= 0) {
    logError_(ss, row, 'Не нашёл «Количество в коробе» для артикула ' + article +
      ' ни в Матрице (лист «' + MATRIX_SHEET + '», столбец H), ни в карточке МойСклад.');
    return;
  }

  // Тип короба тоже берём из Матрицы (столбец G), если задан; иначе — из карточки МойСклад.
  if (matrix && matrix.boxType && BOX_MAP[matrix.boxType]) {
    product.boxValue = { meta: {
      href: MS_BASE + '/entity/customentity/' + CE_BOX + '/' + BOX_MAP[matrix.boxType],
      type: 'customentity', mediaType: 'application/json',
    }};
  }

  // Число отгрузок — из столбца U «Всего коробов» (не G «Оплачено коробов»).
  // Комплектов в каждом коробе — из Матрицы; последний короб = остаток от F.
  // Пример: F=1000, U=10, в коробе 100 → 10 отгрузок по 100. F=1005, U=11 → последняя на 5.
  var numBoxes = parseInt(sheet.getRange(row, COL.BOXES_TOTAL).getValue(), 10) || 0;
  if (numBoxes <= 0) {
    logError_(ss, row, 'Не заполнено «Всего коробов» (столбец U) — не знаю, сколько отгрузок создавать.');
    return;
  }
  if (numBoxes > MAX_BOXES_PER_ROW) {
    logError_(ss, row, 'В столбце U указано ' + numBoxes + ' коробов — больше лимита ' +
      MAX_BOXES_PER_ROW + '. Отгрузки НЕ создавались.');
    return;
  }

  // имя позиции для доп-поля «Наименование» — берём из карточки, иначе из листа
  var posName = product.name || name;

  var ids = [];
  var remaining = totalKits;
  for (var i = 0; i < numBoxes; i++) {
    var qty = Math.min(kitsPerBox, remaining);   // последний короб = остаток
    
    // Если комплекты закончились до того, как мы создали все указанные в U короба:
    // (например, указали U=65, а по количеству комплектов F хватает только на 34).
    if (qty <= 0) {
      logError_(ss, row, 'Создано ' + i + ' коробов из ' + numBoxes + '. Комплекты из столбца F (' + totalKits + ') закончились. Проверьте количество коробов (U) и комплектов (F).');
      break;
    }
    
    remaining -= qty;
    var boxLabel = numBoxes > 1 ? (' (короб ' + (i + 1) + '/' + numBoxes + ', ' + qty + ' компл.)') : '';
    var id = createDemand_({
      article:  article,
      posName:  posName,
      boxLabel: boxLabel,
      product:  product,
      quantity: qty,   // комплектов в этом коробе
      shop:     shop,
    });
    if (id) {
      ids.push(id);
      sheet.getRange(row, COL.MS_IDS).setValue(ids.join(', '));
    } else {
      logError_(ss, row, 'Ошибка создания отгрузки ' + (i + 1) + '/' + numBoxes + ' (арт. ' + article + ')');
    }
    Utilities.sleep(250);
  }
}

// ─── Поиск товара: href, name, boxQty, короб ────────────────────────────────
function findProductByArticle_(article) {
  var r = msFetch_('GET', '/entity/product?filter=article=' + encodeURIComponent(article), null);
  if (!r.ok || !r.body.rows || !r.body.rows.length) return null;
  var p = r.body.rows[0];

  // boxQty = 0 означает «в карточке не заполнено» — processRow_ это поймает и не наплодит коробов.
  var boxQty = 0, boxValue = null;
  (p.attributes || []).forEach(function (a) {
    if (a.id === P_ATTR_BOXQTY && a.value) boxQty = parseInt(a.value, 10) || 0;
    if (a.id === P_ATTR_BOX && a.value && a.value.meta) boxValue = { meta: a.value.meta };
  });
  return { href: p.meta.href, name: p.name, boxQty: boxQty, boxValue: boxValue };
}

// ─── Чтение Матрицы (лист «База»): артикул → {boxQty, boxType} ───────────────
// Кэшируем за один проход: один раз читаем лист на всю обработку.
var _matrixCache = null;
function getMatrixInfo_(article) {
  if (_matrixCache === null) {
    _matrixCache = {};
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName(MATRIX_SHEET);
    if (sh) {
      var last = sh.getLastRow();
      if (last >= FIRST_DATA_ROW) {
        var vals = sh.getRange(FIRST_DATA_ROW, 1, last - FIRST_DATA_ROW + 1, MATRIX_COL.BOX_QTY)
                     .getDisplayValues();
        for (var i = 0; i < vals.length; i++) {
          var art = String(vals[i][MATRIX_COL.ARTICLE - 1] || '').trim();
          if (!art) continue;
          var qty = parseInt(String(vals[i][MATRIX_COL.BOX_QTY - 1] || '').replace(/[^\d]/g, ''), 10) || 0;
          var boxType = String(vals[i][MATRIX_COL.BOX_TYPE - 1] || '').toUpperCase().trim();
          _matrixCache[art] = { boxQty: qty, boxType: boxType };
        }
      }
    }
  }
  return _matrixCache[String(article).trim()] || null;
}

// ─── Создание отгрузки ──────────────────────────────────────────────────────
function createDemand_(o) {
  var boxValue = o.product.boxValue || { meta: {
    href: BOX_S_HREF, type: 'customentity', mediaType: 'application/json',
  }};

  // name НЕ задаём — номер отгрузки присваивает сама МойСклад (по порядку).
  // Описание позиции кладём в доп-поле «Наименование» = «артикул — наименование».
  var posName = o.article + ' — ' + o.posName + (o.boxLabel || '');

  var payload = {
    moment: Utilities.formatDate(new Date(), 'Europe/Moscow', 'yyyy-MM-dd HH:mm:ss.SSS'),
    applicable: false,  // документ НЕ проведён (черновик) — не списывает остаток
    state: { meta: { href: STATE_NEW_HREF, type: 'state', mediaType: 'application/json' } },
    organization: { meta: { href: orgHref_(o.shop), type: 'organization', mediaType: 'application/json' } },
    agent: { meta: { href: AGENT_HREF, type: 'counterparty', mediaType: 'application/json' } },
    store: { meta: { href: STORE_HREF, type: 'store', mediaType: 'application/json' } },
    positions: [{
      quantity: o.quantity,
      assortment: { meta: { href: o.product.href, type: 'product', mediaType: 'application/json' } },
    }],
    attributes: [
      dAttr_(D_ATTR.BOX,  boxValue),     // Тип короба (обязательный)
      dAttr_(D_ATTR.NAME, posName),      // Наименование = «артикул — наименование»
      dAttr_(D_ATTR.KITS, o.quantity),   // Комплектов (= кол-во в коробе)
    ],
  };

  var r = msFetch_('POST', '/entity/demand', payload);
  if (r.ok) return r.body.id;
  Logger.log('createDemand ошибка: ' + r.error);
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName('Errors') || ss.insertSheet('Errors');
    sh.appendRow([new Date(), 'API', 'createDemand: ' + r.error]);
  } catch (e) {}
  return null;
}

function dAttr_(attrId, value) {
  return {
    meta: {
      href: MS_BASE + '/entity/demand/metadata/attributes/' + attrId,
      type: 'attributemetadata', mediaType: 'application/json',
    },
    value: value,
  };
}

function orgHref_(shop) {
  var s = String(shop || '').toLowerCase();
  if (s.indexOf('grip') >= 0 || s.indexOf('грип') >= 0) return ORG.GRIPON;
  return ORG.RUSBREND;
}

// ─── HTTP с ретраями ────────────────────────────────────────────────────────
function msFetch_(method, path, payload) {
  var opt = {
    method: method.toLowerCase(),
    headers: { 'Authorization': 'Bearer ' + getToken_() },
    contentType: 'application/json',
    muteHttpExceptions: true,
  };
  if (payload) opt.payload = JSON.stringify(payload);
  for (var a = 1; a <= RETRIES; a++) {
    try {
      var resp = UrlFetchApp.fetch(MS_BASE + path, opt);
      var code = resp.getResponseCode();
      var text = resp.getContentText();
      if (code >= 200 && code < 300) return { ok: true, body: text ? JSON.parse(text) : {}, error: '' };
      return { ok: false, body: null, error: 'HTTP ' + code + ': ' + text.substring(0, 250) };
    } catch (ex) {
      if (a < RETRIES) Utilities.sleep(1500 * a);
      else return { ok: false, body: null, error: 'сеть: ' + ex.message };
    }
  }
  return { ok: false, body: null, error: 'неизвестная ошибка' };
}

// ─── Лог ошибок + Telegram ──────────────────────────────────────────────────
function logError_(ss, row, message) {
  var sh = ss.getSheetByName('Errors') || ss.insertSheet('Errors');
  sh.appendRow([new Date(), row, message]);
  Logger.log('Строка ' + row + ': ' + message);
  sendTelegram_('⚠️ RUSbrend отгрузки\nСтрока ' + row + ': ' + message);
}

function sendTelegram_(text) {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('TG_BOT_TOKEN');
  var chat = props.getProperty('TG_CHAT_ID');
  if (!token || !chat) return;
  try {
    UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
      method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ chat_id: chat, text: text }), muteHttpExceptions: true,
    });
  } catch (e) {}
}
