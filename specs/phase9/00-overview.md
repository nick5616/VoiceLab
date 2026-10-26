# Phase 9: real-world reliability

Four audits (`C:\tmp\phase9-audit\`) measured VoiceLab against the user's real takes and synthetic ground truth. Their consensus:

1. **The pitch tracker reports noise as voice.**
   - 15–45% of "voiced" frames in real takes are ghosts:
     - 60–70 Hz frames locked onto desk rumble and hum;
     - ~1000 Hz frames, the 4:1 subharmonic of a steady 4 kHz interference tone from the USB mic.
   - It also halves real f0 on about 21% of truly voiced frames.
   - Stored intonation range is 44–49 semitones; real speech is 4–28.
   - Every downstream number inherits the ghosts: pitch range, contour, Voice Map, time-in-target, singing stats, register and medians.
2. **Formant and weight readings are fragile.**
   - F1 snaps to H1 at f0 ≥ ~200 Hz.
   - The formant ceiling jumps between takes (ΔF 995 vs 845 Hz on takes 13 s apart).
   - CPP isn't comparable to published CPPS.
   - Targets are tighter than the measure's own spread.
3. **The input chain is poor and the app doesn't notice.**
   - Takes sit at −38 to −47 dBFS with SNR 11–20 dB.
   - The mic is 48 kHz-only but is opened at 44.1 kHz through MME (90 ms latency).
   - The `hiss_note` check can't see rumble or tones.
4. **The app reports numbers, not coaching.**
   - Unreliable readings are shown as fact.
   - Colours have no basis.
   - The note readout goes green on pitch alone, ignoring resonance.

## Waves

| Spec | Wave | Owns (may create or edit) |
| ---- | ---- | ------------------------- |
| [01 Pitch & voicing](01-pitch-voicing.md) | A | `analysis.py` from the top through `analyze_take` (pitch, voicing, noise floor, `FrameAnalyzer`, `analyze_take`), **except** the formant/weight functions `cpp`, `_resonance_gain_db`, `h1_h2_corrected`, `ring_db`, `formant_values`, `to_formants`, `_ceiling_score`, `choose_formant_ceiling`; `shift.py`; new `tests/test_pitch.py` |
| [02 Input chain](02-input-chain.md) | A | `engine.py`, new `denoise.py`, `tests/test_engine.py`, new `tests/test_denoise.py` |
| [03 Singing](03-singing.md) | A | `analysis.py` from `NOTE_MIN_SECONDS` to the end of `score_exercise`; `exercise.py`, `register.py`, `voice_map.py`, `take_register.py`; new `tests/test_singing.py`; `tests/test_register.py` |
| [04 Coach core](04-coach-core.md) | A | new `coach.py`, new `tests/test_coach.py`, `profile_store.py` (target and tolerance logic only) |
| 05 Formants & weight | B | the formant/weight functions excluded from 01, `analyze_vowels`, `vowel_nucleus` (spec written after wave A) |
| 06 Live UI, 07 Take & progress UI | B | UI modules (specs written after wave A) |

Wave A specs share `analysis.py` but own disjoint functions. Don't reformat, reorder or rename anything you don't own, so the merges stay clean. If you need a constant at the top of `analysis.py`, add it next to the functions that use it, in your own zone.

## Rules for every agent

1. **Environment.** Windows 11. Use `C:\Users\nickb\repos\VoiceLab\venv\Scripts\python.exe` by absolute path and don't create another venv. Run `python -m pytest tests -q -k "not isolation"`. Specs are uncommitted, so read them from `C:\Users\nickb\repos\VoiceLab\specs\phase9\` by absolute path.
2. **Baseline first.** Run the full suite before changing anything and note the result. When you finish, the whole suite must pass, and existing tests may only change where your spec changes the behaviour they assert. Say which ones you changed and why.
3. **Stay inside your files.** Anything you need elsewhere goes under "Requests for integration" in your report.
4. **Match the code style.** Plain module-level functions; constants at the top of their zone with a short trailing comment giving units or the source; one-line docstrings; no type annotations. No new runtime dependencies: numpy, scipy, parselmouth, pyworld, sounddevice and soundfile are already there. Leave `python -m pyflakes` clean on the files you touched.
5. **Synthetic ground truth.**
   - Build test signals from `tests/test_analysis.py::synth_vowel` (import it, don't copy it) plus **`tests/mic_noise.py`**. That noise model is measured from the user's mic: rumble-heavy broadband at about −58 dBFS plus steady tones at integer kHz, the strongest at 4 kHz and −73.5 dBFS.
   - Use `mic_noise.at_level(voice, -42)` for realistic voice level (SNR about 16 dB).
   - Never use a microphone or speakers.
6. **Real takes, read-only.**
   - Validate on `C:\Users\nickb\VoiceLab\*.wav`: the user's private speech takes, 7 so far. Never modify, copy into the repo, commit or upload them.
   - Put scratch scripts and plots under `C:\tmp\phase9-work\<spec>\`.
   - Report before/after numbers on the real takes in your report.
7. **Thresholds need a source.** Cite literature, or one of the audit measurements quoted in your spec, or your own measurement. Mark anything from memory as unverified.
8. **Commit** on your worktree branch with message `phase 9: <spec name>` and the attribution line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Don't push, merge or rebase.
9. **Final report.** Include files changed, the public API added or changed (signatures), test counts, before/after numbers (synthetic and real), the design decisions with the source for each, known limitations and requests for integration.
