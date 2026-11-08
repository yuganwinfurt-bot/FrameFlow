这个目录用来放 FFmpeg 与 FFprobe 的可执行文件，仓库不提交它们（各约 105 MB）。

运行时程序按下面的顺序查找（见 `frameflow/media.py` 的 `Tools.find()`）：

1. 本目录下的 `ffmpeg.exe` 与 `ffprobe.exe`；
2. 系统 `PATH` 里的 `ffmpeg` 与 `ffprobe`。

Windows 上取 [gyan.dev 的 essentials 构建](https://www.gyan.dev/ffmpeg/builds/)：解压后把 `bin\ffmpeg.exe` 和 `bin\ffprobe.exe` 复制到本目录即可。程序用到 libx264、libx265、libvpx-vp9、libopus、libmp3lame 与 libvorbis 编码器。
