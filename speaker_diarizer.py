import os
import subprocess
import wave
import numpy as np

def extract_audio_from_video(video_path, wav_path, ffmpeg_path='ffmpeg'):
    """Extract audio from video to a 16kHz mono 16-bit PCM WAV file."""
    if os.path.exists(wav_path):
        try:
            os.remove(wav_path)
        except Exception:
            pass
            
    cmd = [
        ffmpeg_path,
        '-i', video_path,
        '-vn',
        '-acodec', 'pcm_s16le',
        '-ar', '16000',
        '-ac', '1',
        '-y',
        wav_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return os.path.exists(wav_path)

def read_wav_file(wav_path):
    """Read WAV file and return audio samples as a float32 numpy array and sample rate."""
    with wave.open(wav_path, 'rb') as w:
        params = w.getparams()
        n_channels, sampwidth, framerate, n_frames = params[:4]
        frames = w.readframes(n_frames)
        
        # Convert raw bytes to numpy array
        if sampwidth == 2:
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 1:
            data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sampwidth == 4:
            data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"Unsupported sample width: {sampwidth}")
            
        return data, framerate

def get_mel_filterbanks(n_mels, n_fft, sample_rate):
    """Generate Mel-frequency filterbanks."""
    mel_min = 0.0
    mel_max = 2595.0 * np.log10(1.0 + (sample_rate / 2.0) / 700.0)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = 700.0 * (10.0**(mel_points / 2595.0) - 1.0)
    bin_points = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    
    filters = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(1, n_mels + 1):
        for j in range(bin_points[i - 1], bin_points[i]):
            filters[i - 1, j] = (j - bin_points[i - 1]) / (bin_points[i] - bin_points[i - 1])
        for j in range(bin_points[i], bin_points[i + 1]):
            filters[i - 1, j] = (bin_points[i + 1] - j) / (bin_points[i + 1] - bin_points[i])
    return filters

def extract_segment_features(audio_data, sample_rate, start_sec, end_sec, n_mels=20, n_fft=512, hop_length=160):
    """Extract aggregated log-Mel features for a specific audio segment."""
    start_sample = int(start_sec * sample_rate)
    end_sample = int(end_sec * sample_rate)
    
    # Clip and get segment audio
    segment_audio = audio_data[max(0, start_sample):min(len(audio_data), end_sample)]
    if len(segment_audio) < 160: # less than 10ms, return silence feature
        return np.zeros(2 * n_mels)
        
    # Standard frame-by-frame analysis
    frame_length = n_fft
    step = hop_length
    num_frames = max(1, (len(segment_audio) - frame_length) // step + 1)
    
    # Pre-calculate Mel filterbank
    filters = get_mel_filterbanks(n_mels, n_fft, sample_rate)
    
    # Hamming window
    window = np.hamming(frame_length)
    
    frame_features = []
    for i in range(num_frames):
        start = i * step
        end = start + frame_length
        frame = segment_audio[start:end]
        
        # Pad frame if it is shorter than frame_length
        if len(frame) < frame_length:
            frame = np.pad(frame, (0, frame_length - len(frame)), 'constant')
            
        # Apply window
        windowed_frame = frame * window
        
        # FFT and Power Spectrum
        fft_complex = np.fft.rfft(windowed_frame, n=n_fft)
        power_spectrum = np.abs(fft_complex) ** 2 / frame_length
        
        # Filterbank energies
        mel_energies = np.dot(filters, power_spectrum)
        # Log scaling (with small epsilon to avoid log(0))
        log_mel_energies = np.log(mel_energies + 1e-6)
        
        frame_features.append(log_mel_energies)
        
    if not frame_features:
        return np.zeros(2 * n_mels)
        
    # Aggregate across frames: mean and standard deviation to represent voice print
    mean_feat = np.mean(frame_features, axis=0)
    std_feat = np.std(frame_features, axis=0)
    
    # Concatenate mean and std to form a 2 * n_mels voice print vector
    voice_print = np.concatenate([mean_feat, std_feat])
    return voice_print

def cluster_speakers(video_path, segments, num_speakers=2, ffmpeg_path='ffmpeg'):
    """
    Classify each segment by speaker.
    segments: list of dicts, each having 'start' and 'end' keys.
    Returns: list of speaker labels (0 to num_speakers-1).
    """
    if not segments:
        return []
        
    temp_wav_path = video_path + ".temp.wav"
    try:
        # Step 1: Extract audio track
        if not extract_audio_from_video(video_path, temp_wav_path, ffmpeg_path):
            print("Failed to extract audio from video.")
            return [0] * len(segments)
            
        # Step 2: Read audio samples
        audio_data, sample_rate = read_wav_file(temp_wav_path)
        
        # Step 3: Extract speaker features for each segment
        features = []
        for seg in segments:
            feat = extract_segment_features(audio_data, sample_rate, seg['start'], seg['end'])
            features.append(feat)
            
        features = np.array(features)
        
        # Normalize features (standardization)
        feat_mean = np.mean(features, axis=0, keepdims=True)
        feat_std = np.std(features, axis=0, keepdims=True) + 1e-6
        normalized_features = (features - feat_mean) / feat_std
        
        # Step 4: Run KMeans clustering
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
        
        # Check if we should auto-detect the number of speakers
        if num_speakers <= 0:
            max_k = min(5, len(segments))
            if max_k >= 2:
                best_k = 1
                best_score = -1.0
                for k in range(2, max_k + 1):
                    if len(segments) >= k:
                        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
                        labels = kmeans.fit_predict(normalized_features)
                        try:
                            score = silhouette_score(normalized_features, labels)
                            print(f"[Diarization] Tried K={k}, Silhouette Score={score:.4f}")
                            if score > best_score:
                                best_score = score
                                best_k = k
                        except Exception as e:
                            print(f"[Diarization] K={k} score error: {e}")
                
                # If silhouette score is too low, it's likely just one speaker
                if best_score < 0.25:
                    print(f"[Diarization] Best clustering score {best_score:.4f} is too low. Assuming 1 speaker.")
                    best_k = 1
                
                actual_num_speakers = best_k
                print(f"[Diarization] Auto-detected number of speakers: {actual_num_speakers}")
            else:
                actual_num_speakers = 1
        else:
            actual_num_speakers = min(num_speakers, len(segments))
            
        if actual_num_speakers <= 1:
            return [0] * len(segments)
            
        kmeans = KMeans(n_clusters=actual_num_speakers, random_state=42, n_init=10)
        labels = kmeans.fit_predict(normalized_features)
        return labels.tolist()
        
    except Exception as e:
        print(f"Error during speaker clustering: {e}")
        return [0] * len(segments)
    finally:
        # Cleanup temp WAV file
        if os.path.exists(temp_wav_path):
            try:
                os.remove(temp_wav_path)
            except Exception:
                pass
