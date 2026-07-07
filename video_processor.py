import os
import subprocess
import re
import shutil

def get_ffmpeg_binaries():
    ffmpeg_bin = shutil.which('ffmpeg')
    ffprobe_bin = shutil.which('ffprobe')
    if ffmpeg_bin and ffprobe_bin:
        return ffmpeg_bin, ffprobe_bin
    
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    if local_app_data:
        winget_dir = os.path.join(local_app_data, 'Microsoft', 'WinGet', 'Packages')
        if os.path.exists(winget_dir):
            for item in os.listdir(winget_dir):
                if 'Gyan.FFmpeg' in item:
                    # search recursively
                    for root, dirs, files in os.walk(os.path.join(winget_dir, item)):
                        if 'ffmpeg.exe' in files:
                            ffmpeg_bin = os.path.join(root, 'ffmpeg.exe')
                        if 'ffprobe.exe' in files:
                            ffprobe_bin = os.path.join(root, 'ffprobe.exe')
                        if ffmpeg_bin and ffprobe_bin:
                            return ffmpeg_bin, ffprobe_bin
                            
    return 'ffmpeg', 'ffprobe'

def get_video_duration(video_path, ffprobe_path='ffprobe'):
    cmd = [
        ffprobe_path,
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    try:
        if result.returncode != 0:
            print(f"ffprobe failed with return code {result.returncode}")
            print(f"ffprobe stderr: {result.stderr}")
            return 0.0
        val = result.stdout.strip()
        print(f"ffprobe stdout: {val}")
        return float(val)
    except Exception as e:
        print(f"get_video_duration exception: {e}")
        print(f"ffprobe stdout: {result.stdout}")
        print(f"ffprobe stderr: {result.stderr}")
        return 0.0

def get_video_resolution(video_path, ffprobe_path='ffprobe'):
    cmd = [
        ffprobe_path,
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=s=x:p=0',
        video_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    try:
        parts = result.stdout.strip().split('x')
        return int(parts[0]), int(parts[1])
    except Exception as e:
        print(f"Error getting video resolution: {e}")
        return 1080, 1920

def detect_silence(video_path, noise_db=-30, min_silence_len=0.3, ffmpeg_path='ffmpeg'):
    cmd = [
        ffmpeg_path,
        '-i', video_path,
        '-af', f'silencedetect=noise={noise_db}dB:d={min_silence_len}',
        '-f', 'null',
        '-'
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    stderr = result.stderr
    
    intervals = []
    lines = stderr.split('\n')
    current_start = None
    for line in lines:
        if 'silence_start:' in line:
            m = re.search(r'silence_start:\s*([\d\.]+)', line)
            if m:
                current_start = float(m.group(1))
        elif 'silence_end:' in line:
            m = re.search(r'silence_end:\s*([\d\.]+)', line)
            if m and current_start is not None:
                current_end = float(m.group(1))
                intervals.append((current_start, current_end))
                current_start = None
                
    return intervals

def calculate_keep_intervals(silence_intervals, duration):
    if not silence_intervals:
        return [(0.0, duration)]
        
    keep_intervals = []
    current_time = 0.0
    
    for start, end in silence_intervals:
        if start > current_time + 0.1:
            keep_intervals.append((current_time, start))
        current_time = end
        
    if duration > current_time + 0.1:
        keep_intervals.append((current_time, duration))
        
    if not keep_intervals:
        return [(0.0, duration)]
        
    return keep_intervals

def cut_silence(video_path, keep_intervals, output_path, ffmpeg_path='ffmpeg', ffprobe_path='ffprobe', format_shorts=True, logo_path=None):
    if len(keep_intervals) == 1 and keep_intervals[0][0] == 0.0:
        if not format_shorts:
            cmd = [ffmpeg_path, '-i', video_path, '-c', 'copy', '-y', output_path]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return
        else:
            if logo_path:
                filter_complex = "[0:v]split=2[v1][v2];" \
                                 "[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];" \
                                 "[v2]scale=1080:1920:force_original_aspect_ratio=decrease[fg];" \
                                 "[bg][fg]overlay=(W-w)/2:(H-h)/2[base_v];" \
                                 "[1:v]scale=1080:-2[logo];" \
                                 "[base_v][logo]overlay=0:220[outv]"
                cmd = [
                    ffmpeg_path,
                    '-i', video_path,
                    '-i', logo_path,
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-map', '0:a',
                    '-y',
                    output_path
                ]
            else:
                filter_complex = "[0:v]split=2[v1][v2];" \
                                 "[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];" \
                                 "[v2]scale=1080:1920:force_original_aspect_ratio=decrease[fg];" \
                                 "[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
                cmd = [
                    ffmpeg_path,
                    '-i', video_path,
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-map', '0:a',
                    '-y',
                    output_path
                ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
            if result.returncode != 0:
                raise Exception(f"FFmpeg failed to scale video to Shorts format: {result.stderr}")
            return
        
    filter_complex = ""
    inputs = ""
    for i, (start, end) in enumerate(keep_intervals):
        filter_complex += f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}];"
        filter_complex += f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}];"
        inputs += f"[v{i}][a{i}]"
        
    if format_shorts:
        if logo_path:
            filter_complex += f"{inputs}concat=n={len(keep_intervals)}:v=1:a=1[concatv][outa];"
            filter_complex += f"[concatv]split=2[v_split1][v_split2];" \
                              f"[v_split1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];" \
                              f"[v_split2]scale=1080:1920:force_original_aspect_ratio=decrease[fg];" \
                              f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base_v];" \
                              f"[1:v]scale=1080:-2[logo];" \
                              f"[base_v][logo]overlay=0:220[outv]"
        else:
            filter_complex += f"{inputs}concat=n={len(keep_intervals)}:v=1:a=1[concatv][outa];"
            filter_complex += f"[concatv]split=2[v_split1][v_split2];" \
                              f"[v_split1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];" \
                              f"[v_split2]scale=1080:1920:force_original_aspect_ratio=decrease[fg];" \
                              f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
    else:
        filter_complex += f"{inputs}concat=n={len(keep_intervals)}:v=1:a=1[outv][outa]"
    
    if logo_path and format_shorts:
        cmd = [
            ffmpeg_path,
            '-i', video_path,
            '-i', logo_path,
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-map', '[outa]',
            '-y',
            output_path
        ]
    else:
        cmd = [
            ffmpeg_path,
            '-i', video_path,
            '-filter_complex', filter_complex,
            '-map', '[outv]',
            '-map', '[outa]',
            '-y',
            output_path
        ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    if result.returncode != 0:
        raise Exception(f"FFmpeg failed to cut video: {result.stderr}")

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds == 100:
        secs += 1
        centiseconds = 0
    if secs == 60:
        minutes += 1
        secs = 0
    if minutes == 60:
        hours += 1
        minutes = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"

def clean_and_merge_tokens(words, language):
    if not words:
        return []
    
    lang = (language or 'en').lower()
    if lang not in ['th', 'ja']:
        return words
        
    merged_words = []
    current_word = None
    
    for w in words:
        word_text = w.get('word', '')
        word_clean = word_text.strip()
        if not word_clean:
            continue
            
        if current_word is None:
            current_word = {
                'word': word_clean,
                'start': w['start'],
                'end': w['end']
            }
        else:
            current_len = len(current_word['word'])
            current_duration = current_word['end'] - current_word['start']
            
            # Merge if the accumulated word is less than 3 characters or duration is less than 0.3s
            if current_len < 3 or current_duration < 0.3:
                current_word['word'] += word_clean
                current_word['end'] = w['end']
            else:
                merged_words.append(current_word)
                current_word = {
                    'word': word_clean,
                    'start': w['start'],
                    'end': w['end']
                }
                
    if current_word:
        merged_words.append(current_word)
        
    return merged_words

def group_words(words, max_words=3, max_duration=1.5, max_chars=20):
    groups = []
    current_group = []
    
    for w in words:
        if len(current_group) > 0:
            group_duration = w['end'] - current_group[0]['start']
            group_chars = sum(len(x['word']) for x in current_group) + len(w['word'])
            
            if len(current_group) >= max_words or group_duration > max_duration or group_chars > max_chars:
                groups.append(current_group)
                current_group = []
        
        current_group.append(w)
        
    if current_group:
        groups.append(current_group)
        
    return groups

def is_dark_color(hex_str):
    # Strip ASS tags if present
    clean_hex = hex_str.replace('&H', '').replace('&', '').replace('00', '', 1)
    if len(clean_hex) >= 6:
        try:
            # ASS format is BBGGRR
            b = int(clean_hex[0:2], 16)
            g = int(clean_hex[2:4], 16)
            r = int(clean_hex[4:6], 16)
            return (0.299 * r + 0.587 * g + 0.114 * b) < 120
        except ValueError:
            pass
    return False

def generate_ass_file(groups, ass_path, width=1080, height=1920, font_name="Arial Black", font_size=70, font_color="&H00D5FF&", language="en", speaker_labels=None, speaker_colors=None, group_colors=None):
    # Convert BGR hex for ASS format
    if not font_color.startswith('&H'):
        font_color = f"&H00{font_color}&"
        
    # Scale font size and outline dynamically based on the height
    scale_factor = height / 1920.0
    scaled_font_size = max(16, int(font_size * scale_factor))
    scaled_outline = max(2, int(10 * scale_factor))
    
    if height > width:
        margin_v = int(height * 0.4)
    else:
        margin_v = int(height * 0.15)
    
    header = f"""[Script Info]
; Script generated by Antigravity Arona
Title: YouTube Shorts Subtitles
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{scaled_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{scaled_outline},0,2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    lang = (language or 'en').lower()
    sep = "" if lang in ['th', 'ja'] else " "
    
    # Highlight outline color (usually black unless highlight color is dark)
    highlight_outline = "&H00FFFFFF&" if is_dark_color(font_color) else "&H00000000&"
    
    dialogues = []
    for i, group in enumerate(groups):
        # Determine speaker and their base color
        speaker = speaker_labels[i] if (speaker_labels and i < len(speaker_labels)) else 0
        
        # Get base color for this speaker or custom color for this group
        base_color = None
        if group_colors and i < len(group_colors) and group_colors[i]:
            base_color = group_colors[i]
            
        if not base_color:
            if speaker_colors and speaker in speaker_colors:
                base_color = speaker_colors[speaker]
            else:
                # Default BGR neon colors: 0=White, 1=Yellow (00FFFF), 2=Green (00FF00), 3=Pink (FF00FF), 4=Orange/Cyan (00D5FF)
                default_palette = ["FFFFFF", "00FFFF", "00FF00", "FF00FF", "00D5FF"]
                if 0 <= speaker < len(default_palette):
                    base_color = default_palette[speaker]
                else:
                    base_color = "FFFFFF"
            
        if not base_color.startswith('&H'):
            base_bgr = f"&H00{base_color}&"
        else:
            base_bgr = base_color
            
        # Determine base outline color
        base_outline = "&H00FFFFFF&" if is_dark_color(base_bgr) else "&H00000000&"
        
        for j in range(len(group)):
            start_time = group[j]['start']
            if j < len(group) - 1:
                end_time = group[j+1]['start']
            else:
                end_time = group[j]['end']
                
            text_parts = []
            for idx, w in enumerate(group):
                word_str = w['word'].strip()
                if idx == j:
                    # Highlight word
                    text_parts.append(f"{{\\c{font_color}\\3c{highlight_outline}}}{word_str}{{\\c{base_bgr}\\3c{base_outline}}}")
                else:
                    text_parts.append(word_str)
                    
            text = sep.join(text_parts)
            # Wrap the entire dialogue line in the speaker's base color and outline style
            line_text = f"{{\\c{base_bgr}\\3c{base_outline}}}{text}"
            dialogues.append(
                f"Dialogue: 0,{format_time(start_time)},{format_time(end_time)},Default,,0,0,0,,{line_text}"
            )
            
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write("\n".join(dialogues))

def burn_subtitles(video_path, ass_path, output_path, ffmpeg_path='ffmpeg'):
    # To avoid Windows path escaping issues with the subtitles filter,
    # we run ffmpeg with relative paths by executing it in the directory of the video.
    video_dir = os.path.dirname(os.path.abspath(video_path))
    video_file = os.path.basename(video_path)
    ass_file = os.path.basename(ass_path)
    
    # We must ensure the paths are relative to video_dir
    # FFmpeg subtitles filter syntax: -vf "subtitles=filename.ass"
    # To be extremely safe: -vf "subtitles='filename.ass'"
    filter_str = f"subtitles='{ass_file}'"
    
    cmd = [
        ffmpeg_path,
        '-i', video_file,
        '-vf', filter_str,
        '-c:a', 'copy', # keep original audio
        '-y',
        output_path
    ]
    
    result = subprocess.run(cmd, cwd=video_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    if result.returncode != 0:
        raise Exception(f"FFmpeg failed to burn subtitles: {result.stderr}")

def html_to_ass_color(html_color):
    """Convert HTML hex '#RRGGBB' to ASS hex 'BBGGRR'."""
    if not html_color:
        return None
    html_color = html_color.lstrip('#')
    if len(html_color) == 6:
        r = html_color[0:2]
        g = html_color[2:4]
        b = html_color[4:6]
        return f"{b}{g}{r}"
    return html_color

def ass_to_html_color(ass_color):
    """Convert ASS hex 'BBGGRR' (or '&H00BBGGRR&') to HTML hex '#RRGGBB'."""
    if not ass_color:
        return '#FFFFFF'
    clean = ass_color.replace('&H', '').replace('&', '').replace('00', '', 1)
    if len(clean) == 6:
        b = clean[0:2]
        g = clean[2:4]
        r = clean[4:6]
        return f"#{r}{g}{b}"
    return '#FFFFFF'

def has_audio(video_path, ffprobe_path='ffprobe'):
    """Check if the video file contains an audio stream."""
    cmd = [
        ffprobe_path,
        '-v', 'error',
        '-select_streams', 'a',
        '-show_entries', 'stream=codec_type',
        '-of', 'csv=p=0',
        video_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    return 'audio' in result.stdout.lower()
