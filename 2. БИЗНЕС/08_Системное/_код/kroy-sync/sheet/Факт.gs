/**
 * Лист факта кроя. Закройщик ставит галочку в «Провести».
 * Пока свойство APPLY не равно 1, в МойСклад ничего не уходит.
 * Скрипт не ходит на Wildberries и Ozon.
 *
 * Свойства скрипта: MS_TOKEN, MS_ORG_ID, MS_STORE_ID, APPLY.
 */

var HEADERS = ["Дата", "Изделие", "Цвет", "S", "M", "L", "Рулоны", "Провести", "Статус"];

function setup() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  sheet.setName("Факт");
}

function onEdit(e) {
  if (!e || !e.range) return;
  var sheet = e.range.getSheet();
  if (sheet.getName() !== "Факт") return;
  if (e.range.getColumn() !== 8 || e.range.getRow() < 2) return;
  if (e.value !== "TRUE") return;
  conductRow_(sheet, e.range.getRow());
}

function conductMarked() {
  var sheet = SpreadsheetApp.getActive().getSheetByName("Факт");
  var last = sheet.getLastRow();
  for (var row = 2; row <= last; row++) {
    if (sheet.getRange(row, 8).getValue() === true) conductRow_(sheet, row);
  }
}

function conductRow_(sheet, row) {
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty("APPLY") !== "1") {
    sheet.getRange(row, 9).setValue("режим выключен, в МойСклад не отправлено");
    return;
  }
  sheet.getRange(row, 9).setValue("проведение включает только отдельный запуск с APPLY=1");
}
