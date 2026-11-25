# JetVideo 1.1.0 · тег v1.1.0

Portable ZIP содержит JetVideo.exe, FFmpeg и FFprobe, встроенную справку и локализованные README. При запуске этой копии программа создаёт собственную папку JetVideo Data рядом с EXE. Пустой образец .env находится в `.env.example`.

## Сборка и упаковка

Релиз собирается на Windows x64 с CPython 3.14.10 и Tkinter. Запустите `build_windows.bat` или установите `requirements-build.lock` и выполните `python -m PyInstaller --noconfirm --clean JetVideo.spec`. Для FFmpeg используйте [инструкцию сборки из соответствующих исходников](../build_tools/ffmpeg/README.md).

```powershell
python package_release.py --ffmpeg-directory 'path/to/tools' --ffmpeg-source 'path/to/ffmpeg-source.zip' --whisper-directory 'path/to/whisper' --dependency-sources 'path/to/dependency-sources.zip'
```

Выходная папка по умолчанию — `release/v1.1.0`. Ключ `--executable` позволяет указать EXE, собранный в отдельном каталоге. Упаковщик сверяет SHA‑256 FFmpeg и FFprobe с манифестом в архиве исходников и добавляет в пользовательский ZIP локальный Whisper, обе модели и их лицензии.

## Комплект GitHub Release

- `JetVideo-1.1.0-Windows-x64.zip` с переносимой программой.
- `JetVideo-1.1.0-source.zip` с исходниками приложения и скриптами сборки.
- `FFmpeg-JetVideo-v1.1.0-source.zip` с соответствующими исходниками FFmpeg и сборочными материалами.
- `JetVideo-1.1.0-dependency-sources.zip` с исходниками зависимостей, включая Whisper и инструкции по его сборке.
- `SHA256SUMS.txt` с контрольными суммами.
- [Описание выпуска](RELEASE_NOTES.md).

Пользователь скачивает Windows ZIP. Исходные архивы предоставляются отдельными файлами рядом с ним. Для публикации репозитория берем только содержимое исходного ZIP. Рабочие файлы пользователей хранятся локально.

## Проверка

Запустите тесты с комплектными FFmpeg и FFprobe. Затем распакуйте итоговый Windows ZIP в новый каталог и запустите проверку из другого рабочего каталога с PATH, ограниченным системными папками Windows. Режим `JetVideo.exe --portable-test <отчёт.json>` проверяет первый запуск, встроенный Python, справку и нарезку с PNG-баннером на синтетическом видео.

Buffer, Cloudflare R2 и TikTok настраиваются ключами и каналом пользователя. Тесты релиза используют пустой профиль и не публикуют видео от имени пользователя.
