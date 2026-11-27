# JetVideo 1.1.2 · тег v1.1.2

Обе переносимые сборки содержат JetVideo.exe, FFmpeg и FFprobe, встроенную справку и локализованные README. При запуске программа создаёт папку JetVideo Data рядом с EXE. Пустой образец .env находится в `.env.example`.

## Сборка и упаковка

Релиз собирается на Windows x64 с CPython 3.14.10 и Tkinter. Запустите `build_windows.bat` или установите `requirements-build.lock` и выполните `python -m PyInstaller --noconfirm --clean JetVideo.spec`. Для FFmpeg используйте [инструкцию сборки из соответствующих исходников](../build_tools/ffmpeg/README.md).

```powershell
python package_release.py --ffmpeg-directory 'path/to/tools' --ffmpeg-source 'path/to/ffmpeg-source.zip' --whisper-directory 'path/to/whisper' --dependency-sources 'path/to/dependency-sources.zip'
```

Выходная папка по умолчанию — `release/v1.1.2`. Ключ `--executable` позволяет указать EXE, собранный в отдельном каталоге. Упаковщик сверяет SHA‑256 FFmpeg и FFprobe с манифестом в архиве исходников. В пользовательский ZIP входят локальные Whisper Base и фильтр Silero VAD с их лицензиями.

### Полная сборка

1. Сверьте SHA-256 опубликованного лёгкого ZIP по `SHA256SUMS.txt` и распакуйте его в новый временный каталог.
2. Добавьте в `tools/whisper` файлы `ggml-small.bin`, `ggml-large-v3.bin`, `PROVENANCE-Small.txt` и `PROVENANCE-Large-v3.txt`. Сверьте контрольные суммы весов с файлами `PROVENANCE-*.txt` и сохраните комплектную лицензию `LICENSE-Whisper-model.txt`. [Веса Small](https://huggingface.co/ggerganov/whisper.cpp/blob/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small.bin) · [Веса Large v3](https://huggingface.co/ggerganov/whisper.cpp/blob/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3.bin)
3. Обновите внешние README и уведомления о лицензиях из документации полной сборки. Обе сборки используют тот же `JetVideo.exe` и те же исходники зависимостей.
4. Из временного каталога создайте архив с томами по 1900 МиБ и запишите SHA-256 обоих файлов в `SHA256SUMS-Full.txt`. [Лимит на файлы GitHub Release](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)

```powershell
7z a -t7z -mx=1 -ms=off -v1900m '..\JetVideo-1.1.2-Windows-x64-Full.7z' '.\*'
7z t '..\JetVideo-1.1.2-Windows-x64-Full.7z.001'
```

## Комплект GitHub Release

- `JetVideo-1.1.2-Windows-x64.zip` с лёгкой сборкой и Whisper Base.
- `JetVideo-1.1.2-Windows-x64-Full.7z.001` и `.002` с полной сборкой и моделями Base, Small и Large v3.
- `JetVideo-1.1.2-source.zip` с исходниками приложения и скриптами сборки.
- `FFmpeg-JetVideo-v1.1.2-source.zip` с соответствующими исходниками FFmpeg и сборочными материалами.
- `JetVideo-1.1.2-dependency-sources.zip` с исходниками зависимостей, включая Whisper и инструкции по его сборке.
- `SHA256SUMS.txt` с контрольными суммами лёгкого ZIP и архивов исходников.
- `SHA256SUMS-Full.txt` с контрольными суммами томов полной сборки.
- [Описание выпуска](RELEASE_NOTES.md).

Пользователь скачивает либо лёгкий ZIP, либо оба тома полной сборки. Исходные архивы предоставляются отдельно. Для публикации репозитория берём только содержимое исходного ZIP. Рабочие файлы пользователей хранятся локально.

## Проверка

Запустите тесты с комплектными FFmpeg и FFprobe. Распакуйте проверяемую сборку в новый каталог и запустите проверку из другого рабочего каталога с PATH, ограниченным системными папками Windows. Режим `JetVideo.exe --portable-test <отчёт.json>` проверяет первый запуск, встроенный Python, справку и нарезку с PNG-баннером на синтетическом видео.

Buffer, Cloudflare R2 и TikTok настраиваются ключами и каналом пользователя. Тесты релиза используют пустой профиль и не публикуют видео от имени пользователя.
