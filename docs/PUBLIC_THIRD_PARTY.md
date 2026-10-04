# Public third-party distribution review

WorkOS Suite 1.0.1 ships the original scrcpy runtime together with a corresponding-source asset. The source download is [provided in the same release](https://github.com/jg8421/WorkOS-Suite-Public/releases/download/v1.0.1/WorkOS-Suite-1.0.1-corresponding-sources.zip). This document records concrete provenance checks and distribution materials; it is not a claim that the original upstream build was reproduced locally.

## Fixed upstream evidence

| Runtime component | Exact version | Evidence |
| --- | --- | --- |
| scrcpy | 4.1 | [Official signed release](https://github.com/Genymobile/scrcpy/releases/tag/v4.1); fixed commit `2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0`; official ZIP digest and original-file comparison |
| FFmpeg | 8.1.2 | [Fixed dependency script](https://github.com/Genymobile/scrcpy/blob/2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0/app/deps/ffmpeg.sh), version/hash pins and DLL version/license strings |
| libusb | 1.0.30 | [Fixed dependency script](https://github.com/Genymobile/scrcpy/blob/2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0/app/deps/libusb.sh), version/hash pins and DLL version strings |
| SDL | 3.4.12 | [Fixed dependency script](https://github.com/Genymobile/scrcpy/blob/2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0/app/deps/sdl.sh), version/hash pins and DLL version strings |
| dav1d | 1.5.3 | [Fixed dependency script](https://github.com/Genymobile/scrcpy/blob/2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0/app/deps/dav1d.sh), version/hash pins and explicit static linking into FFmpeg |
| zlib | 1.3.1 | Upstream inflate version/copyright marker in avcodec/avformat; [official fixed tag](https://github.com/madler/zlib/releases/tag/v1.3.1) |
| pystray | 0.19.5 | Official PyPI wheel digest; [official fixed tag](https://github.com/moses-palmer/pystray/releases/tag/v0.19.5); editable Python source and original GPL/LGPL texts |

The [official Windows build recipe](https://github.com/Genymobile/scrcpy/blob/2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0/release/build_windows.sh) calls the principal libraries with `cross shared`. The four FFmpeg DLLs report LGPL 2.1; the reviewed binary configuration does not contain `--enable-gpl` or `--enable-nonfree`. The corresponding source ZIP supplies the exact pinned dependency archives and full scrcpy source/build scripts, complete LGPL/SDL/dav1d/zlib notices and original ADB notices. Its SHA256 and the source lock permit independent integrity checks. DLL names remain unchanged and compatible modified DLLs may be substituted.

The [FFmpeg distribution checklist](https://ffmpeg.org/legal.html) calls for corresponding source, build instructions and appropriate notices. The [libusb LGPL text](https://github.com/libusb/libusb/blob/v1.0.30/COPYING) permits redistribution under its source/notice and suitable shared-library conditions. Full license texts are supplied, rather than summarized as a replacement license.

## Other bundled dependencies

Python and Node retain original runtime licenses. Each of the 22 Python wheels has a retained `.dist-info` license file; cached archives match the runtime lock and their official PyPI JSON digest. Python's official sigstore digest, Node's official SHASUMS and scrcpy's GitHub asset digest match the binary lock. Google platform-tools 37.0.1's cached ZIP matches the measured SHA256 lock and its complete original NOTICE is retained; the Google release page is not represented as a machine-readable SHA256 source. The separate scrcpy adb 37.0.0 files match the exact official Google archive pinned by scrcpy's fixed dependency script, and that version's full original NOTICE is also included. Memory/npm dependencies retain upstream package licenses, including qrcode-terminal's license file despite the package's absent license metadata field. Vendored pypdf keeps its source/license attribution.

The public package excludes the unused legacy WebView2 SDK DLLs/native PDF GUI binary. Microsoft Office/WebView2/Qianwen and external service accounts remain external setup requirements. The APK received the scoped static privacy review described in THIRD-PARTY-NOTICES; normal upstream author names and copyright emails are retained where license notices require them.
