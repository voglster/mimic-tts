"""Qwen3-TTS FastAPI server — voice cloning & synthesis.

Models load on demand and unload after 15s idle to free VRAM for Ollama.
"""

import asyncio
import io
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from qwen_tts import Qwen3TTSModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CLONE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
CUSTOM_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
UNLOAD_AFTER = 15  # seconds of idle

_voice_prompts: dict[str, object] = {}  # keyed by voice/style name

_models: dict[str, Qwen3TTSModel] = {}
_lock = threading.Lock()
_last_used: float = 0
_unload_task: asyncio.Task | None = None


def _load_model(model_id: str) -> Qwen3TTSModel:
    logger.info("Loading %s …", model_id)
    t0 = time.monotonic()
    model = Qwen3TTSModel.from_pretrained(
        model_id,
        device_map="cuda:0",
        dtype=torch.bfloat16,
    )
    logger.info("Loaded %s in %.1fs", model_id, time.monotonic() - t0)
    return model


def _unload_all():
    with _lock:
        if not _models:
            return
        names = list(_models.keys())
        _models.clear()
        _voice_prompts.clear()
        torch.cuda.empty_cache()
        logger.info("Unloaded models (%s) — VRAM freed", ", ".join(names))


def get_model(key: str) -> Qwen3TTSModel:
    """Load model on demand, reset idle timer."""
    global _last_used
    model_id = CLONE_MODEL_ID if key == "clone" else CUSTOM_MODEL_ID
    with _lock:
        _last_used = time.monotonic()
        if key not in _models:
            _models[key] = _load_model(model_id)
        return _models[key]


async def _unload_watcher():
    """Background task that unloads models after UNLOAD_AFTER seconds idle."""
    while True:
        await asyncio.sleep(5)
        with _lock:
            if not _models:
                continue
            idle = time.monotonic() - _last_used
        if idle >= UNLOAD_AFTER:
            _unload_all()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _unload_task
    _unload_task = asyncio.create_task(_unload_watcher())
    yield
    _unload_task.cancel()
    _unload_all()


app = FastAPI(title="Qwen3-TTS API", lifespan=lifespan)


def wav_response(samples, sample_rate: int, filename: str = "output.wav"):
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ── Edge-TTS-compatible endpoint ─────────────────────────────────────
@app.post("/tts")
async def tts(
    text: str = Form(...),
    language: str = Form("English"),
    speaker: str = Form("Ryan"),
    instruct: str = Form(""),
):
    """Standard TTS with built-in voices. Swap this in where you use edge-tts."""
    model = get_model("custom")
    wavs, sr = model.generate_custom_voice(
        text=text,
        language=language,
        speaker=speaker,
        instruct=instruct or None,
    )
    return wav_response(wavs[0], sr)


# ── Voice cloning endpoints ──────────────────────────────────────────
@app.post("/clone/register")
async def register_voice(
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(...),
    name: str = Form("default"),
):
    """Upload a reference audio clip (~3+ seconds) to register a voice/style."""
    model = get_model("clone")
    audio_bytes = await ref_audio.read()

    ref_dir = Path("reference") / name
    ref_dir.mkdir(parents=True, exist_ok=True)
    ref_path = ref_dir / "audio.wav"
    ref_path.write_bytes(audio_bytes)
    (ref_dir / "text.txt").write_text(ref_text)

    _voice_prompts[name] = model.create_voice_clone_prompt(
        ref_audio=str(ref_path),
        ref_text=ref_text,
    )
    return {"status": "ok", "name": name, "message": f"Voice '{name}' registered. Use /clone/tts?name={name} to synthesize."}


@app.post("/clone/tts")
async def clone_tts(
    text: str = Form(...),
    language: str = Form("English"),
    name: str = Form("default"),
):
    """Synthesize speech using a registered voice/style."""
    model = get_model("clone")

    if name not in _voice_prompts:
        ref_path = Path("reference") / name / "audio.wav"
        ref_text_path = Path("reference") / name / "text.txt"
        if ref_path.exists() and ref_text_path.exists():
            _voice_prompts[name] = model.create_voice_clone_prompt(
                ref_audio=str(ref_path),
                ref_text=ref_text_path.read_text(),
            )
        else:
            raise HTTPException(400, f"No voice '{name}' registered. POST to /clone/register first.")

    wavs, sr = model.generate_voice_clone(
        text=text,
        language=language,
        voice_clone_prompt=_voice_prompts[name],
    )
    return wav_response(wavs[0], sr)


# ── One-shot clone (no pre-registration) ─────────────────────────────
@app.post("/clone/oneshot")
async def clone_oneshot(
    text: str = Form(...),
    language: str = Form("English"),
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(...),
):
    """Clone and synthesize in one call — convenient but slower per request."""
    model = get_model("clone")
    audio_bytes = await ref_audio.read()
    buf = io.BytesIO(audio_bytes)

    wavs, sr = model.generate_voice_clone(
        text=text,
        language=language,
        ref_audio=(buf, None),
        ref_text=ref_text,
    )
    return wav_response(wavs[0], sr)


@app.get("/voices")
async def list_voices():
    """List available built-in voices."""
    return {
        "voices": [
            {"name": "Ryan", "language": "English"},
            {"name": "Aiden", "language": "English"},
            {"name": "Vivian", "language": "Chinese"},
            {"name": "Serena", "language": "Chinese"},
            {"name": "Uncle_Fu", "language": "Chinese"},
            {"name": "Dylan", "language": "Chinese"},
            {"name": "Eric", "language": "Chinese"},
            {"name": "Ono_Anna", "language": "Japanese"},
            {"name": "Sohee", "language": "Korean"},
        ]
    }


@app.get("/clone/voices")
async def list_registered_voices():
    """List all registered clone voices/styles."""
    ref_dir = Path("reference")
    on_disk = {p.parent.name for p in ref_dir.glob("*/audio.wav")} if ref_dir.exists() else set()
    return {"voices": sorted(on_disk | _voice_prompts.keys())}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "models_loaded": list(_models.keys()),
        "registered_voices": list(_voice_prompts.keys()),
    }
