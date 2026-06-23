import os
import tempfile
import time
from flask import Flask, request, jsonify, render_template, send_from_directory
from main import analyze_audio, load_models, enhance_audio

app = Flask(__name__, template_folder='templates', static_folder='static')

# Set upload folder
UPLOAD_FOLDER = tempfile.gettempdir()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max upload limit

# Preload whisperx models at startup
print("Pre-loading models at startup...")
load_models()
print("Models preloaded successfully.")

# Ensure static/enhanced directory exists
ENHANCED_DIR = os.path.join('static', 'enhanced')
os.makedirs(ENHANCED_DIR, exist_ok=True)

import subprocess

def convert_to_wav_16k(input_path: str, output_path: str):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_exe = os.path.join(current_dir, "ffmpeg.exe")
    if not os.path.exists(ffmpeg_exe):
        ffmpeg_exe = "ffmpeg"
        
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i", input_path,
        "-ar", "16000",
        "-ac", "1",
        output_path
    ]
    print(f"Running conversion: {' '.join(cmd)}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr.decode('utf-8', errors='ignore')}")

def cleanup_enhanced_files():
    try:
        now = time.time()
        for f in os.listdir(ENHANCED_DIR):
            filepath = os.path.join(ENHANCED_DIR, f)
            # Remove files older than 15 minutes
            if os.path.getmtime(filepath) < now - 900:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
    except Exception as e:
        print(f"Cleanup error: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
        
    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Save to a temporary file
    _, ext = os.path.splitext(file.filename)
    if not ext:
        ext = '.wav'
        
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"uploaded_{os.urandom(8).hex()}{ext}")
    
    # Generate enhanced file path
    enhanced_filename = f"enhanced_{os.urandom(8).hex()}.wav"
    enhanced_path = os.path.join(ENHANCED_DIR, enhanced_filename)
    
    wav_path = os.path.join(app.config['UPLOAD_FOLDER'], f"converted_{os.urandom(8).hex()}.wav")
    
    try:
        file.save(temp_path)
        print(f"File saved. Converting to 16kHz mono WAV...")
        
        convert_to_wav_16k(temp_path, wav_path)
        
        # 1. Clean background noise & enhance speech
        print(f"Enhancing audio...")
        enhance_audio(wav_path, enhanced_path)
        
        # 2. Perform analysis on the ENHANCED file
        print(f"Enhanced audio saved to {enhanced_path}. Running analysis...")
        results = analyze_audio(enhanced_path)
        
        # 3. Add the enhanced audio URL to results
        results['enhanced_audio_url'] = f"/static/enhanced/{enhanced_filename}"
        
        # Periodic cleanup of old cached files
        cleanup_enhanced_files()
        
        return jsonify(results)
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        return jsonify({'error': str(e)}), 500
        
    finally:
        # Clean up raw upload and intermediate WAV file
        for p in [temp_path, wav_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    print(f"Error removing file {p}: {e}")

@app.route('/samples/<path:filename>')
def serve_sample(filename):
    return send_from_directory('.', filename)

@app.route('/api/samples', methods=['GET'])
def list_samples():
    try:
        files = [f for f in os.listdir('.') if f.lower().endswith('.wav')]
        return jsonify(files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analyze-local', methods=['POST'])
def analyze_local():
    data = request.json or {}
    filename = data.get('filename')
    if not filename:
        return jsonify({'error': 'No filename provided'}), 400
        
    if not os.path.exists(filename) or '/' in filename or '\\' in filename:
        return jsonify({'error': 'Invalid file or file not found'}), 404
        
    # Generate enhanced file path
    enhanced_filename = f"enhanced_{os.urandom(8).hex()}.wav"
    enhanced_path = os.path.join(ENHANCED_DIR, enhanced_filename)
    
    try:
        print(f"Enhancing local file: {filename}...")
        enhance_audio(filename, enhanced_path)
        
        print(f"Enhanced local file saved to {enhanced_path}. Running analysis...")
        results = analyze_audio(enhanced_path)
        results['enhanced_audio_url'] = f"/static/enhanced/{enhanced_filename}"
        
        cleanup_enhanced_files()
        return jsonify(results)
    except Exception as e:
        print(f"Error during local analysis: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Run server on port defined by environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    # Bind to 0.0.0.0 when hosted, or 127.0.0.1 locally
    host = '0.0.0.0' if os.environ.get('PORT') else '127.0.0.1'
    app.run(host=host, port=port, debug=False if os.environ.get('PORT') else True)
