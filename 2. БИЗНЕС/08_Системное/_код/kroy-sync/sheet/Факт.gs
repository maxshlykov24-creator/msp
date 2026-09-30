/**
 * Лист «Факт». Галочка в «Провести» списывает рулон и приходует размеры в МойСклад.
 * Пока свойство APPLY не равно 1, строка только помечается и наружу не уходит.
 * Wildberries и Ozon этот скрипт не вызывает.
 *
 * Свойства: MS_TOKEN, MS_ORG_ID, MS_STORE_ID, APPLY.
 * Ключ строки совпадает с cut.py: JSON с пробелами и порядком ключей d, p, r, s.
 */

var HEADERS = ["Дата", "Изделие", "Цвет", "S", "M", "L", "Рулоны", "Провести", "Статус"];
var MS = "https://api.moysklad.ru/api/remap/1.2";

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
  var values = sheet.getRange(row, 1, 1, 7).getValues()[0];
  var date = formatDate_(values[0]);
  var product = String(values[1] || "").trim();
  var color = String(values[2] || "").trim();
  var sizes = {S: num_(values[3]), M: num_(values[4]), L: num_(values[5])};
  var rolls = num_(values[6]);
  var expected = product.split(" — ")[1] || "";
  if (!expected || expected !== color) {
    sheet.getRange(row, 9).setValue("цвет не совпадает с изделием");
    return;
  }
  if (rolls < 1 || sizes.S + sizes.M + sizes.L < 1) {
    sheet.getRange(row, 9).setValue("нужны штуки и рулоны");
    return;
  }
  var key = factKey_(date, product, sizes, rolls);
  var lossCode = "kroy-" + key + "-loss";
  var enterCode = "kroy-" + key + "-enter";
  if (findDoc_("loss", lossCode) && findDoc_("enter", enterCode)) {
    sheet.getRange(row, 9).setValue("уже проведено");
    return;
  }
  var token = props.getProperty("MS_TOKEN");
  var productRow = findBy_("product", "name", product);
  var roll = findBy_("product", "article", rollArticle_(color));
  if (!productRow || !roll) {
    sheet.getRange(row, 9).setValue("нет изделия или рулона");
    return;
  }
  var stock = stockOf_(props.getProperty("MS_STORE_ID"), roll.id);
  if (stock === null) stock = 0;
  if (stock < rolls) {
    sheet.getRange(row, 9).setValue("рулонов не хватает");
    return;
  }
  var positions = sizePositions_(productRow.id, sizes);
  if (!positions) {
    sheet.getRange(row, 9).setValue("нет размера");
    return;
  }
  var org = meta_("organization", props.getProperty("MS_ORG_ID"));
  var store = meta_("store", props.getProperty("MS_STORE_ID"));
  var moment = date + " 12:00:00";
  var loss = findDoc_("loss", lossCode);
  if (!loss) {
    loss = ms_("post", "/entity/loss", {
      organization: {meta: org},
      store: {meta: store},
      externalCode: lossCode,
      moment: moment,
      description: "Факт кроя " + product,
      positions: [{quantity: rolls, assortment: {meta: meta_("product", roll.id)}}]
    });
  }
  try {
    if (!findDoc_("enter", enterCode)) {
      ms_("post", "/entity/enter", {
        organization: {meta: org},
        store: {meta: store},
        externalCode: enterCode,
        moment: moment,
        description: "Факт кроя " + product,
        positions: positions
      });
    }
  } catch (err) {
    if (loss && loss.id) ms_("delete", "/entity/loss/" + loss.id, null);
    sheet.getRange(row, 9).setValue("откат: " + err.message);
    return;
  }
  sheet.getRange(row, 9).setValue("проведено " + key);
}

function factKey_(date, product, sizes, rolls) {
  var raw = '{"d": "' + date + '", "p": "' + product + '", "r": ' + rolls
    + ', "s": {"L": ' + sizes.L + ', "M": ' + sizes.M + ', "S": ' + sizes.S + '}}';
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_1, raw, Utilities.Charset.UTF_8);
  var hex = bytes.map(function (b) {
    var v = (b + 256) % 256;
    return (v < 16 ? "0" : "") + v.toString(16);
  }).join("");
  return hex.substring(0, 16);
}

function rollArticle_(color) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_1, color.toLowerCase(), Utilities.Charset.UTF_8);
  var hex = bytes.map(function (b) {
    var v = (b + 256) % 256;
    return (v < 16 ? "0" : "") + v.toString(16);
  }).join("");
  return "ROL-" + hex.substring(0, 8);
}

function formatDate_(value) {
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, "Europe/Moscow", "yyyy-MM-dd");
  }
  return String(value || "").trim();
}

function num_(value) {
  if (value === "" || value === null) return 0;
  return parseInt(value, 10) || 0;
}

function meta_(kind, id) {
  return {href: MS + "/entity/" + kind + "/" + id, type: kind, mediaType: "application/json"};
}

function ms_(method, path, body) {
  var token = PropertiesService.getScriptProperties().getProperty("MS_TOKEN");
  var params = {
    method: method,
    headers: {Authorization: "Bearer " + token, Accept: "application/json"},
    muteHttpExceptions: true
  };
  if (body) {
    params.contentType = "application/json";
    params.payload = JSON.stringify(body);
  }
  var response = UrlFetchApp.fetch(MS + path, params);
  var code = response.getResponseCode();
  if (code >= 300) throw new Error("МойСклад " + code);
  var text = response.getContentText() || "{}";
  return JSON.parse(text);
}

function findBy_(kind, field, value) {
  var data = ms_("get", "/entity/" + kind + "?limit=100&filter=" + encodeURIComponent(field + "=" + value), null);
  var list = data.rows || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i][field] === value) return list[i];
  }
  return null;
}

function findDoc_(kind, code) {
  return findBy_(kind, "externalCode", code);
}

function stockOf_(storeId, assortmentId) {
  var href = MS + "/entity/store/" + storeId;
  var data = ms_("get", "/report/stock/all?limit=1000&filter=" + encodeURIComponent("store=" + href), null);
  var list = data.rows || [];
  for (var i = 0; i < list.length; i++) {
    var link = (list[i].meta && list[i].meta.href) || "";
    if (link.indexOf(assortmentId) !== -1) return Number(list[i].stock);
  }
  return null;
}

function sizePositions_(productId, sizes) {
  var data = ms_("get", "/entity/variant?limit=100&filter=" + encodeURIComponent("productid=" + productId), null);
  var bySize = {};
  (data.rows || []).forEach(function (variant) {
    (variant.characteristics || []).forEach(function (char) {
      if (char.name === "Размер") bySize[char.value] = variant.id;
    });
  });
  var positions = [];
  ["S", "M", "L"].forEach(function (size) {
    if (!sizes[size]) return;
    if (!bySize[size]) {
      positions = null;
      return;
    }
    if (!positions) return;
    positions.push({quantity: sizes[size], assortment: {meta: meta_("variant", bySize[size])}});
  });
  return positions;
}
