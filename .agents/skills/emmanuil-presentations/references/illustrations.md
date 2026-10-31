# Иллюстрации

Для новых изображений используй доступный навык ImageGen. Фон слайдов, логотип и буквы
берутся из Figma. Картинки генерируются отдельно, текст накладывается в вёрстке.

Общая часть использованных промптов:

```text
Use case: historical-scene. Create a polished contemporary painterly biblical illustration for a church conference presentation.
[SCENE]
Landscape 3:2 composition. Sophisticated editorial oil painting with soft visible brushwork and believable anatomy, no cartoon, no glossy fantasy. Deep teal green #053c3a shadows, muted olive, warm sand and soft ivory light. Quiet emotionally credible scene, generous atmospheric space, no text, no letters, no logos, no frames. Compose important subjects centrally with safe space around them.
```

Зелёные тени относятся к иллюстрациям, фон слайдов остаётся светлым.
Подстановки SCENE:

**shepherds.png**

```text
Two groups of ancient Hebrew shepherds with sheep on opposing sides of a dry hillside, a restrained disagreement through gestures, no violence, not Abraham and Lot themselves, distant olive hills.
```

**wells.png**

```text
Ancient Canaan stone water well stopped with earth, abandoned rope and clay vessel, contemplative empty landscape, olive hills. No people.
```

**abigail.png**

```text
Abigail, a dignified ancient Hebrew woman in ivory and muted green robe, bowing respectfully before David, a bearded young ancient Hebrew leader in simple bronze-age clothing with a sheathed sword, a donkey carrying baskets of bread and dried fruit behind her. Peaceful mediation, no Roman armor, no crowns, no violence.
```

**father.png**

```text
An elderly ancient Hebrew father in a muted ochre robe embracing his returning adult son in worn cream linen, tender dignified reconciliation, natural hands, close medium view, no other people.
```

Отдельный использованный промпт **preparations.png**:

```text
Historical-scene illustration for church presentation. Ancient Hebrew woman Abigail in an ivory headscarf and muted teal robe preparing baskets of bread, raisins, dried figs, grain and closed wine skins loaded on a donkey in a stone courtyard. Dignified peaceful capable woman, no other prominent people. Contemporary editorial oil painting, natural face and hands, soft visible brushwork, deep teal #053c3a shadows, muted olive and warm sand, soft ivory sunlight, landscape 3:2. No text, logos or frames, not cartoon, no Roman dress. Focus on preparations, no meeting or conflict.
```

Проверяй:

- Пастухи не превращаются в дерущихся Авраама и Лота.
- Колодцы Быт. 26 не смешиваются с Быт. 13.
- Давиду отказал Навал; Авигея действует для примирения.
- В сцене встречи правдоподобные руки, лица, ножны, нет римской формы.
- Отец и сын — современная интерпретация, не оригинальная картина из вводных.
- После кадрирования важные лица и действие остаются видны.

В `functions.exec` результат генерации сохраняй через `store`, выводи только `output_hint`,
для показа — `generatedImage`. Не печатай полный объект через `text`: в нём может быть
миллионы символов base64. Для проекта копируй итоговый файл в папку задачи.
