pip3 install pytube
python3 - <<'PY'
from pytube import YouTube
url = "https://www.youtube.com/watch?v=ewzNHwA_cUM"
yt = YouTube(url)
yt.streams.filter(only_audio=True).first().download(filename="songs/desperado.mp4")
print("✅ Downloaded audio (convert to mp3 with ffmpeg if needed).")
PY

