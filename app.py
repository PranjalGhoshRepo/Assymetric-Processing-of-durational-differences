import os
import tempfile
from flask import Flask, request, jsonify, render_template, send_from_directory
from main import analyze_audio, load_models

app = Flask(__name__, template_folder='templates', static_folder='static')

# Set upload folder
UPLOAD_FOLDER = tempfile.gettempdir()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max upload limit

# Preload whisperx models at startup
print("Pre-loading models at startup...")
load_models()
print("Models preloaded successfully.")

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
        ext = '.wav'  # default to WAV
        
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"uploaded_{os.urandom(8).hex()}{ext}")
    
    try:
        file.save(temp_path)
        print(f"File saved to {temp_path}. Running analysis...")
        
        # Perform Bengali Phonetic Duration Analysis
        results = analyze_audio(temp_path)
        
        return jsonify(results)
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        return jsonify({'error': str(e)}), 500
        
    finally:
        # Clean up the uploaded file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                print(f"Error removing temporary file {temp_path}: {e}")

@app.route('/samples/<path:filename>')
def serve_sample(filename):
    return send_from_directory('.', filename)

@app.route('/api/samples', methods=['GET'])
def list_samples():
    try:
        # Scan workspace directory for wav files
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
        
    # Security: check if file exists locally in the workspace
    if not os.path.exists(filename) or '/' in filename or '\\' in filename:
        return jsonify({'error': 'Invalid file or file not found'}), 404
        
    try:
        print(f"Analyzing local file: {filename}")
        results = analyze_audio(filename)
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
