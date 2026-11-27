# Сторонние компоненты

Код JetVideo распространяется по GPLv3. Для сторонних компонентов действуют их собственные лицензии и уведомления. [Лицензия JetVideo](LICENSE)

Версии Python-пакетов зафиксированы в `requirements.txt` и `requirements-build.lock`; сведения из установленного окружения — `third_party/manifest.json`, полные тексты лицензий и уведомлений — в соседних подкаталогах `third_party`. Их следует сохранять в исходном и бинарном дистрибутивах.

| Компоненты | Лицензия |
| --- | --- |
| boto3, botocore, s3transfer, requests, tzdata | Apache-2.0 (см. индивидуальные NOTICE) |
| python-dotenv | BSD-3-Clause |
| python-dateutil | Apache-2.0 / BSD-3-Clause |
| urllib3, idna, charset-normalizer, six, jmespath | MIT / BSD. Точные условия в соответствующих файлах. |
| certifi | MPL-2.0 для пакета сертификатов |
| Pillow | MIT-CMU. Лицензии включённых библиотек, в том числе FreeType, приведены в `third_party/pillow/LICENSE`. |
| Inter, Montserrat, Noto Sans, Roboto Condensed | SIL Open Font License 1.1. Лицензии и уведомления находятся в `third_party/fonts`. |
| CPython 3.14.10 | PSF и уведомления сторонних компонентов в `third_party/Python-LICENSE.txt` |
| Tcl/Tk 9.0.4 | Лицензии Tcl и Tk в `third_party` |
| PyInstaller | GPL с исключением для созданных приложений; отдельные файлы Apache-2.0 |
| Прочие инструменты сборки/тестирования | Индивидуальные лицензии в third_party и manifest.json |

В бинарник попадают только необходимые runtime-компоненты и загрузчик. Файлы лицензий инструментов сборки приведены для полноты; это не означает, что все инструменты входят в EXE. Официальное [исключение PyInstaller](https://pyinstaller.org/en/stable/license.html) позволяет выбрать собственную лицензию приложения при соблюдении условий его зависимостей.

## FFmpeg / FFprobe

JetVideo запускает комплектные FFmpeg и FFprobe 9.1 из папки `tools` отдельными процессами. Для релиза они собраны из исходников под GPL версии 3 или новее. Исходники, конфигурация и скрипт сборки поставляются архивом `FFmpeg-JetVideo-v1.1.2-source.zip`. [Уведомления FFmpeg](third_party/ffmpeg/NOTICE.md)

## Автосубтитры

Автосубтитры обрабатываются на компьютере отдельным процессом whisper.cpp b5907 с моделью Whisper Base. Режим «Только речь» использует Silero VAD v5.1.2. Лицензии MIT и сведения о точной сборке находятся рядом с файлами в `tools/whisper`. Исходники whisper.cpp и инструкции по сборке входят в `JetVideo-1.1.2-dependency-sources.zip`. [whisper.cpp](https://github.com/ggml-org/whisper.cpp/tree/b5907) · [OpenAI Whisper](https://github.com/openai/whisper) · [Silero VAD](https://github.com/snakers4/silero-vad/tree/v5.1.2)

Происхождение и контрольные суммы комплектных моделей указаны в `tools/whisper` рядом с файлами весов. Орфография проверяется библиотекой Spylls со словарями LibreOffice. [Источники и лицензии словарей и Spylls](third_party/spelling/NOTICE.md)

## Материалы и сервисы

В исходники не включены пользовательские видео, изображения баннеров, прежние скриншоты аккаунтов и материалы JoJo. Значок воспроизведения JetVideo создан из простых геометрических элементов специально для приложения. Перед распространением своих видео/баннеров пользователь должен иметь права на них.

Buffer, Cloudflare, TikTok и ACRCloud — независимые сервисы со своими условиями, тарифами и лимитами. Приложение не связано с ними официально и не обещает доступ к данным или функциям вне предоставленного ими API.
