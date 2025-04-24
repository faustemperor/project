from flask import Flask, request, jsonify, send_file, after_this_request
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, APIC, error
import os
import traceback
import re
import time
import requests
import csv
from datetime import datetime

app = Flask(__name__)
CACHE_FOLDER = 'cache'
LOG_FILE = 'downloads.csv'
os.makedirs(CACHE_FOLDER, exist_ok=True)

last_request_time = {}
download_counter = 0
RATE_LIMIT_SECONDS = 10

def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]+', '', name).strip()[:60]

def log_download(user_ip, title):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([now, user_ip, title])

class MyLogger:
    def debug(self, msg):
        if msg.startswith('[download]') or msg.startswith('[ExtractAudio]'):
            print(msg)
    def warning(self, msg): print(f"[WARNING] {msg}")
    def error(self, msg): print(f"[ERROR] {msg}")

@app.route('/download', methods=['GET'])
def download():
    global download_counter
    query = request.args.get('q')
    if not query:
        return jsonify({'error': 'Missing query ?q=...'}), 400

    user_ip = request.remote_addr
    now = time.time()

    if user_ip in last_request_time and now - last_request_time[user_ip] < RATE_LIMIT_SECONDS:
        return jsonify({'error': 'Слишком часто. Подожди пару секунд 😊'}), 429
    last_request_time[user_ip] = now

    try:
        print(f"\n🔍 Обрабатываю запрос от {user_ip}: {query}")

        info_opts = {
            'quiet': True,
            'noplaylist': True,
            'skip_download': True,
            'default_search': 'ytsearch1',
        }
        with YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query} music", download=False)

        duration = info.get("duration", 0)
        if duration > 3600:
            return jsonify({'error': '🎧 Ого, это целый концерт! Я качаю только треки до 1 часа ⏱️'}), 400

        video_id = info.get("id", "unknown")
        title = sanitize_filename(info.get("title", video_id))
        artist = sanitize_filename(info.get("uploader", "Unknown Artist"))
        thumbnail_url = info.get("thumbnail")

        mp3_filename = f"{video_id} - {title}.mp3"
        mp3_path = os.path.join(CACHE_FOLDER, mp3_filename)

        if os.path.exists(mp3_path):
            print(f"♻️ Использую кеш: {mp3_filename}")
        else:
            print(f"⬇️ Скачиваю: {title}")
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(CACHE_FOLDER, f"{video_id}.%(ext)s"),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': False,
                'noplaylist': True,
                'default_search': 'ytsearch1',
                'logger': MyLogger(),
            }

            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"ytsearch1:{query} music"])

            downloaded_file = os.path.join(CACHE_FOLDER, f"{video_id}.mp3")
            if os.path.exists(downloaded_file):
                os.rename(downloaded_file, mp3_path)
            else:
                return jsonify({'error': 'Файл не создан'}), 500

            # Добавление ID3 и обложки
            try:
                audio = MP3(mp3_path, ID3=ID3)
                try:
                    audio.add_tags()
                except error:
                    pass
                audio["TIT2"] = TIT2(encoding=3, text=title)
                audio["TPE1"] = TPE1(encoding=3, text=artist)

                if thumbnail_url:
                    img_data = requests.get(thumbnail_url).content
                    audio.tags.add(
                        APIC(
                            encoding=3,
                            mime='image/jpeg',
                            type=3,
                            desc='Cover',
                            data=img_data
                        )
                    )
                audio.save()
                print(f"🎧 Встроены ID3 + обложка: {artist} – {title}")
            except Exception as e:
                print(f"⚠️ Не удалось встроить ID3 или обложку: {e}")

        @after_this_request
        def remove_file(response):
            try:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
                    print(f"🗑️ Удалён: {mp3_filename}")
            except Exception as e:
                print(f"⚠️ Ошибка удаления: {e}")
            return response

        download_counter += 1
        log_download(user_ip, title)
        print(f"✅ [+1] {user_ip} скачал «{title}». Всего: {download_counter}")

        return send_file(mp3_path, as_attachment=True, download_name=mp3_filename, mimetype='audio/mpeg')

    except DownloadError as e:
        print("⛔ yt-dlp ошибка:")
        traceback.print_exc()
        return jsonify({'error': 'yt-dlp error: ' + str(e)}), 500

    except Exception as e:
        print("🔥 Общая ошибка:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000)