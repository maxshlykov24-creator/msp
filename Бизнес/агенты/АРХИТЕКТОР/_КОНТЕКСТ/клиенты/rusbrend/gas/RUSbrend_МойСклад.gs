/**
 * ============================================================================
 *  RUSbrend × МойСклад — синхронизация таблицы и штрихкоды из PDF
 *  Версия 2.0 | Apps Script (один файл)
 *
 *  Возможности:
 *    - Флажок в столбце W («Загружено в МойСклад») = полная синхронизация строки:
 *        найти товар по артикулу → создать (если нет) / обновить (если есть)
 *        + проставить штрихкод (из столбца F или извлечь из PDF в столбце E).
 *    - Штрихкод из PDF: ссылка Drive в ячейке E → конвертация в Google Doc
 *      (берётся встроенный текст, при отсутствии — OCR) → regex + валидация.
 *    - Статус результата пишется в столбец X.
 *    - Меню «МойСклад»: пилот извлечения ШК, пилот синхронизации, массовый прогон.
 *
 *  Перед использованием: см. README_УСТАНОВКА.md (в этой же папке).
 * ============================================================================
 */

// ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────────────────
var MS_BASE   = 'https://api.moysklad.ru/api/remap/1.2';
var SHEET_NAME = 'База';           // вкладка с номенклатурой (поменяй при необходимости)
var REQUEST_TIMEOUT_RETRIES = 3;   // ретраи при сетевых сбоях
var BATCH_TIME_BUDGET_MS = 280000; // ~4.6 мин (лимит выполнения GAS = 6 мин)

// Колонки (1-based). Структура листа «База».
var COL = {
  SHOP:     1,   // A  Магазин
  PHOTO:    2,   // B  Фото
  ARTICLE:  3,   // C  Артикул
  NAME:     4,   // D  Наименование
  PDF:      5,   // E  ШК (Этикетка) — ссылка на PDF в Drive
  BARCODE:  6,   // F  Баркод (если заполнен — приоритет над PDF)
  BOX_TYPE: 7,   // G  Часто используемый короб (S/M/L)
  BOX_QTY:  8,   // H  Количество в выбранном коробе
  FIT_S:    10,  // J  Помещается в короб S
  FIT_M:    11,  // K  Помещается в короб M
  FIT_L:    12,  // L  Помещается в короб L
  PKG_QTY:  14,  // N  Количество в пакете
  PKG_SIZE: 17,  // Q  Размер пакета
  WEIGHT_G: 18,  // R  Вес, грамм
  COMMENT:  19,  // S  Комментарий
  BOX_CNT:  20,  // T  Короб, шт
  BOX_WT:   21,  // U  Вес короба, кг
  FLAG:     23,  // W  Загружено в МойСклад (флажок) — ТРИГГЕР
  STATUS:   24,  // X  Статус (заполняет скрипт)
};
var FIRST_DATA_ROW = 2;  // строка 1 — заголовок

// ─── ID атрибутов товара в МойСклад (из load_products.py) ────────────────────
var ATTR = {
  'Организация':                   'e881075b-4f9d-11f1-0a80-156000141799',
  'Помещается в короб S':          'e8810aa5-4f9d-11f1-0a80-15600014179a',
  'Помещается в короб M':          'e8810bb9-4f9d-11f1-0a80-15600014179b',
  'Помещается в короб L':          'e8810c9f-4f9d-11f1-0a80-15600014179c',
  'Часто используемый короб':      '5b730858-4fa4-11f1-0a80-1d2e0015579d',
  'Количество в выбранном коробе': 'e8810f2c-4f9d-11f1-0a80-15600014179f',
  'Количество в пакете':           'e881100a-4f9d-11f1-0a80-1560001417a0',
  'Размер пакета':                 'e8811110-4f9d-11f1-0a80-1560001417a1',
  'Вес г факт':                    'e88111fb-4f9d-11f1-0a80-1560001417a2',
  'Короб, шт':                     'e88112d5-4f9d-11f1-0a80-1560001417a3',
  'Вес короба, кг':                'e88113cc-4f9d-11f1-0a80-1560001417a4',
};

var CE_ORG = '06cc7a62-4f9d-11f1-0a80-075500139fb5'; // справочник «Организация»
var CE_BOX = '87b0e62a-4fa3-11f1-0a80-13470014140f'; // справочник «Короб»

var BOX_MAP = {
  'S': '106993b0-4fa4-11f1-0a80-07550014b96a',
  'M': '12b42bc3-4fa4-11f1-0a80-134700142471',
  'L': '1475f29d-4fa4-11f1-0a80-056700138e7e',
};

// Известные значения справочника «Организация». Остальные резолвятся динамически.
var ORG_MAP_STATIC = {
  'rusbrend': '2cff3b7d-52be-11f1-0a80-165a0038aa04',
  'русбренд': '2cff3b7d-52be-11f1-0a80-165a0038aa04',
};
var _orgMapCache = null;  // кэш «имя→id» из справочника

// ============================================================================
//  МЕНЮ И УСТАНОВКА
// ============================================================================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('МойСклад')
    .addItem('1. Пилот: извлечь ШК (5 строк, без записи)', 'pilotBarcodes')
    .addItem('2. Пилот: синхронизировать 5 строк', 'pilotSync5')
    .addSeparator()
    .addItem('3. Синхронизировать ОТМЕЧЕННЫЕ (флажок W)', 'syncCheckedRows')
    .addItem('4. Отметить уже загруженные (W=✓, без пересинка)', 'markExistingLoaded')
    .addItem('Снять флажки без статуса OK', 'resetFailedFlags')
    .addSeparator()
    .addItem('Установить триггер onEdit', 'setup')
    .addToUi();
}

/** Однократно: устанавливает onEdit-триггер. */
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'handleEdit') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('handleEdit').forSpreadsheet(ss).onEdit().create();
  SpreadsheetApp.getActive().toast('Триггер onEdit установлен.');
}

function getToken_() {
  var t = PropertiesService.getScriptProperties().getProperty('MS_TOKEN');
  if (!t) throw new Error('Не задан MS_TOKEN в Script Properties.');
  return t;
}

// ============================================================================
//  ОБРАБОТЧИК ФЛАЖКА (onEdit)
// ============================================================================

function handleEdit(e) {
  if (!e || !e.range) return;
  var sheet = e.range.getSheet();
  if (sheet.getName() !== SHEET_NAME) return;
  if (e.range.getColumn() !== COL.FLAG) return;
  if (e.range.getRow() < FIRST_DATA_ROW) return;
  if (e.value !== 'TRUE' && e.value !== true) return;

  var row = e.range.getRow();
  var res = syncRow_(sheet, row, false);
  writeStatus_(sheet, row, res);
}

// ============================================================================
//  ПИЛОТ
// ============================================================================

/** Извлекает ШК из первых 5 строк с PDF в столбце E. Пишет результат в столбец X. Без записи в МойСклад. */
function pilotBarcodes() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  var last = sheet.getLastRow();
  var done = 0;
  for (var row = FIRST_DATA_ROW; row <= last && done < 5; row++) {
    var article = String(sheet.getRange(row, COL.ARTICLE).getValue()).trim();
    if (!article) continue;
    var bc = resolveBarcode_(sheet, row);
    var msg = bc.ok ? ('ШК: ' + bc.value + ' (' + bc.type + ')') : ('ШК не получен: ' + bc.error);
    sheet.getRange(row, COL.STATUS).setValue('[ПИЛОТ] ' + msg);
    done++;
  }
  SpreadsheetApp.getActive().toast('Пилот извлечения ШК: обработано ' + done + ' строк. Смотри столбец X.');
}

/** Полная синхронизация первых 5 строк с артикулом. */
function pilotSync5() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  var last = sheet.getLastRow();
  var done = 0;
  for (var row = FIRST_DATA_ROW; row <= last && done < 5; row++) {
    var article = String(sheet.getRange(row, COL.ARTICLE).getValue()).trim();
    if (!article) continue;
    var res = syncRow_(sheet, row, false);
    writeStatus_(sheet, row, res);
    done++;
    Utilities.sleep(300);
  }
  SpreadsheetApp.getActive().toast('Пилот синхронизации: обработано ' + done + ' строк.');
}

// ============================================================================
//  МАССОВЫЙ ПРОГОН ПО ФЛАЖКУ W
// ============================================================================

/** Обрабатывает все строки с флажком W=TRUE, у которых статус ещё не OK. Чанками по времени. */
function syncCheckedRows() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  var last = sheet.getLastRow();
  var data = sheet.getRange(1, 1, last, COL.STATUS).getValues();
  var start = Date.now();
  var processed = 0, ok = 0, err = 0;

  for (var i = FIRST_DATA_ROW - 1; i < data.length; i++) {
    if (Date.now() - start > BATCH_TIME_BUDGET_MS) {
      SpreadsheetApp.getActive().toast('Время вышло. Обработано ' + processed + '. Запусти ещё раз для продолжения.');
      break;
    }
    var row = i + 1;
    var flag = data[i][COL.FLAG - 1];
    var status = String(data[i][COL.STATUS - 1] || '');
    if (flag !== true) continue;
    if (status.indexOf('OK') === 0) continue;  // уже синхронизировано

    var res = syncRow_(sheet, row, false);
    writeStatus_(sheet, row, res);
    processed++;
    if (res.ok) ok++; else err++;
    Utilities.sleep(250);
  }
  SpreadsheetApp.getActive().toast('Готово. Обработано ' + processed + ' | OK ' + ok + ' | ошибок ' + err);
}

/**
 * Отмечает строки, чей артикул УЖЕ есть в МойСклад: ставит W=TRUE и X="OK · уже в МС".
 * Один GET всех артикулов из МС, БЕЗ вызова syncRow_ (без PDF/OCR) — быстро и безопасно.
 * Закрывает требование «проставить флажок у всех загруженных», не упираясь в лимит GAS.
 */
function markExistingLoaded() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) { SpreadsheetApp.getActive().toast('Нет вкладки ' + SHEET_NAME); return; }

  var existing = fetchAllArticles_();
  if (!existing) { SpreadsheetApp.getActive().toast('Не удалось получить артикулы из МС.'); return; }

  var last = sheet.getLastRow();
  var marked = 0, missing = 0;
  for (var row = FIRST_DATA_ROW; row <= last; row++) {
    var article = String(sheet.getRange(row, COL.ARTICLE).getDisplayValue() || '').trim();
    if (!article) continue;
    if (existing[article]) {
      sheet.getRange(row, COL.FLAG).setValue(true);
      sheet.getRange(row, COL.STATUS).setValue('OK · уже в МС');
      marked++;
    } else {
      missing++;
    }
  }
  SpreadsheetApp.getActive().toast('Отмечено: ' + marked + ' · нет в МС: ' + missing +
    '. Новые товары добавляй флажком W по одному (создастся через onEdit).');
}

/** Собирает множество (как объект-словарь) всех артикулов из МойСклад. @return {Object|null} */
function fetchAllArticles_() {
  var set = {};
  var offset = 0;
  while (true) {
    var r = msRequest_('GET', '/entity/product?limit=1000&offset=' + offset +
      '&fields=article', null);
    if (!r.ok) return offset === 0 ? null : set;
    var rows = (r.body && r.body.rows) || [];
    for (var i = 0; i < rows.length; i++) {
      var a = String(rows[i].article || '').trim();
      if (a) set[a] = true;
    }
    if (rows.length < 1000) break;
    offset += 1000;
  }
  return set;
}

/** Снимает флажок W у строк, где статус не OK (чтобы переотметить вручную). */
function resetFailedFlags() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  var last = sheet.getLastRow();
  var data = sheet.getRange(1, 1, last, COL.STATUS).getValues();
  var n = 0;
  for (var i = FIRST_DATA_ROW - 1; i < data.length; i++) {
    var flag = data[i][COL.FLAG - 1];
    var status = String(data[i][COL.STATUS - 1] || '');
    if (flag === true && status.indexOf('OK') !== 0) {
      sheet.getRange(i + 1, COL.FLAG).setValue(false);
      n++;
    }
  }
  SpreadsheetApp.getActive().toast('Снято флажков: ' + n);
}

// ============================================================================
//  СИНХРОНИЗАЦИЯ ОДНОЙ СТРОКИ
// ============================================================================

/**
 * Полная синхронизация строки в МойСклад.
 * @return {{ok:boolean, message:string, id:string}}
 */
function syncRow_(sheet, row, dryRun) {
  try {
    var vals = sheet.getRange(row, 1, 1, COL.STATUS).getValues()[0];

    // Артикул — через ОТОБРАЖАЕМОЕ значение, чтобы сохранить ведущие нули (напр. 00102)
    var article = String(sheet.getRange(row, COL.ARTICLE).getDisplayValue() || '').trim();
    var name    = String(vals[COL.NAME - 1] || '').trim();
    if (!article || !name) return { ok: false, message: 'нет артикула или наименования', id: '' };

    var existing = findProductByArticle_(article);

    // Защита от лишнего OCR: если товар уже есть и у него уже проставлен ШК,
    // не лезем в PDF/OCR при обновлении — обновляем только поля и атрибуты.
    var skipBarcode = !!(existing && existing.barcodes && existing.barcodes.length > 0);
    var payload = buildProductPayload_(sheet, row, vals, article, skipBarcode);

    var result;
    if (existing) {
      result = msRequest_('PUT', '/entity/product/' + existing.id, payload);
    } else {
      result = msRequest_('POST', '/entity/product', payload);
    }
    if (!result.ok) return { ok: false, message: result.error, id: '' };
    return { ok: true, message: existing ? 'обновлён' : 'создан', id: result.body.id };
  } catch (ex) {
    return { ok: false, message: 'исключение: ' + ex.message, id: '' };
  }
}

/** Собирает payload товара из строки. skipBarcode=true — не трогать ШК (без PDF/OCR). */
function buildProductPayload_(sheet, row, vals, article, skipBarcode) {
  var get = function (c) { return vals[c - 1]; };

  var shop    = String(get(COL.SHOP) || '').trim();
  var name    = String(get(COL.NAME) || '').trim();
  var comment = String(get(COL.COMMENT) || '').trim();
  var wtG     = toNum_(get(COL.WEIGHT_G));

  var payload = { name: name, article: article, code: article };
  if (comment) payload.description = comment;
  if (wtG && wtG > 0) payload.weight = Math.round(wtG / 1000 * 10000) / 10000;

  // штрихкод (пропускаем, если товар уже существует с ШК — экономим OCR-вызовы)
  if (!skipBarcode) {
    var bc = resolveBarcode_(sheet, row);
    if (bc.ok) {
      var obj = {};
      obj[bc.type] = bc.value;
      payload.barcodes = [obj];
    }
  }

  // атрибуты
  var attrs = [];
  pushAttr_(attrs, attrCustomEntity_('Организация', CE_ORG, resolveOrgId_(shop)));
  pushAttr_(attrs, attrLong_('Помещается в короб S', get(COL.FIT_S)));
  pushAttr_(attrs, attrLong_('Помещается в короб M', get(COL.FIT_M)));
  pushAttr_(attrs, attrLong_('Помещается в короб L', get(COL.FIT_L)));
  pushAttr_(attrs, attrCustomEntity_('Часто используемый короб', CE_BOX, resolveBoxId_(get(COL.BOX_TYPE))));
  pushAttr_(attrs, attrLong_('Количество в выбранном коробе', get(COL.BOX_QTY)));
  pushAttr_(attrs, attrLong_('Количество в пакете', get(COL.PKG_QTY)));
  pushAttr_(attrs, attrStr_('Размер пакета', get(COL.PKG_SIZE)));
  pushAttr_(attrs, attrStr_('Вес г факт', get(COL.WEIGHT_G)));
  pushAttr_(attrs, attrLong_('Короб, шт', get(COL.BOX_CNT)));
  pushAttr_(attrs, attrDouble_('Вес короба, кг', get(COL.BOX_WT)));
  if (attrs.length) payload.attributes = attrs;

  return payload;
}

function writeStatus_(sheet, row, res) {
  var text = res.ok ? ('OK · ' + res.message + (res.id ? ' · id=' + res.id : '')) : ('ОШИБКА · ' + res.message);
  sheet.getRange(row, COL.STATUS).setValue(text);
}

// ============================================================================
//  ШТРИХКОД: F (приоритет) или извлечение из PDF (E)
// ============================================================================

/** @return {{ok:boolean, type:string, value:string, error:string}} */
function resolveBarcode_(sheet, row) {
  var raw = sheet.getRange(row, COL.BARCODE).getValue();
  var parsed = parseBarcode_(raw);
  if (parsed) return { ok: true, type: parsed.type, value: parsed.value, error: '' };
  return extractBarcodeFromCellLink_(sheet, row, COL.PDF);
}

/** Разбор готового значения ШК из ячейки F. */
function parseBarcode_(raw) {
  if (raw === null || raw === undefined) return null;
  var v = String(raw).trim();
  if (!v) return null;
  if (/^OZN/i.test(v)) return { type: 'code128', value: v };
  // числовое (в т.ч. если ячейка хранит число)
  var digits = v.replace(/[^\d]/g, '');
  if (!digits) return { type: 'code128', value: v };
  if (digits.length === 13) return { type: 'ean13', value: digits };
  if (digits.length === 8)  return { type: 'ean8',  value: digits };
  return { type: 'code128', value: digits };
}

/** Извлечение ШК из PDF по ссылке Drive в ячейке. */
function extractBarcodeFromCellLink_(sheet, row, colE) {
  var range = sheet.getRange(row, colE);
  var url = null;

  // 1) обычная гиперссылка в rich text
  var rich = range.getRichTextValue();
  if (rich) {
    url = rich.getLinkUrl();
    if (!url) {
      var runs = rich.getRuns();
      for (var i = 0; i < runs.length && !url; i++) url = runs[i].getLinkUrl();
    }
  }
  // 2) =HYPERLINK("url";"text")
  if (!url) {
    var formula = range.getFormula();
    var mF = formula && formula.match(/HYPERLINK\(\s*"([^"]+)"/i);
    if (mF) url = mF[1];
  }
  if (!url) return { ok: false, type: '', value: '', error: 'нет ссылки на PDF в столбце E' };

  var idMatch = url.match(/[-\w]{25,}/);
  if (!idMatch) return { ok: false, type: '', value: '', error: 'не распознан id файла: ' + url };

  // Проход 1: встроенный текст PDF (без OCR) — точно, без ошибок распознавания.
  // Проход 2: OCR — только если в тексте ШК не нашёлся (PDF-картинка).
  for (var pass = 0; pass < 2; pass++) {
    var text;
    try {
      text = pdfToText_(idMatch[0], pass === 1);
    } catch (ex) {
      return { ok: false, type: '', value: '', error: 'PDF→текст: ' + ex.message };
    }
    var found = findBarcodeInText_(text);
    if (found) return { ok: true, type: found.type, value: found.value, error: '' };
  }
  return { ok: false, type: '', value: '', error: 'ШК в PDF не найден / контроль не сошёлся' };
}

/** Ищет ШК в тексте: сначала OZN (Code128), затем 13 цифр с верной контрольной суммой (EAN-13). */
function findBarcodeInText_(text) {
  if (!text) return null;
  var ozn = text.match(/OZN\d+/i);
  if (ozn) return { type: 'code128', value: ozn[0].toUpperCase() };
  var nums = text.match(/\d{13}/g) || [];
  for (var k = 0; k < nums.length; k++) {
    if (eanChecksumOk_(nums[k])) return { type: 'ean13', value: nums[k] };
  }
  return null;
}

/** Конвертирует PDF (Drive id) в Google Doc, возвращает текст, временный Doc удаляет. */
function pdfToText_(fileId, useOcr) {
  var blob = DriveApp.getFileById(fileId).getBlob();
  var resource = { title: 'tmp_bc_' + Date.now(), mimeType: 'application/vnd.google-apps.document' };
  var options = useOcr ? { ocr: true, ocrLanguage: 'ru' } : {};
  var inserted = Drive.Files.insert(resource, blob, options);
  var text = '';
  try {
    text = DocumentApp.openById(inserted.id).getBody().getText();
  } finally {
    try { DriveApp.getFileById(inserted.id).setTrashed(true); } catch (e) {}
  }
  return text;
}

function eanChecksumOk_(s) {
  if (!/^\d{13}$/.test(s)) return false;
  var sum = 0;
  for (var i = 0; i < 12; i++) sum += (+s[i]) * (i % 2 ? 3 : 1);
  return ((10 - (sum % 10)) % 10) === (+s[12]);
}

// ============================================================================
//  МОЙСКЛАД API
// ============================================================================

function findProductByArticle_(article) {
  var url = '/entity/product?filter=article=' + encodeURIComponent(article);
  var r = msRequest_('GET', url, null);
  if (!r.ok) return null;
  var rows = r.body.rows || [];
  return rows.length ? rows[0] : null;
}

/** Универсальный запрос с ретраями. @return {{ok:boolean, body:Object, error:string}} */
function msRequest_(method, path, payload) {
  var options = {
    method: method.toLowerCase(),
    headers: { 'Authorization': 'Bearer ' + getToken_() },
    muteHttpExceptions: true,
    contentType: 'application/json',
  };
  if (payload) options.payload = JSON.stringify(payload);

  for (var attempt = 1; attempt <= REQUEST_TIMEOUT_RETRIES; attempt++) {
    try {
      var resp = UrlFetchApp.fetch(MS_BASE + path, options);
      var code = resp.getResponseCode();
      var text = resp.getContentText();
      if (code >= 200 && code < 300) {
        return { ok: true, body: text ? JSON.parse(text) : {}, error: '' };
      }
      return { ok: false, body: null, error: 'HTTP ' + code + ': ' + text.substring(0, 250) };
    } catch (ex) {
      if (attempt < REQUEST_TIMEOUT_RETRIES) {
        Utilities.sleep(2000 * attempt);
      } else {
        return { ok: false, body: null, error: 'сеть: ' + ex.message };
      }
    }
  }
  return { ok: false, body: null, error: 'неизвестная ошибка' };
}

/** Разрешает id значения справочника «Организация» по имени магазина. */
function resolveOrgId_(shop) {
  if (!shop) return null;
  var key = shop.toLowerCase().trim();
  if (ORG_MAP_STATIC[key]) return ORG_MAP_STATIC[key];

  if (_orgMapCache === null) {
    _orgMapCache = {};
    var r = msRequest_('GET', '/entity/customentity/' + CE_ORG, null);
    if (r.ok && r.body.rows) {
      r.body.rows.forEach(function (row) {
        _orgMapCache[String(row.name).toLowerCase().trim()] = row.id;
      });
    }
  }
  if (_orgMapCache[key]) return _orgMapCache[key];
  // мягкое совпадение по началу (grip / рус)
  for (var nm in _orgMapCache) {
    if (key.indexOf(nm) === 0 || nm.indexOf(key) === 0) return _orgMapCache[nm];
  }
  return null;
}

function resolveBoxId_(v) {
  var s = String(v || '').toUpperCase().trim();
  return BOX_MAP[s] || null;
}

// ─── Конструкторы атрибутов ─────────────────────────────────────────────────

function attrMeta_(name) {
  return {
    href: MS_BASE + '/entity/product/metadata/attributes/' + ATTR[name],
    type: 'attributemetadata',
    mediaType: 'application/json',
  };
}

function attrLong_(name, value) {
  var n = toInt_(value);
  if (n === null) return null;
  return { meta: attrMeta_(name), value: n };
}

function attrDouble_(name, value) {
  var n = toNum_(value);
  if (n === null) return null;
  return { meta: attrMeta_(name), value: n };
}

function attrStr_(name, value) {
  var s = String(value === null || value === undefined ? '' : value).trim();
  if (!s) return null;
  return { meta: attrMeta_(name), value: s };
}

function attrCustomEntity_(name, ceId, itemId) {
  if (!itemId) return null;
  return {
    meta: attrMeta_(name),
    value: {
      meta: {
        href: MS_BASE + '/entity/customentity/' + ceId + '/' + itemId,
        type: 'customentity',
        mediaType: 'application/json',
      },
    },
  };
}

function pushAttr_(arr, a) { if (a) arr.push(a); }

// ─── Утилиты чисел ──────────────────────────────────────────────────────────

function toInt_(v) {
  if (v === null || v === undefined || v === '') return null;
  var s = String(v).replace(/\s|\u00a0/g, '').replace(',', '.');
  var n = parseFloat(s);
  return isNaN(n) ? null : Math.round(n);
}

function toNum_(v) {
  if (v === null || v === undefined || v === '') return null;
  var s = String(v).replace(/\s|\u00a0/g, '').replace(',', '.');
  var n = parseFloat(s);
  return isNaN(n) ? null : n;
}
