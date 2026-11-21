# Rebuild the FFmpeg pair for JetVideo v4.3.0

Use an MSYS2 x86-64 UCRT64 shell. The matching-source ZIP includes the upstream archives in `sources/`, the MinGW-w64 source packages in `toolchain/` and the exact build package versions in `TOOLCHAIN.txt`. MSYS2 build and archive tools are supplied by the development environment. [MSYS2 portable archives](https://www.msys2.org/docs/installer/)

```sh
pacman -S --needed make mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-nasm \
  mingw-w64-ucrt-x86_64-pkgconf mingw-w64-ucrt-x86_64-meson mingw-w64-ucrt-x86_64-ninja
bash build.sh ./sources /path/to/new-build-directory
```

Use a work path without spaces. The script checks the inputs against `SOURCE_SHA256SUMS.txt`, builds x264, zlib and dav1d as static libraries, and writes the two executables under `tools` in the build directory. The recorded paths in the FFmpeg version banner are compilation paths. The programs run without the build directory or its tools. Use `BINARY_SHA256.json` to identify the binaries shipped with this release.

The FFmpeg archive is signed by key `FCF986EA15E6E293A5644F10B4322F04D67658D8`. The verified signature and signing key accompany it. x264 is a Git archive of the specified VideoLAN commit. The `sources/` hashes identify all input archives. The included configuration and package versions record how the pair was built.
