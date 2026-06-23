import os
import sys

# Fallback to current dir if ffmpeg.exe was placed here
os.environ["PATH"] = os.path.dirname(os.path.abspath(__file__)) + os.pathsep + os.environ["PATH"]

import whisperx
import torch
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
import json
import numpy as np
import soundfile as sf
import re
from indic_transliteration import sanscript
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, classification_report, confusion_matrix

sys.stdout.reconfigure(encoding="utf-8")

# Check for Groq environment variables
groq_api_key = os.environ.get("GROQ_API_KEY")

if groq_api_key:
    print("\n=== Initializing Groq Client (Llama 3.3) ===")
    llm = ChatOpenAI(
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key=groq_api_key
    )
else:
    print("\n=== Initializing Local LM Studio Client ===")
    llm = ChatOpenAI(
        base_url="http://127.0.0.1:1234/v1",
        model="local-model",
        api_key="not-needed"
    )

# Quick test of LLM connectivity
try:
    test_response = llm.invoke("Say 'Connection working' briefly")
    print(f"✓ LLM connection online: {test_response.content[:100]}")
except Exception as e:
    print(f"✗ LLM connection failed: {e}")



meaning_template = """
You are an expert Bengali linguist.
I have extracted words and their consonants from a Bengali speech segment.
Here is the data for each word:
{batch_data}

TASK:
1. Provide a concise English meaning for each word.
2. Based purely on standard Bengali linguistics and spelling, tell me if each listed consonant is expected to be 'Short' or 'Long' (geminate/double consonant). Do NOT just say they are all short. Pay attention to words with geminates.
3. Provide a summary of the entire sentence.

OUTPUT FORMAT:
1. A human-readable analysis for each word.
2. A VALIDATION JSON block at the very end. This is CRITICAL for my metrics calculation.
The JSON must be a list of lists, where each inner list contains the EXPECTED standard linguistic labels ('Short' or 'Long') for that word's consonants in the exact order they are listed.

Example JSON format for two words (where the second word has a geminate consonant):
```json
[
  ["Short", "Short"], 
  ["Short", "Long"]
]
```
"""
meaning_prompt = PromptTemplate.from_template(meaning_template)
meaning_chain = meaning_prompt | llm

# ---------------------------------------------------------
# 1.5 EVALUATION & METRICS SETUP (Automated via LLM)
# ---------------------------------------------------------
y_true = []
y_pred = []
alignment_scores = []

# ---------------------------------------------------------
# 2. PROCESS THE AUDIO (WhisperX)
# ---------------------------------------------------------
device = "cuda" if torch.cuda.is_available() else "cpu"

# Globals to cache WhisperX models
_model = None
_model_a = None
_metadata = None

def load_models():
    global _model, _model_a, _metadata
    if _model is None:
        print(f"Loading WhisperX large-v3 model on {device}...")
        _model = whisperx.load_model("large-v3", device)
    if _model_a is None:
        try:
            print("Loading WhisperX alignment model...")
            _model_a, _metadata = whisperx.load_align_model(language_code="en", device=device)
        except Exception as e:
            print(f"[warn] Failed to load alignment model: {e}")
            _model_a, _metadata = None, None

def load_wav_mono_16k(path: str) -> np.ndarray:
    data, sr = sf.read(path, dtype='float32')
    
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    
    if sr != 16000:
        from scipy.signal import resample_poly
        data = resample_poly(data, 16000, sr).astype(np.float32)
    
    return data.astype(np.float32)

def enhance_audio(input_path: str, output_path: str):
    import noisereduce as nr
    from scipy.signal import butter, lfilter
    
    # Load audio
    data, sr = sf.read(input_path, dtype='float32')
    
    # Convert to mono if stereo
    if len(data.shape) > 1:
        data = data.mean(axis=1)
        
    # Perform stationary noise reduction using noisereduce
    reduced_noise = nr.reduce_noise(y=data, sr=sr, stationary=True)
    
    # Add a bandpass filter targeting human speech frequencies (80Hz to 4000Hz)
    def butter_bandpass(lowcut, highcut, fs, order=4):
        nyq = 0.5 * fs
        low = lowcut / nyq
        high = highcut / nyq
        b, a = butter(order, [low, high], btype='band')
        return b, a

    def bandpass_filter(data, lowcut, highcut, fs, order=4):
        b, a = butter_bandpass(lowcut, highcut, fs, order=order)
        y = lfilter(b, a, data)
        return y
        
    # Filter frequencies below 80Hz and above 4000Hz (or half sampling rate)
    high_limit = min(4000.0, sr / 2.0 - 1.0)
    filtered = bandpass_filter(reduced_noise, 80.0, high_limit, sr)
    
    # Peak normalize
    max_val = np.max(np.abs(filtered))
    if max_val > 0:
        normalized = filtered / max_val * 0.95
    else:
        normalized = filtered
        
    # Save output
    sf.write(output_path, normalized.astype(np.float32), sr)

BENGALI_VOWELS = set("অআইঈউঊঋএঐওঔািীুূৃেৈোৌ")
BENGALI_CONSONANTS = set("কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়ৎংঃঁ")
ENGLISH_VOWELS = set("aeiou")

def _is_letter(ch: str) -> bool:
    ch = (ch or "").strip()
    if not ch: return False
    return ch in BENGALI_VOWELS or ch in BENGALI_CONSONANTS or bool(re.fullmatch(r"[A-Za-z]", ch))

def _is_vowel(ch: str) -> bool:
    ch = (ch or "").strip()
    return ch in BENGALI_VOWELS or (bool(re.fullmatch(r"[A-Za-z]", ch)) and ch.lower() in ENGLISH_VOWELS)

def _is_consonant(ch: str) -> bool:
    ch = (ch or "").strip()
    return ch in BENGALI_CONSONANTS or (bool(re.fullmatch(r"[A-Za-z]", ch)) and ch.lower() not in ENGLISH_VOWELS)

def classify_short_long(
    char_duration: float,
    pos_in_letters: int,
    last_pos_in_letters: int,
    prev_vowel_duration: float | None,
) -> tuple[str, str]:
    at_edge = pos_in_letters == 0 or pos_in_letters == last_pos_in_letters
    prev_vowel_short = prev_vowel_duration is not None and prev_vowel_duration < 0.08

    if at_edge:
        verdict = "Short"
        reason = "Edge position ⇒ duration rule ignored."
        return verdict, reason

    if char_duration >= 0.13 or (prev_vowel_short and char_duration >= 0.11):
        verdict = "Long"
        reason = "Middle position; duration indicates Long (geminate)."
        if prev_vowel_short and char_duration < 0.13:
            reason += " Previous vowel was very short (<0.08s), boosting likelihood."
        return verdict, reason

    verdict = "Short"
    reason = "Middle position; duration below Long threshold."
    return verdict, reason

def analyze_audio(audio_path: str) -> dict:
    load_models()
    
    audio = load_wav_mono_16k(audio_path)
    result = _model.transcribe(audio, language="bn", task="transcribe")
    
    # Transliterate
    for segment in result.get("segments", []):
        corrected_text = ""
        for char in segment.get("text", ""):
            code = ord(char)
            if 0x0900 <= code <= 0x097F:
                corrected_text += chr(code + 0x0080)
            else:
                corrected_text += char
                
        latin_text = sanscript.transliterate(corrected_text, sanscript.BENGALI, sanscript.ITRANS)
        segment["text"] = latin_text.lower()

    alignment_scores = []
    for seg in result.get("segments", []):
        if "avg_logprob" in seg:
            alignment_scores.append(float(np.exp(seg["avg_logprob"])))

    aligned_result = None
    if _model_a is not None:
        try:
            aligned_result = whisperx.align(
                result["segments"],
                _model_a,
                _metadata,
                audio,
                device,
                return_char_alignments=True,
            )
        except Exception as e:
            print(f"[warn] Skipping alignment due to: {e}")
            aligned_result = {"segments": result["segments"]}
    else:
        aligned_result = {"segments": result["segments"]}

    did_analyze = False
    consonants_detailed = []
    y_pred = []
    batch_phonetic_data = []

    for segment in aligned_result["segments"]:
        words = segment.get("words")
        if isinstance(words, list) and words:
            for word_data in words:
                spoken_word = (word_data.get("word", "") or "").strip()
                if not spoken_word:
                    continue

                w_dur = 0.0
                w_start = word_data.get("start")
                w_end = word_data.get("end")
                if w_start is not None and w_end is not None:
                    w_dur = float(w_end) - float(w_start)

                if "chars" not in word_data or not isinstance(word_data["chars"], list):
                    seg_chars = segment.get("chars", [])
                    actual_chars = []
                    temp_idx = 0
                    for c in spoken_word:
                        found = False
                        for i in range(temp_idx, len(seg_chars)):
                            sc = seg_chars[i]
                            if sc.get("char", "").lower() == c.lower():
                                if "start" in sc and "end" in sc:
                                    actual_chars.append({"char": c, "start": sc["start"], "end": sc["end"]})
                                    found = True
                                temp_idx = i + 1
                                break
                        if not found:
                            actual_chars.append({"char": c})
                    
                    if w_start is not None and w_end is not None and spoken_word:
                        avg_dur = w_dur / len(spoken_word)
                        curr_t = float(w_start)
                        for ac in actual_chars:
                            if "start" not in ac or "end" not in ac:
                                ac["start"] = curr_t
                                ac["end"] = curr_t + avg_dur
                            curr_t = float(ac["end"])
                    
                    word_data["chars"] = actual_chars

                letter_chars = [c for c in word_data["chars"] if _is_letter(c.get("char", ""))]
                if not letter_chars:
                    continue

                last_pos = len(letter_chars) - 1
                prev_vowel_dur: float | None = None
                letter_pos = -1
                consonant_history = []

                for char_data in word_data["chars"]:
                    ch = char_data.get("char", "")
                    if not _is_letter(ch):
                        continue

                    letter_pos += 1
                    if "start" not in char_data or "end" not in char_data:
                        continue

                    dur = float(char_data["end"]) - float(char_data["start"])

                    if _is_vowel(ch):
                        prev_vowel_dur = dur
                        continue

                    if not _is_consonant(ch):
                        continue

                    verdict, reason = classify_short_long(dur, letter_pos, last_pos, prev_vowel_dur)
                    did_analyze = True
                    consonants_detailed.append({
                        "word": spoken_word,
                        "char": ch,
                        "duration": float(f"{dur:.3f}"),
                        "classification": verdict,
                        "reason": reason
                    })
                    consonant_history.append(f"'{ch}'")
                    y_pred.append(verdict)
                    
                consonant_data_str = ", ".join(consonant_history) if consonant_history else "No consonants."
                batch_phonetic_data.append(f"Word: {spoken_word} | Consonants to classify: {consonant_data_str}")

    llm_analysis_text = ""
    y_true = []
    if batch_phonetic_data:
        batch_text = "\n".join(batch_phonetic_data)
        try:
            resp = meaning_chain.invoke({"batch_data": batch_text})
            full_response = resp.content.strip()
            llm_analysis_text = full_response
            
            if "```json" in full_response:
                parts = full_response.split("```json")
                llm_analysis_text = parts[0].strip()
                json_str = parts[1].split("```")[0].strip()
                try:
                    json_data = json.loads(json_str)
                    for word_labels in json_data:
                        if isinstance(word_labels, list):
                            y_true.extend(word_labels)
                except Exception as je:
                    print(f"\n[warn] Could not parse Validation JSON: {je}")
        except Exception as e:
            print(f"\n[Linguistic Analysis Failed]: {e}")
            provider = "Groq" if groq_api_key else "LM Studio"
            llm_analysis_text = f"{provider} is offline or unavailable. Details: {e}"

    metrics = {}
    if alignment_scores:
        metrics["alignment_confidence"] = float(np.mean(alignment_scores))

    if y_true and y_pred:
        min_len = min(len(y_true), len(y_pred))
        y_true_final = y_true[:min_len]
        y_pred_final = y_pred[:min_len]

        acc = accuracy_score(y_true_final, y_pred_final)
        f1_macro = f1_score(y_true_final, y_pred_final, average="macro", zero_division=0)
        prec_macro = precision_score(y_true_final, y_pred_final, average="macro", zero_division=0)
        rec_macro = recall_score(y_true_final, y_pred_final, average="macro", zero_division=0)
        
        f1_long = f1_score(y_true_final, y_pred_final, labels=["Long"], average="macro", zero_division=0)
        prec_long = precision_score(y_true_final, y_pred_final, labels=["Long"], average="macro", zero_division=0)
        rec_long = recall_score(y_true_final, y_pred_final, labels=["Long"], average="macro", zero_division=0)

        # Build confusion matrix
        labels_list = sorted(list(set(y_true_final + y_pred_final)))
        cm = confusion_matrix(y_true_final, y_pred_final, labels=labels_list)
        
        metrics.update({
            "accuracy": float(acc),
            "precision_macro": float(prec_macro),
            "recall_macro": float(rec_macro),
            "f1_macro": float(f1_macro),
            "precision_long": float(prec_long),
            "recall_long": float(rec_long),
            "f1_long": float(f1_long),
            "confusion_matrix": {
                "labels": labels_list,
                "values": cm.tolist()
            }
        })

    return {
        "segments": aligned_result.get("segments", []),
        "consonants_detailed": consonants_detailed,
        "llm_analysis": llm_analysis_text,
        "metrics": metrics,
        "expected_labels": y_true,
        "predicted_labels": y_pred
    }

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    audio_file = "your_bengali_audio2.wav"
    print(f"Running standalone analysis on {audio_file}...")
    res = analyze_audio(audio_file)
    print("\n--- Output Summary ---")
    print(f"Segments Count: {len(res['segments'])}")
    print(f"Consonants classified: {len(res['consonants_detailed'])}")
    print(f"Metrics: {res['metrics']}")
    print(f"LM Studio Analysis:\n{res['llm_analysis']}")
