function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Фулфилмент")
    .addItem("Создать товары и заказ поставщика", "runIntake")
    .addItem("Обновить клиентов из МойСклад", "runAgents")
    .addToUi();
}

function props_() {
  var p = PropertiesService.getScriptProperties();
  var url = p.getProperty("FF_WEB_URL");
  var token = p.getProperty("FF_WEB_TOKEN");
  if (!url || !token) {
    throw new Error("Заполни свойства скрипта FF_WEB_URL и FF_WEB_TOKEN");
  }
  return { url: url.replace(/\/$/, ""), token: token };
}

function post_(path) {
  var cfg = props_();
  var res = UrlFetchApp.fetch(cfg.url + path, {
    method: "post",
    headers: { "X-FF-Token": cfg.token },
    muteHttpExceptions: true,
  });
  var text = res.getContentText() || "";
  SpreadsheetApp.getActive().toast(text.slice(0, 250), "Фулфилмент", 8);
  return text;
}

function runIntake() {
  post_("/run/intake");
}

function runAgents() {
  post_("/run/agents");
}
