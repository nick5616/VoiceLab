# 02 Input chain

Capture cleanly at the device's native rate, characterise noise, and give the analysis a way to remove the user's steady interference tones without damaging the voice.

## Measured problem (from the signal-chain audit)
**How the app captures now**
- `engine.py` opens `sd.InputStream` at 44.1 kHz, mono, float32, blocksize 512, through `sd.default.hostapi` (MME), with default latency.
- The USB PnP mic (VID_0C76/PID_161E, generic Windows USB audio driver, no enhancements) is **48 kHz only**:

| Host API | Rates | Low / high latency |
|---|---|---|
| WASAPI | 48 kHz only | 3/10 ms |
| WDM-KS | 48 kHz only | 10/85 ms |
| MME, DirectSound | any rate (Windows resamples) | 90/180 ms, 120/240 ms |

- Lines at 13.1 and 14.1 kHz (44.1 kHz minus 31 and 30 kHz) hint at aliasing in that conversion.

**Noise in the takes**
- *Tones:* steady lines at integer kHz, identical in every take over two days and unrelated to the voice (correlation −0.14 to +0.27). The strongest is 4 kHz at −73.5 dBFS, 17–31 dB above the local floor; also 6, 8, 10, 12 and 18 kHz. This is the user's "screech". It is probably USB-frame interference.
- *Broadband floor:* about −58 dBFS, with 86% of its power below 1 kHz. There is hum near 56–62 Hz.
- *Voice:* −38 to −47 dBFS, peaks 0.06–0.19, SNR 11.5–19.7 dB.
- *Hiss check:* `ui_common.hiss_note`'s 6 dB band-gap test sees −0.6 to +0.6 dB on these takes, so it can never fire for this noise.

**Preprocessing tried**
- *Notch at 4 kHz:* wrecks F4 on a female /a/ (−905 Hz, ΔF −64 Hz), because the user's target F4 sits near 3.8–4.3 kHz. **Don't use notches.**
- *Spectral gating:* moves F1 by up to −100 Hz. **Don't use it for measurement.**
- *Coherent tone subtraction:* recommended. Estimate each line's amplitude and phase with slow demodulation (a low-pass of about 4 Hz, about 40 ms to converge) and subtract it.
  - It cuts the 4 kHz line by 14–30 dB on real takes and halves the F4/ΔF error on synthetic vowels.
  - Lines must be **detected from noise-only audio** (lead-in or pauses). Detecting them on voiced audio removes the voice's own harmonics.
  - The lines drift by about 10 ppm against the clock, so it must be adaptive, not a fixed template.
  - Risk: a voice harmonic within about 4–15 Hz of a line loses energy. The 4 kHz line hits a 200 Hz voice's 20th harmonic.

## Do
1. **`engine.py`**
   - Prefer the WASAPI host API when the device offers it, at the device's native rate (`default_samplerate`).
   - Resample to the app's `SAMPLE_RATE` (44100) with `scipy.signal.resample_poly` (48000 → 44100 is 147/160) in the stream callback or worker. A stateful polyphase filter (or overlap) is needed so block boundaries don't click; measure that.
   - Fall back to the current MME path if WASAPI fails to open, and record which path was used (`engine.capture_info`: host API, native rate, latency).
   - Keep the public API and the EGG two-channel path working.
   - Measure and report the added latency.
2. **New `denoise.py`**
   - `find_lines(noise_audio, sr)`: stationary narrowband lines, given as frequency and level. Require a strong margin over the local floor and persistence across the segment. Zero false detections on clean voice-free synthetic noise is the bar.
   - `noise_audio_from_take(audio, sr, voiced_mask_or_levels)`: pick the noise-only stretches (lead-in or pauses).
   - `cancel_lines(audio, sr, lines)`: adaptive coherent subtraction that tracks drift. It must never touch frequencies other than the given lines.
   - `noise_report(audio, sr, voiced)`: noise floor (dBFS), rumble share (fraction of noise power below 150 Hz), list of lines, voiced level, SNR (full-band and voice-band), peak, clipping and DC. Return a plain dict for wave B's quality tier and mic check.
   - A streaming variant for live use is optional. Say whether it fits the 25 ms tick.
3. Don't wire `denoise` into `analysis.py` or the UI. Wave B does that. Document the intended call order in the module docstring.

## Verify (`tests/test_denoise.py`, `tests/test_engine.py`)
- `find_lines` on `mic_noise.mic_noise` finds the 4, 6, 8 and 10 kHz lines (at least), with no false lines on `mic_noise.broadband` alone or on a clean `synth_vowel`.
- `cancel_lines` cuts the 4 kHz line by ≥ 15 dB on synthetic voice plus `mic_noise`. F1–F4 measured by `analysis.to_formants` on a female /a/ at 200 Hz moves under 20 Hz, versus the clean signal, compared with no cancelling. H1–H2, CPP and ring change by under 0.5 dB on a clean vowel.
- A slowly drifting line (+10 ppm) is still cancelled.
- `noise_report` on `mic_noise` matches its construction: floor about −58 dBFS, rumble share above 0.5, lines found.
- Engine: monkeypatch `sounddevice` to simulate a 48 kHz-only WASAPI device. Assert the stream opens at 48 kHz, output arrives at 44.1 kHz with the correct duration, and a 1 kHz sine passes through without block-boundary clicks (residual under −60 dB). Also assert the MME fallback works.
- Real takes: report the 4 kHz line level before and after `cancel_lines`, using each take's quietest 1.5 s to detect lines, plus `noise_report` per take.
