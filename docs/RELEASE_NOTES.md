JetVideo 1.1.2 is available in two portable editions for 64-bit Windows 10 or 11. Both include FFmpeg, FFprobe and the Silero speech filter.

## Lightweight edition

The 219 MiB ZIP includes Whisper Base for offline subtitles. Extract the ZIP and open `JetVideo.exe`. [Download the Windows x64 ZIP](https://github.com/DaniloAdmin/JetVideo/releases/download/v1.1.2/JetVideo-1.1.2-Windows-x64.zip)

## Full edition

The full edition includes Whisper Base, Small and unquantized Large v3 for offline subtitles.

1. Save both parts to the same folder. [Part 1 (.001)](https://github.com/DaniloAdmin/JetVideo/releases/download/v1.1.2/JetVideo-1.1.2-Windows-x64-Full.7z.001) · [Part 2 (.002)](https://github.com/DaniloAdmin/JetVideo/releases/download/v1.1.2/JetVideo-1.1.2-Windows-x64-Full.7z.002)
2. Open the `.001` file in 7-Zip and extract the archive. [7-Zip](https://www.7-zip.org/)
3. Open `JetVideo.exe` in the extracted folder. The model selector appears in the subtitle settings.

[SHA-256 checksums for both parts](https://github.com/DaniloAdmin/JetVideo/releases/download/v1.1.2/SHA256SUMS-Full.txt)

## What JetVideo does

JetVideo splits long videos into numbered clips, adds timed banners, and schedules TikTok posts through Buffer and Cloudflare R2. Editing and subtitles run locally. To publish, connect your TikTok channel in Buffer and enter the Buffer and R2 settings under **Publishing → Connections**.

## What's new

- **Subtitle editing.** Add phrases with their own timing, delete phrases, move them between adjacent time slots, and check Russian or Ukrainian spelling. Automatic subtitles keep each completed phrase on screen briefly so it is easier to read.
- **Optional music check.** Open **Publishing → Dashboard → More actions → Check music** to scan the ready folder through ACRCloud. When enabled for WATCH, a match or incomplete scan stops the batch before submission. The activity log shows track names and available timecodes.
- **Setup help.** The music check's **Help** button opens step-by-step ACRCloud setup instructions inside JetVideo.

[English guide](https://github.com/DaniloAdmin/JetVideo/blob/main/README.md) · [Українська](https://github.com/DaniloAdmin/JetVideo/blob/main/README.uk.md) · [Русский](https://github.com/DaniloAdmin/JetVideo/blob/main/README.ru.md)

## License and sources

JetVideo is distributed under GNU GPLv3. Copyright © 2026 axegaoo. Both editions include component licenses and use the same executable and corresponding dependency source archives.

| Asset | Contents |
| --- | --- |
| `JetVideo-1.1.2-Windows-x64.zip` | Lightweight Windows edition with Whisper Base |
| `JetVideo-1.1.2-Windows-x64-Full.7z.001` and `.002` | Full Windows edition with Whisper Base, Small and Large v3 |
| `JetVideo-1.1.2-source.zip` | JetVideo source and build scripts |
| `FFmpeg-JetVideo-v1.1.2-source.zip` | Corresponding FFmpeg source, build script, configuration, and hashes |
| `JetVideo-1.1.2-dependency-sources.zip` | Dependency sources, including Whisper and build instructions |
| `SHA256SUMS.txt` | SHA-256 checksums for the lightweight ZIP and source archives |
| `SHA256SUMS-Full.txt` | SHA-256 checksums for the full archive volumes |
