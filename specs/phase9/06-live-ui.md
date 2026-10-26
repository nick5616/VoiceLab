# 06 Live UI (wave B)

Wave A is merged on `main`:
- **Pitch** (spec 01): mode-aware `FrameAnalyzer(sample_rate, interval, mode, noise_floor)`.
- **Input** (spec 02): `engine.capture_info`, WASAPI at 48 kHz with a resampler, and `denoise.noise_report`.
- **Singing** (spec 03): `analysis.perceived_cents` / `perceived_track`, and gap-tolerant `vibrato_stats`.
- **Coach** (spec 04): `coach.take_quality`, `coach.advice`, `profile_store.TARGET_PRESETS`, `Profile.in_both`.

You make the live screen honest and useful. Spec 07 does the take cards, targets, progress and `main.py` in parallel. Spec 05 does formant measurement in `analysis.py`.

**Files you own:** `live_panel.py`, `ui_common.py`, `live_views.py`, `widgets.py`, `engine.py`, new `mic_check.py`, and new `tests/test_live_ui.py`. You may edit `tests/test_app.py` only where your changes require it.

**Don't touch:** `main.py`, `take_card.py`, `targets_dialog.py`, `progress.py`, `profile_store.py`, `analysis.py`. Put the calls you need there under "Requests for integration". Spec 07 is told to make these calls on `main.py`'s side:
- `engine.set_mode(mode)` on mode switch;
- `engine.noise_floor()` saved into the profile on close and passed back as `engine.open_stream(..., noise_floor=…)`;
- `live_panel.set_targets(targets)`;
- `live_panel.show_mic_check()` from a menu or button, and on first launch.

## Measured problems (usefulness, speech and singing audits)
- **The live pitch readout and meters show ghosts.** Spec 01 has largely fixed this, but the meters still draw whatever arrives.
- **Unreliable readings are drawn as confident bars.** The bars say "Breathy" or "Muffled" when the cause is the noise.
- **Colours have no basis.** With no target set, `bar_color` shades blue, purple and red by position, so red reads as an alarm.
- **Meter ranges have no source.** ΔF 900–1300, CPP 8–30, H1*–H2* −5–15 and ring −30–0 carry no citation. The user's ΔF of 845–1024 sits under the bar's 900 floor.
- **The pitch bar is a poor fit for speech.** It spans 60–1200 Hz on a log scale, so the 140–250 Hz speech zone is a thin slice.
- **Labels misstate what's measured.**
  - "Fold Contact (H1*–H2*)" is not contact; it's a noisy proxy.
  - "Clarity (CPP)" also falls with noise and reverb.
  - "Ring" reads as "better" when high, with no meaning for speech.
- **Live smoothing is too short for resonance.** The EMA's τ is 200 ms, shorter than a vowel, so the ΔF meter tracks the vowel sequence. With a ±30 Hz target, the verdict flips 1.5–3.6 times a second.
- **The note readout goes green on pitch alone**, even when resonance is off target.
- **Singing tuning is judged on raw f0**, so it flips every 80 ms on vibrato.
- **The hiss check can't see the user's noise.** `ui_common.hiss_note` uses a 6 dB gap test that never fires on rumble or tones.
- **There is no mic check.** Takes record at −38 to −47 dBFS; the user needs to be 10–15 cm from the mic.

## Do
1. **Mode and noise-floor plumbing in `engine.py`.**
   - `set_mode(mode)` and `noise_floor()`.
   - `open_stream(..., noise_floor=None)` seeds the `FrameAnalyzer`.
   - The live frame keeps `band_level`; spec 03's Voice Map needs it.
2. **Honest meters (`ui_common.py`, `live_panel.py`).**
   - **Mode-dependent scales:**
     - speech pitch 80–400 Hz;
     - sing pitch to 1500 Hz;
     - ΔF scale wide enough for the user (about 800–1400 Hz);
     - ring hidden or de-emphasised in speak mode.
   - **Cited ranges:** give every range a source comment in `METRIC_DEFS`.
   - **Neutral colours:** a neutral hue for position, green only for on target, amber only for off target.
   - **Relabels:**
     - "Light ↔ Heavy (H1*–H2*)", with a caution above 250 Hz;
     - "Clarity (CPP, noise-sensitive)";
     - "apparent" on any tract length.
   - **Unreliable readings:** show "—" or a hatched bar with a short reason, never a confident value. That covers no voice, outside the formant-reliable pitch range, and noise too high per `coach.take_quality`'s `trust` (or a live equivalent you compute from the rolling SNR).
3. **Stable verdicts.**
   - Keep the 200 ms EMA for bar motion.
   - Base the on/off-target verdict for resonance and pitch on a 1–2 s voiced-time median, with hysteresis.
   - The note readout goes green only when `in_both` holds: pitch and resonance both on target, when both targets are set.
   - In sing mode, colour tuning by `perceived_cents`, and draw the raw trace separately.
4. **Live coach line.** One line in the live panel header, built from `coach.advice` on a rolling few-second summary. Update it at most about every 2 s so it doesn't flicker.
5. **Noise badge.**
   - Replace `hiss_note` and the 30 dB banner with a badge from `denoise.noise_report` on the last few seconds: "whine", "rumble", "quiet: move closer", "clipping".
   - Click the badge to open the mic check.
   - Warn when `engine.capture_info["path"]` isn't WASAPI.
6. **Mic check dialog (`mic_check.py`).**
   - Record 3 s of silence and then a 3 s sustained "ah", through the engine.
   - Run `noise_report` and show: noise floor, voice level, SNR, rumble and whine found, and the capture path, rate and latency.
   - Give plain advice: distance 10–15 cm and slightly off-axis; raise the Windows input level after moving closer; try a rear USB port; turn off Windows enhancements. The longer-term fix is a USB interface with an XLR mic.
   - Store the result so spec 07 can save it in the profile. Return a dict.

## Verify
- Offscreen Qt tests feed synthetic frames, with `mic_noise` and clean, through the live panel, and assert:
  - "—" appears for untrusted metrics;
  - no green shows when only pitch is on target;
  - the verdict doesn't flip on a synthetic /a/–/i/ alternation around a target;
  - tuning stays green on ±50 ¢ vibrato around the target.
- The mic check runs offscreen with a fake engine, gives the right advice for quiet input and for whine input, and never opens real hardware.
- Screenshots go in `C:\tmp\phase9-work\06-live-ui\`: speak and sing, wide and narrow, before and after. Describe what changed.
