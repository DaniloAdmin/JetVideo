# JetVideo 1.0.0 · тег v4.3.0

Portable ZIP содержит JetVideo.exe, FFmpeg и FFprobe, встроенную справку и локализованные README. При запуске этой копии программа создаёт собственную папку JetVideo Data рядом с EXE. Пустой образец .env находится в `.env.example`.

## Сборка и упаковка

Релиз собирается на Windows x64 с CPython 3.14.10 и Tkinter. Запустите `build_windows.bat` или установите `requirements-build.lock` и выполните `python -m PyInstaller --noconfirm --clean JetVideo.spec`. Для FFmpeg используйте [инструкцию сборки из соответствующих исходников](../build_tools/ffmpeg/README.md).

```powershell
python package_release.py --ffmpeg-directory 'path/to/tools' --ffmpeg-source 'path/to/ffmpeg-source.zip' --dependency-sources 'path/to/dependency-sources.zip'
```

Выходная папка по умолчанию — `release/v4.3.0`. Ключ `--executable` позволяет передать EXE, собранный в отдельном каталоге. Упаковщик сверяет SHA‑256 обеих утилит с манифестом в архиве их исходников. В исходный ZIP попадают файлы по списку, включая обе серии иллюстраций.

## Комплект GitHub Release

- `JetVideo-1.0.0-Windows-x64.zip` для установки.
- `JetVideo-1.0.0-source.zip` с исходниками приложения и скриптами сборки.
- `FFmpeg-JetVideo-v4.3.0-source.zip` с соответствующими исходниками FFmpeg и сборочными материалами.
- `JetVideo-1.0.0-dependency-sources.zip` с исходниками остальных распространяемых компонентов.
- `SHA256SUMS.txt` с контрольными суммами.
- [Описание выпуска](RELEASE_NOTES.md).

Пользователь скачивает Windows ZIP. Исходные архивы предоставляются отдельными файлами рядом с ним. Для публикации репозитория берем только содержимое исходного ZIP. Рабочие файлы пользователей хранятся локально.

## Проверка

Запустите тесты с комплектными FFmpeg и FFprobe. Затем распакуйте итоговый Windows ZIP в новый каталог и запустите проверку из другого рабочего каталога с PATH, ограниченным системными папками Windows. Режим `JetVideo.exe --portable-test <отчёт.json>` проверяет первый запуск, встроенный Python, справку и нарезку с PNG-баннером на синтетическом видео.

Buffer, Cloudflare R2 и TikTok настраиваются ключами и каналом пользователя. Тесты релиза используют пустой профиль и не публикуют видео от имени пользователя.
