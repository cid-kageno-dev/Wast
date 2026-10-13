# TikUtils Web Patcher (Termux)

A Flask web interface for the Termux FFmpeg patcher. Open the interface from your phone's browser at `http://127.0.0.1:5000`.

## Install and run
```sh
pkg update
pkg install python ffmpeg
termux-setup-storage
cd /storage/emulated/0/Download
unzip tikutils_termux_web.zip
cd tikutils_termux_web
pip install -r requirements.txt
python app.py
```
Then open `http://127.0.0.1:5000` in the browser on the same phone.

## Features
- Browser-based file selection and upload to the local Flask server.
- Video analysis: dimensions, reported FPS, codecs, duration, approximate bitrate, 4K and 60 FPS checks.
- Remux mode: copies streams without intentional re-encoding.
- Optimize mode: H.264/H.265 CRF, preset, audio copy or AAC.
- Validates the generated output with ffprobe.

## Important
This is a local web interface hosted by Termux; the browser is the client UI, while FFmpeg runs in Termux. The file is uploaded to the local server. It is not fully client-side browser processing. To make it public, deploy on a server with FFmpeg installed, add authentication, HTTPS, upload quotas, rate limits, automatic cleanup, and storage isolation. Public hosting consumes server CPU, RAM, disk and bandwidth.
TikTok can still transcode uploads. This does not bypass TikTok processing or spoof real video quality.
