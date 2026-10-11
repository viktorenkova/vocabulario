# Источники и условия использования

Готовый `dictionary.jsonl` — адаптированный учебный набор: фильтрация, объединение источников, ограничение примеров, добавление идентификаторов и тематических меток. Это не новый словарь, созданный с нуля. Точные версии и хеши находятся в `sources.json`.

| Компонент | Автор / источник | Лицензия и атрибуция |
|---|---|---|
| Английские определения | Авторы Wiktionary, выгрузка David Doozan [spanish_data](https://github.com/doozan/spanish_data), `es-en.data` | Upstream указывает CC BY-SA. Производная текстовая часть распространяется под [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); исходная страница и её история доступны по `source` каждой карточки. Сохраните ссылки и указание адаптации. |
| Частотность | David Doozan; исходная частотная база [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords), `frequency.csv` | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). `rank` получен из порядка исходного файла. |
| Примеры и переводы | Авторы [Tatoeba](https://tatoeba.org), через `sentences.tsv` David Doozan | [CC BY 2.0 France](https://creativecommons.org/licenses/by/2.0/fr/). В каждом примере сохранены имена авторов, номера предложений и ссылка. См. [условия Tatoeba](https://tatoeba.org/en/terms_of_use). |
| IPA испанского Испании | [open-dict-data/ipa-dict](https://github.com/open-dict-data/ipa-dict), dohliam, `data/es_ES.txt` | MIT, полный текст [licenses/ipa.txt](licenses/ipa.txt). Испанская часть экспериментальная, автоматически построена по правилам Timur Baytukalov. |
| Спряжения | Fred Jehle; компиляция Brian Ghidinelli, [fred-jehle-spanish-verbs](https://github.com/ghidinelli/fred-jehle-spanish-verbs) | **[CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/)**. Полное уведомление: [licenses/verbs.txt](licenses/verbs.txt). Только некоммерческое использование; производные таблицы сохраняют эту лицензию. |
| Автоматические тематические метки | WordNet 3.0, Princeton University; копия корпуса [nltk/nltk_data](https://github.com/nltk/nltk_data) | [WordNet License](https://wordnet.princeton.edu/license-and-commercial-use), полный текст [licenses/wordnet.txt](licenses/wordnet.txt). Использован первый synset английского перевода для приблизительной группировки. |
| Пиктограммы | Созданы специально для Vocabulario, `scripts/build_images.py` | Собственные геометрические рисунки; внешние фотографии, шрифты и изображения не использованы. |

Лицензия верхнего уровня `doozan/spanish_data` не заменяет раздельные лицензии исходных Wiktionary, Tatoeba и частотного списка. Объединение в JSONL не отменяет условий отдельных полей. Текущая комплектация со спряжениями пригодна для личного некоммерческого обучения; не распространяйте её как коммерческий словарный продукт без пересмотра компонента NC.

Произношение в боте синтезируется онлайн и кешируется отдельно. Библиотека [edge-tts](https://github.com/rany2/edge-tts) обеспечивает доступ к сервису; её открытая лицензия не является отдельной лицензией на любые услуги Microsoft. Аудиозаписи Tatoeba, Forvo и Wiktionary в поставку не включены.
