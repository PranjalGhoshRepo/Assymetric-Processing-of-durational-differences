---
title: Bengali Phonetic Analyzer
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Bengali Phonetic Speech Analyzer

This is a web application that performs Bengali speech transcription, forced alignment, phonetic duration classification (Short vs Long consonants), and LLM-based linguistic validation.

## Local Run Instructions
1. Run local LLM engine (LM Studio) on port `1234`.
2. Start Flask server: `python app.py`.
3. Open `http://127.0.0.1:5000` in browser.

## Cloud Deploy Instructions
1. Configure Space secret `GROQ_API_KEY` with your Groq cloud key.
2. Push repository to the Space using Docker SDK.
