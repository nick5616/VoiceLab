"""Optional vocal isolation with Demucs (htdemucs), for analysing a voice that
was recorded over music, such as a sung take over a backing track or a
reference song. It runs offline, on the CPU unless a GPU is available.

Requires the extras in requirements-isolation.txt (PyTorch + Demucs). The model
weights (~80 MB) are downloaded on first use.
"""
import numpy as np

MODEL_NAME = "htdemucs"
_model = None


def available():
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _load_model():
    global _model
    if _model is None:
        from demucs.pretrained import get_model
        _model = get_model(MODEL_NAME)
        _model.eval()
    return _model


def isolate_vocals(audio, sample_rate):
    """Mono vocal stem of `audio` at the same sample rate."""
    import torch
    import torchaudio.functional as AF
    from demucs.apply import apply_model

    model = _load_model()
    wav = torch.tensor(np.asarray(audio, dtype=np.float32).flatten())
    if sample_rate != model.samplerate:
        wav = AF.resample(wav, sample_rate, model.samplerate)
    wav = wav.expand(model.audio_channels, -1)
    # Demucs expects input normalized the way it was trained.
    mean, std = wav.mean(), wav.std() + 1e-8
    device = "cuda" if torch.cuda.is_available() else "cpu"
    with torch.no_grad():
        sources = apply_model(model, ((wav - mean) / std)[None], device=device,
                              split=True, overlap=0.25, progress=False)[0]
    vocals = (sources[model.sources.index("vocals")] * std + mean).mean(0)
    if sample_rate != model.samplerate:
        vocals = AF.resample(vocals, model.samplerate, sample_rate)
    return vocals.cpu().numpy().astype(float)
