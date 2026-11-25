**[Download JetVideo 1.1.0 for Windows x64](https://github.com/DaniloAdmin/JetVideo/releases/download/v1.1.0/JetVideo-1.1.0-Windows-x64.zip).** Extract the entire ZIP and open `JetVideo.exe` on 64-bit Windows 10 or 11. The portable build includes FFmpeg, FFprobe and Whisper with both subtitle models.

## What's new

- **Dark interface with clearer navigation.** Icon tabs, short transitions, separate subtitle editing and styling tabs, and more room for posting history in smaller windows.
- **Offline Russian and Ukrainian subtitles.** Transcribe speech or import an SRT. Preview the video with sound, correct phrases, adjust placement and style, then save an SRT or export an MP4 with subtitles in the picture. The default speech filter skips sections without detected speech and removes standalone fillers. Singing or very quiet speech can still be misclassified.
- **Fast splitting without re-encoding.** With banners off, keyframe mode copies video and audio into numbered clips. Clip lengths depend on keyframe spacing. Use regular splitting for exact timing or to add timed banners.

Editing works without an account. To schedule posts, connect a TikTok channel in Buffer and configure a Cloudflare R2 bucket in **Publishing → Connections**. The app includes illustrated setup help in English, Ukrainian and Russian, available with **F1**.

[English guide](https://github.com/DaniloAdmin/JetVideo/blob/main/README.md) · [Українська](https://github.com/DaniloAdmin/JetVideo/blob/main/README.uk.md) · [Русский](https://github.com/DaniloAdmin/JetVideo/blob/main/README.ru.md)

## License and sources

JetVideo is distributed under GNU GPLv3. Copyright © 2026 axegaoo. FFmpeg and FFprobe 9.1 are included as separately invoked programs. The notices in the ZIP list the licenses of bundled components.

| Asset | Contents |
| --- | --- |
| `JetVideo-1.1.0-Windows-x64.zip` | Portable Windows application with FFmpeg, FFprobe and offline subtitles |
| `JetVideo-1.1.0-source.zip` | JetVideo source and build scripts |
| `FFmpeg-JetVideo-v1.1.0-source.zip` | Corresponding FFmpeg source, build script, configuration, and hashes |
| `JetVideo-1.1.0-dependency-sources.zip` | Python dependencies, runtime sources, and Whisper source and build instructions |
| `SHA256SUMS.txt` | SHA-256 checksums |
