# Mock-экраны детейлинга

Исходник четырёх экранов для презентации. С 2026-09-21 **названия услуг и суммы карточек** взяты из присланного прайса ([`../../02_Знание/ПРАЙС.md`](../../02_Знание/ПРАЙС.md)). Объёмы колонок, KPI дашборда и имена клиентов по-прежнему выдуманные. Реальных показателей салона DIVO Motors здесь нет и быть не должно.

| Экран | Что показывает | Куда идёт |
|---|---|---|
| `mock.html?s=1` | Воронка детейлинга, семь этапов | слайд 3 |
| `mock.html?s=2` | Карточка машины, история обращения, признак клиента DIVO Motors | слайд 7 |
| `mock.html?s=3` | Панель показателей: источник оплаченного заказа, причины отказа | слайд 7 |
| `mock.html?s=4` | Загрузка площадки: мастера, объём, очередь к назначению, подмена | слайд 8 |

## Как пересобрать скрины

Chrome на машине нет, снимаем Edge на Chromium. Высота кадра подобрана так, чтобы контент не обрезался посередине карточки.

```bash
cd "2. БИЗНЕС/04_Производство/Лиды/DIVO_Detailing/05_Материалы/mock"
EDGE="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
for p in "1:620" "2:800" "3:680" "4:880"; do
  i="${p%%:*}"; h="${p##*:}"
  "$EDGE" --headless=new --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=2 --window-size=1400,$h \
    --virtual-time-budget=3000 --screenshot="shot-$i.png" \
    "file://$(pwd)/mock.html?s=$i"
done
python3 -c "
from PIL import Image
for i in (1,2,3,4):
    Image.open(f'shot-{i}.png').convert('RGB').save(f'../presentation/assets/shot-{i}.jpg', quality=88, optimize=True)
"
```

После замены скринов заново прогнать `embed_presentation.py` на презентации, иначе в офлайн-файле останутся старые картинки.
