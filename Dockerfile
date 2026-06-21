# Use a lightweight official Python image with development tools
FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Upgrade pip and install wheel
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# Copy requirements
COPY requirements.txt .

# Install PyTorch CPU first (standard for free cloud CPU tiers)
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install WhisperX from git repository (recommended to avoid version clashes)
RUN pip install --no-cache-dir git+https://github.com/m-bain/whisperX.git

# Install other requirements
RUN pip install --no-cache-dir -r requirements.txt

# Pre-cache WhisperX model weights during container build to ensure fast startups
RUN python -c "import whisperx; whisperx.load_model('large-v3', 'cpu')"
RUN python -c "import whisperx; whisperx.load_align_model('en', 'cpu')"

# Copy the rest of the application code
COPY . .

# Hugging Face Spaces runs on port 7860 by default
EXPOSE 7860
ENV PORT=7860

# Start Flask application
CMD ["python", "app.py"]
