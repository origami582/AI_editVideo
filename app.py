import os
import sys
import uuid
import shutil
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Import our video processor functions
import video_processor

# Inject FFmpeg folder into PATH so Whisper can find it
ffmpeg_bin, ffprobe_bin = video_processor.get_ffmpeg_binaries()
if os.path.exists(ffmpeg_bin):
    ffmpeg_dir = os.path.dirname(ffmpeg_bin)
    if ffmpeg_dir not in os.environ.get('PATH', ''):
        os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')
        print(f"Arona injected FFmpeg path to environment: {ffmpeg_dir}")

app = Flask(__name__, template_folder='templates')
CORS(app)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Cache loaded Whisper models to avoid reloading them on every request
loaded_models = {}

def get_whisper_model(model_name):
    if model_name not in loaded_models:
        import whisper
        print(f"Loading Whisper model: {model_name}...")
        # Load model (will download to cache if not present)
        loaded_models[model_name] = whisper.load_model(model_name)
    return loaded_models[model_name]

def google_translate(text, source_lang='auto', target_lang='en'):
    import urllib.request
    import urllib.parse
    import json
    if not text.strip():
        return ""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "dt": "t",
            "q": text
        }
        query_string = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{query_string}", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            translations = [item[0] for item in data[0] if item[0]]
            return "".join(translations)
    except Exception as e:
        print(f"Translation error: {e}")
        return text

def generate_shorts_titles(transcription_text):
    import urllib.request
    import json
    import re
    
    fallback_titles = [
        "คลิป Shorts สุดเจ๋ง 🎥",
        "ไอเดียยอดฮิตวันนี้ ✨",
        "ห้ามพลาดสิ่งนี้! 🔥",
        "ความจริงที่คุณต้องรู้ 😱",
        "เคล็ดลับดีๆ ที่ควรรู้! 💡"
    ]
    
    if not transcription_text or not transcription_text.strip():
        return fallback_titles
        
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("Warning: GEMINI_API_KEY environment variable not set. Using fallback titles.")
        words = [w.strip() for w in transcription_text.split() if w.strip()]
        if words:
            phrase = " ".join(words[:5])
            if len(phrase) > 30:
                phrase = phrase[:27] + "..."
            return [f"✨ {phrase}", f"🔥 {phrase}!!", f"😱 {phrase}?", f"💡 {phrase} #Shorts", "คลิปเด็ดห้ามพลาด! 🎥"]
        return fallback_titles

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        
        prompt = (
            "You are a YouTube Shorts title expert. Given the following transcript from a video, "
            "generate exactly 5 catchy, engaging, and click-worthy titles for a YouTube Shorts/TikTok video. "
            "The titles should be in the same language as the transcript (usually Thai or English), short, punchy, "
            "and include relevant emojis. Output MUST be a valid JSON array of 5 strings, for example: "
            '["Title 1", "Title 2", "Title 3", "Title 4", "Title 5"]. '
            "Do not include any markdown styling like ```json or ```, just the raw JSON text."
        )
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"{prompt}\n\nTranscript: {transcription_text}"
                }]
            }]
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            candidates = res_data.get('candidates', [])
            if candidates:
                content_text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
                if content_text.startswith("```"):
                    content_text = re.sub(r'^```(?:json)?\s*', '', content_text)
                    content_text = re.sub(r'\s*```$', '', content_text)
                content_text = content_text.strip()
                titles = json.loads(content_text)
                if isinstance(titles, list) and len(titles) >= 5:
                    return titles[:5]
                elif isinstance(titles, list) and len(titles) > 0:
                    while len(titles) < 5:
                        titles.append(fallback_titles[len(titles)])
                    return titles
    except Exception as e:
        print(f"Error generating titles via Gemini API: {e}")
        
    return fallback_titles

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
        
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
        
    # Generate unique filename to avoid collision
    ext = os.path.splitext(file.filename)[1]
    unique_id = uuid.uuid4().hex
    unique_filename = f"{unique_id}{ext}"
    save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
    file.save(save_path)
    
    # Save logo if present
    logo_filename = None
    if 'logo' in request.files:
        logo_file = request.files['logo']
        if logo_file and logo_file.filename != '':
            logo_ext = os.path.splitext(logo_file.filename)[1]
            logo_filename = f"logo_{unique_id}{logo_ext}"
            logo_path = os.path.join(UPLOAD_FOLDER, logo_filename)
            logo_file.save(logo_path)
            
    return jsonify({
        'filename': unique_filename,
        'logo_filename': logo_filename
    })

@app.route('/fetch_info', methods=['POST'])
def fetch_info():
    data = request.get_json() or {}
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        import yt_dlp
        ydl_opts = {
            'skip_download': True,
            'ffmpeg_location': ffmpeg_bin if os.path.exists(ffmpeg_bin) else None,
            'js_runtimes': {'node': {}},
            'remote_components': 'ejs:github',
        }
        
        # Check if local cookies.txt exists
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
        duration_sec = info.get('duration', 0)
        if duration_sec:
            minutes, seconds = divmod(duration_sec, 60)
            duration_str = f"{int(minutes):02}:{int(seconds):02}"
        else:
            duration_str = "Live or Unknown"
            
        return jsonify({
            'success': True,
            'title': info.get('title', 'Unknown Title'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': duration_str,
            'duration_raw': duration_sec,
            'uploader': info.get('uploader', 'Unknown')
        })
    except Exception as e:
        err_msg = str(e)
        if "Sign in to confirm you’re not a bot" in err_msg:
            err_msg = (
                "YouTube บล็อคการดาวน์โหลดเนื่องจากตรวจจับว่าเป็นบอทค่ะ 😭 "
                "กรุณานำไฟล์ cookies.txt มาวางไว้ในโฟลเดอร์ของโปรแกรมเพื่อยืนยันตัวตนนะคะ 💙"
            )
        return jsonify({'error': err_msg}), 400

def time_to_seconds(t_str):
    if t_str is None or t_str == "":
        return None
    if isinstance(t_str, (int, float)):
        return float(t_str)
    
    t_str = str(t_str).strip()
    parts = t_str.split(':')
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    return None

@app.route('/download_url', methods=['POST'])
def download_url():
    data = request.get_json() or {}
    url = data.get('url')
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        import yt_dlp
        unique_id = uuid.uuid4().hex
        
        start_sec = time_to_seconds(start_time)
        end_sec = time_to_seconds(end_time)
        is_clip = start_sec is not None or end_sec is not None
        
        # We always download the full video using a pre-merged format for maximum speed and stability,
        # then we trim it locally using FFmpeg which takes 0.1 seconds!
        out_tmpl = os.path.join(UPLOAD_FOLDER, f"{unique_id}.%(ext)s")
        
        ydl_opts = {
            'ffmpeg_location': ffmpeg_bin if os.path.exists(ffmpeg_bin) else None,
            'outtmpl': out_tmpl,
            # Fallback format string: tries best mp4 video + m4a audio, then any best video + audio (which it will merge to mp4), and finally best single stream.
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'overwrites': True,
            'retries': 10,
            'fragment_retries': 10,
            'socket_timeout': 30,
            'js_runtimes': {'node': {}},
            'remote_components': 'ejs:github',
        }
        
        # Check if local cookies.txt exists
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            final_filepath = ydl.prepare_filename(info)
            name, ext = os.path.splitext(final_filepath)
            if ext != '.mp4' and os.path.exists(name + '.mp4'):
                final_filepath = name + '.mp4'
                
        # Trim the video locally if requested
        if is_clip and os.path.exists(final_filepath):
            import subprocess
            temp_trimmed_path = os.path.join(UPLOAD_FOLDER, f"trimmed_{unique_id}.mp4")
            
            # Lossless stream copying
            cmd = [
                ffmpeg_bin if os.path.exists(ffmpeg_bin) else 'ffmpeg',
                '-y',
            ]
            if start_sec is not None:
                cmd.extend(['-ss', str(start_sec)])
            if end_sec is not None:
                cmd.extend(['-to', str(end_sec)])
                
            cmd.extend([
                '-i', final_filepath,
                '-c', 'copy', # instant copy
                temp_trimmed_path
            ])
            
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            if os.path.exists(temp_trimmed_path):
                try:
                    os.remove(final_filepath)
                except Exception:
                    pass
                os.rename(temp_trimmed_path, final_filepath)
                
        filename = os.path.basename(final_filepath)
        return jsonify({
            'success': True,
            'filename': filename
        })
    except Exception as e:
        err_msg = str(e)
        if "Sign in to confirm you’re not a bot" in err_msg:
            err_msg = (
                "YouTube บล็อคการดาวน์โหลดเนื่องจากตรวจจับว่าเป็นบอทค่ะ 😭 "
                "กรุณานำไฟล์ cookies.txt มาวางไว้ในโฟลเดอร์ของโปรแกรมเพื่อยืนยันตัวตนนะคะ 💙"
            )
        return jsonify({'error': err_msg}), 500

@app.route('/process', methods=['POST'])
def process_video():
    data = request.get_json() or {}
    
    filename = data.get('filename')
    logo_filename = data.get('logo_filename')
    model_name = data.get('model', 'base')
    highlight_color = data.get('color', '00D5FF') # ASS hex BGR format or web format
    noise_db = int(data.get('noise_db', -30))
    min_silence_len = float(data.get('min_silence_len', 0.3))
    font_name = data.get('font_name', 'Arial Black')
    language = data.get('language', 'auto')
    task = data.get('task', 'transcribe')
    
    cut_silence = data.get('cut_silence', True)
    
    # Speaker diarization options
    diarization = data.get('diarization', False)
    num_speakers = int(data.get('num_speakers', 0))
    speaker_color_0 = data.get('speaker_color_0', 'FFFFFF')
    speaker_color_1 = data.get('speaker_color_1', '00FFFF')
    speaker_color_2 = data.get('speaker_color_2', '00FF00')
    speaker_color_3 = data.get('speaker_color_3', 'FF00FF')
    
    if not filename:
        return jsonify({'error': 'Filename is required'}), 400
        
    input_path = os.path.join(UPLOAD_FOLDER, filename)
    logo_path = os.path.join(UPLOAD_FOLDER, logo_filename) if logo_filename else None
    
    if not os.path.exists(input_path):
        return jsonify({'error': 'Video file not found'}), 404
        
    # Get FFmpeg paths
    ffmpeg_bin, ffprobe_bin = video_processor.get_ffmpeg_binaries()

    # Paths for processing stages
    base_name = os.path.splitext(filename)[0]
    cut_video_path = os.path.join(UPLOAD_FOLDER, f"cut_{base_name}.mp4")
    ass_path = os.path.join(UPLOAD_FOLDER, f"subs_{base_name}.ass")
    final_output_path = os.path.join(OUTPUT_FOLDER, f"shorts_{base_name}.mp4")
    
    try:
        # Step 1: Detect silence and cut video
        print("Detecting silence...")
        duration = video_processor.get_video_duration(input_path, ffprobe_bin)
        if duration <= 0:
            return jsonify({'error': 'Could not read video duration. The file may be corrupt.'}), 400
            
        silence_intervals = video_processor.detect_silence(
            input_path, noise_db, min_silence_len, ffmpeg_bin
        )
        print(f"Detected silence intervals: {silence_intervals}")
        
        if cut_silence:
            keep_intervals = video_processor.calculate_keep_intervals(silence_intervals, duration)
        else:
            keep_intervals = [(0.0, duration)]
        print(f"Calculated keep intervals: {keep_intervals}")
        
        print("Cutting video and formatting to Shorts vertical format...")
        video_processor.cut_silence(input_path, keep_intervals, cut_video_path, ffmpeg_bin, ffprobe_bin, format_shorts=True, logo_path=logo_path)
        
        # Step 2: Transcribe with Whisper
        print(f"Transcribing video using Whisper model: {model_name}... Language: {language}, Task: {task}")
        model = get_whisper_model(model_name)
        
        # Transcribe with Whisper using its native task (transcribe or translate)
        transcribe_args = {
            'word_timestamps': True,
            'task': task  # 'transcribe' or 'translate'
        }
        if language and language != 'auto':
            transcribe_args['language'] = language
            
        transcribe_result = model.transcribe(cut_video_path, **transcribe_args)
        
        # Extract all words from transcription
        all_words = []
        detected_lang = transcribe_result.get('language', 'en')
        
        for segment in transcribe_result.get('segments', []):
            seg_start = segment.get('start', 0.0)
            seg_end = segment.get('end', 0.0)
            seg_text = segment.get('text', '').strip()
            if not seg_text:
                continue
                
            words = segment.get('words', [])
            if words:
                for idx, word in enumerate(words):
                    is_last_word = (idx == len(words) - 1)
                    w_end = seg_end if is_last_word else word.get('end', seg_end)
                    all_words.append({
                        'word': word.get('word', ''),
                        'start': word.get('start', seg_start),
                        'end': w_end
                    })
            else:
                # Synthesize word timestamps
                raw_words = seg_text.split()
                if not raw_words:
                    raw_words = [seg_text]
                
                duration = seg_end - seg_start
                word_duration = duration / len(raw_words)
                for i, rw in enumerate(raw_words):
                    w_start = seg_start + (i * word_duration)
                    w_end = seg_end if (i == len(raw_words) - 1) else (w_start + word_duration)
                    all_words.append({
                        'word': f" {rw}" if (i > 0 and not detected_lang in ['th', 'ja']) else rw,
                        'start': w_start,
                        'end': w_end
                    })
                    
        # The output subtitle language
        sub_lang = 'en' if task == 'translate' else detected_lang
        
        # Clean and merge tokens for Thai and Japanese
        all_words = video_processor.clean_and_merge_tokens(all_words, sub_lang)
        print(f"Transcribed and aligned {len(all_words)} words. Subtitle language: {sub_lang}")
        
        # Get actual video resolution to make ASS styles scale perfectly
        width, height = video_processor.get_video_resolution(cut_video_path, ffprobe_bin)
        print(f"Video resolution: {width}x{height}")
        
        # Step 3: Generate ASS Subtitles
        speaker_labels = None
        if all_words:
            # Group words into beautiful phrases
            grouped = video_processor.group_words(
                all_words,
                max_words=3,
                max_duration=1.5,
                max_chars=20
            )
            
            if diarization:
                try:
                    import speaker_diarizer
                    print("Running speaker diarization...")
                    segments = [{'start': g[0]['start'], 'end': g[-1]['end']} for g in grouped]
                    speaker_labels = speaker_diarizer.cluster_speakers(
                        cut_video_path,
                        segments,
                        num_speakers=num_speakers,
                        ffmpeg_path=ffmpeg_bin
                    )
                    print(f"Assigned speaker labels: {speaker_labels}")
                except Exception as e:
                    print(f"Diarization failed: {e}")
                    speaker_labels = [0] * len(grouped)
            
            speaker_colors = {
                0: speaker_color_0,
                1: speaker_color_1,
                2: speaker_color_2,
                3: speaker_color_3
            }
            
            # Copy cut video directly for initial preview (separate diarization & transcript first)
            print("Copying cut video for initial preview...")
            shutil.copy(cut_video_path, final_output_path)
        else:
            # No subtitles transcribed, just copy the cut video as the final output
            print("No words transcribed, skipping subtitles.")
            shutil.copy(cut_video_path, final_output_path)
            
        # Cleanup only original upload to save space, but keep cut video for potential subtitle edits!
        cleanup_paths = [input_path, ass_path]
        if logo_path and os.path.exists(logo_path):
            cleanup_paths.append(logo_path)
        for path in cleanup_paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    safe_path = path.encode('ascii', errors='backslashreplace').decode('ascii')
                    print(f"Could not remove temp file {safe_path}: {e}")
                    
        # Create a simple JSON-serializable list of subtitle groups for the editor
        sub_list = []
        if all_words:
            for idx, group in enumerate(grouped):
                sep = "" if sub_lang in ['th', 'ja'] else " "
                group_text = sep.join([w['word'].strip() for w in group])
                speaker = speaker_labels[idx] if speaker_labels else 0
                spk_color = speaker_colors.get(speaker, 'FFFFFF')
                html_color = video_processor.ass_to_html_color(spk_color)
                sub_list.append({
                    'id': idx,
                    'start': group[0]['start'],
                    'end': group[-1]['end'],
                    'text': group_text,
                    'speaker': speaker,
                    'color': html_color
                })
                
        # Generate suggested titles from full transcript
        full_transcript = " ".join([item['text'] for item in sub_list])
        suggested_titles = generate_shorts_titles(full_transcript)
        
        return jsonify({
            'success': True,
            'output_file': f"shorts_{base_name}.mp4",
            'filename': filename,
            'subtitles': sub_list,
            'suggested_titles': suggested_titles
        })
        
    except Exception as e:
        safe_err = str(e).encode('ascii', errors='backslashreplace').decode('ascii')
        print(f"Error during video processing: {safe_err}")
        # Clean up temp files if error occurs
        for path in [cut_video_path, ass_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        return jsonify({'error': str(e)}), 500

@app.route('/burn', methods=['POST'])
def burn_edited_subtitles():
    data = request.json
    filename = data.get('filename')
    subtitles = data.get('subtitles', [])
    color = data.get('color', '00D5FF')
    font_name = data.get('font_name', 'Arial Black')
    
    # Speaker colors and labels
    speaker_colors_raw = data.get('speaker_colors', {})
    speaker_colors = {}
    for k, v in speaker_colors_raw.items():
        try:
            speaker_colors[int(k)] = v
        except ValueError:
            pass
            
    if not filename:
        return jsonify({'error': 'Filename is required'}), 400
        
    input_path = os.path.join(UPLOAD_FOLDER, filename)
    base_name = os.path.splitext(filename)[0]
    cut_video_path = os.path.join(UPLOAD_FOLDER, f"cut_{base_name}.mp4")
    ass_path = os.path.join(UPLOAD_FOLDER, f"subs_{base_name}.ass")
    final_output_path = os.path.join(OUTPUT_FOLDER, f"shorts_{base_name}.mp4")
    
    if not os.path.exists(cut_video_path):
        if os.path.exists(input_path):
            shutil.copy(input_path, cut_video_path)
        else:
            return jsonify({'error': 'Source video not found. Please upload it again.'}), 404
            
    ffmpeg_bin, ffprobe_bin = video_processor.get_ffmpeg_binaries()
    width, height = video_processor.get_video_resolution(cut_video_path, ffprobe_bin)
    
    # Rebuild grouped subtitles from edited text and extract speaker labels
    grouped = []
    speaker_labels = []
    # Detect language to apply proper separator
    sub_lang = 'en'
    for group in subtitles:
        txt = group.get('text', '')
        if any('\u0e00' <= char <= '\u0e7f' for char in txt):
            sub_lang = 'th'
            break
        elif any('\u3040' <= char <= '\u30ff' or '\u4e00' <= char <= '\u9fff' for char in txt):
            sub_lang = 'ja'
            break
            
    group_colors = []
    for group in subtitles:
        g_start = float(group.get('start', 0.0))
        g_end = float(group.get('end', 0.0))
        g_text = group.get('text', '').strip()
        speaker_labels.append(group.get('speaker', 0))
        
        c = group.get('color')
        if c:
            c = video_processor.html_to_ass_color(c)
        group_colors.append(c)
        
        # Split words
        if sub_lang in ['th', 'ja']:
            if ' ' in g_text:
                raw_words = g_text.split()
            else:
                raw_words = [g_text[i:i+3] for i in range(0, len(g_text), 3)]
        else:
            raw_words = g_text.split()
            
        if not raw_words:
            raw_words = [g_text]
            
        duration = g_end - g_start
        word_duration = duration / len(raw_words)
        
        group_words_list = []
        for i, rw in enumerate(raw_words):
            w_start = g_start + (i * word_duration)
            w_end = w_start + word_duration
            group_words_list.append({
                'word': f" {rw}" if (i > 0 and not sub_lang in ['th', 'ja']) else rw,
                'start': w_start,
                'end': w_end
            })
        grouped.append(group_words_list)
        
    try:
        print("Re-generating subtitles ASS file...")
        video_processor.generate_ass_file(
            grouped,
            ass_path,
            width=width,
            height=height,
            font_name=font_name,
            font_size=70,
            font_color=color,
            language=sub_lang,
            speaker_labels=speaker_labels,
            speaker_colors=speaker_colors,
            group_colors=group_colors
        )
        
        print("Re-burning subtitles...")
        video_processor.burn_subtitles(cut_video_path, ass_path, final_output_path, ffmpeg_bin)
        
        # Re-generate suggested titles from updated subtitles list
        sub_texts = [group.get('text', '').strip() for group in subtitles]
        full_transcript = " ".join(sub_texts)
        suggested_titles = generate_shorts_titles(full_transcript)
        
        return jsonify({
            'success': True, 
            'output_file': f"shorts_{base_name}.mp4",
            'suggested_titles': suggested_titles
        })
        
    except Exception as e:
        safe_err = str(e).encode('ascii', errors='backslashreplace').decode('ascii')
        print(f"Error during re-burning: {safe_err}")
        return jsonify({'error': f"Failed to burn subtitles: {str(e)}"}), 500

@app.route('/outputs/<path:filename>')
def serve_output(filename):
    return send_from_directory(OUTPUT_FOLDER, filename)

@app.route('/download/<path:filename>')
def download_file(filename):
    custom_title = request.args.get('title', '')
    if not custom_title:
        return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)
    
    # Clean the title to be a safe filename (remove illegal chars: \ / : * ? " < > |)
    import re
    safe_title = re.sub(r'[\\/*?:"<>|]', '', custom_title).strip()
    if not safe_title:
        safe_title = "shorts_video"
        
    download_filename = f"{safe_title}.mp4"
    return send_from_directory(
        OUTPUT_FOLDER, 
        filename, 
        as_attachment=True, 
        download_name=download_filename
    )

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == '__main__':
    # Get port from environment or use 5000
    port = int(os.environ.get('PORT', 5000))
    print(f"Arona Auto-Shorts Editor Server running at http://127.0.0.1:{port}/")
    app.run(host='127.0.0.1', port=port, debug=True)
