#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 || ${MSYSTEM:-} != UCRT64 ]]; then
    echo 'Usage in MSYS2 UCRT64: bash build.sh SOURCE_ARCHIVE_DIRECTORY NEW_BUILD_DIRECTORY' >&2
    exit 2
fi

sources=$(cd "$1" && pwd -P)
mkdir "$2"
work=$(cd "$2" && pwd -P)
checksums=$(cd "$(dirname "$0")" && pwd -P)/SOURCE_SHA256SUMS.txt
prefix="$work/prefix"
prefix_win=$(cygpath -m "$prefix")
jobs=${JOBS:-8}
export LC_ALL=C SOURCE_DATE_EPOCH=1795133900
export PATH=/ucrt64/bin:/usr/bin
export PKG_CONFIG_PATH=
export PKG_CONFIG_LIBDIR="$prefix_win/lib/pkgconfig"

(cd "$sources" && sha256sum -c "$checksums")
cd "$work"
x264=b35605ace3ddf7c1a5d67a2eb553f034aef41d55
for archive in ffmpeg-9.1.tar.xz "x264-$x264.tar.gz" zlib-1.4.tar.gz dav1d-1.5.5.tar.xz; do
    tar -xf "$sources/$archive"
done
mkdir -p "$prefix/include" "$prefix/lib"

(
    cd "x264-$x264"
    ./configure --host=x86_64-w64-mingw32 --prefix="$prefix_win" --enable-static --disable-cli --disable-opencl
    make -j"$jobs"
    make install-lib-static
)
(
    cd zlib-1.4
    make -f win32/Makefile.gcc -j"$jobs" libz.a
    cp zlib.h zconf.h "$prefix/include/"
    cp libz.a "$prefix/lib/"
)
meson setup dav1d-build dav1d-1.5.5 --prefix "$prefix_win" --libdir lib --buildtype release \
    -Ddefault_library=static -Denable_tools=false -Denable_tests=false -Dxxhash_muxer=disabled
meson compile -C dav1d-build -j "$jobs"
meson install -C dav1d-build --no-rebuild

cd ffmpeg-9.1
./configure --target-os=mingw32 --arch=x86_64 --disable-autodetect --disable-debug --disable-doc \
    --disable-ffplay --disable-network --enable-static --disable-shared --disable-pthreads \
    --enable-w32threads --enable-gpl --enable-version3 --enable-libx264 --enable-libdav1d --enable-zlib \
    --pkg-config-flags=--static --extra-cflags="-I$prefix_win/include" \
    --extra-ldflags="-static -L$prefix_win/lib"
make -j"$jobs" ffmpeg.exe ffprobe.exe
mkdir "$work/tools"
cp ffmpeg.exe ffprobe.exe "$work/tools/"
