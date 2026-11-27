# FFmpeg and FFprobe for JetVideo

FFmpeg 9.1 and FFprobe 9.1 are separate programs, built for Windows x64 from the signed FFmpeg release. The programs report GNU GPL version 3 or later. Their configuration enables `gpl`, `version3`, `libx264`, `libdav1d` and `zlib`. Windows provides the imported system DLLs. JetVideo invokes the two executables as separate processes.

| Component | Source revision | Applicable notices |
| --- | --- | --- |
| FFmpeg and FFprobe | 9.1 | `COPYING.GPLv3` |
| x264 | `b35605ace3ddf7c1a5d67a2eb553f034aef41d55` | GPL version 2 or later, `COPYING.x264` |
| dav1d | 1.5.5 | BSD-2-Clause, `COPYING.dav1d` |
| zlib | 1.4 | Zlib license, `LICENSE.zlib` |
| MinGW-w64 runtime | `14.0.0.r598.ge3d18511f-1` | `mingw-w64/` |
| GCC runtime objects | MSYS2 GCC `16.2.0-7` | `gcc/` |

Make `FFmpeg-JetVideo-v1.1.2-source.zip` available as a downloadable asset beside the Windows ZIP. It contains the exact source archives, build script, package versions, configuration and hashes of both distributed executables. [FFmpeg release sources](https://ffmpeg.org/download.html)
