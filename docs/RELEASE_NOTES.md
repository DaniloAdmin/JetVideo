**[Download JetVideo 1.1.1 for Windows x64](https://github.com/DaniloAdmin/JetVideo/releases/download/v1.1.1/JetVideo-1.1.1-Windows-x64.zip).** Extract the ZIP and open `JetVideo.exe` on 64-bit Windows 10 or 11. This portable build includes FFmpeg, FFprobe, Whisper Base and the Silero speech filter. The Windows download contains everything needed for video editing and local subtitles.

JetVideo splits long videos into numbered clips, adds timed banners, and schedules TikTok posts through Buffer and Cloudflare R2. Editing and subtitles run locally. To publish, connect your TikTok channel in Buffer and enter the Buffer and R2 settings under **Publishing → Connections**.

## What's new

- **Subtitle editing.** Add phrases with their own timing, delete phrases, move them between adjacent time slots, and check Russian or Ukrainian spelling. Automatic subtitles keep each completed phrase on screen briefly so it is easier to read.
- **Optional music check.** Open **Publishing → Dashboard → More actions → Check music** to scan the ready folder through ACRCloud. When enabled for WATCH, a match or incomplete scan stops the batch before submission. The activity log shows track names and available timecodes.
- **Setup help.** The music check's **Help** button opens step-by-step ACRCloud setup instructions inside JetVideo.

[English guide](https://github.com/DaniloAdmin/JetVideo/blob/main/README.md) · [Українська](https://github.com/DaniloAdmin/JetVideo/blob/main/README.uk.md) · [Русский](https://github.com/DaniloAdmin/JetVideo/blob/main/README.ru.md)

## License and sources

JetVideo is distributed under GNU GPLv3. Copyright © 2026 axegaoo. The Windows ZIP includes component licenses. The source archives and checksums are separate downloads below.

| Asset | Contents |
| --- | --- |
| `JetVideo-1.1.1-Windows-x64.zip` | Portable Windows application with FFmpeg, FFprobe and Whisper Base |
| `JetVideo-1.1.1-source.zip` | JetVideo source and build scripts |
| `FFmpeg-JetVideo-v1.1.1-source.zip` | Corresponding FFmpeg source, build script, configuration, and hashes |
| `JetVideo-1.1.1-dependency-sources.zip` | Dependency sources, including Whisper and build instructions |
| `SHA256SUMS.txt` | SHA-256 checksums |
