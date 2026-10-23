# VoiceLab

A real-time vocal analysis tool for singers, voice actors, people doing gender-affirming voice training, and anyone training their voice. Live meters respond as you sing or speak, and each recorded take gets a detailed report, so you can consciously shape your instrument.

## What It Measures

| Metric                     | What it reflects                                                                                   | Live |
| -------------------------- | -------------------------------------------------------------------------------------------------- | ---- |
| **Pitch**                  | Fundamental frequency (F0), shown as note name, cents and Hz                                        | Yes  |
| **Resonance (ΔF)**         | Formant dispersion over F1–F4 and the apparent vocal tract length it implies (dark → bright)        | Yes  |
| **Fold Contact (H1*–H2*)** | First-minus-second harmonic level, corrected for F1/F2. A proxy for vocal weight (heavy → light)    | Yes  |
| **Clarity (CPP)**          | How periodic the voice is (breathy/rough → clear). Live: a fast per-frame cepstral peak over 0–4 kHz. Takes: smoothed CPPS from Praat, the measure published norms use. The two scales differ (the live number reads several dB higher), so compare like with like. Both read low in background noise | Yes  |
| **Ring (2–4 kHz)**         | Harmonic energy in the singer's-formant region relative to 0–2 kHz (muffled → ringing)              | Yes  |
| **Intonation Range**       | 5th–95th percentile pitch spread in semitones (flat → expressive). The live meter uses the last 6 s | Yes  |
| **Register**               | Estimate of chest (M1) versus head (M2) voice, with a "mix" zone between and a "fry" flag. See [Register](#register) | Yes  |
| **Contact quotient (CQ)**  | Share of each vocal-fold cycle the folds are in contact, from an EGG (light → pressed). Only with an EGG | Yes, with an EGG |
| **Level**                  | Voiced level in dBFS. Uncalibrated, so compare takes on the same mic and gain only                  | Yes  |

Each take also shows an interactive graph: pitch track on top, F1/F2/F3 formant tracks below. Hover to inspect values at any point in time.

### Why these metrics

- **Resonance** uses ΔF rather than individual formants because single formants mostly reflect which vowel you're saying. In a 2025 study a single speaker deliberately varied pitch, resonance and weight; ΔF tracked resonance with 94% ranking accuracy and stayed largely independent of the other two ([Netzorg et al., Interspeech 2025](https://www.isca-archive.org/interspeech_2025/netzorg25_interspeech.pdf)).
- **Weight** has no reliable acoustic-only measure. The same study found that only electroglottograph contact quotient and loudness tracked it, while spectral slope also moved with resonance. With an EGG connected, **contact quotient** covers weight directly (see [EGG](#egg)). Without one, Fold Contact, Clarity and Level are proxies, and are best used to compare your own takes against each other.
- **Register** is an estimate, not a measurement. No validated, speaker-independent way exists to tell chest from head voice from a microphone alone, so VoiceLab combines a few noisy cues and says so ("estimate") until you calibrate it on your own labelled takes.
- **Formant ceiling** (the frequency range Praat searches for formants) is chosen once for your voice, not per take. Per-take choice made two takes recorded 13 s apart differ by 150 Hz in ΔF. VoiceLab sets it from the vowel check, or from your first few good takes, by testing ceilings from 4–6.5 kHz and rejecting any that produce fake formants. Takes and the live meters then all use that one ceiling.
- **Resonance is measured on steady vowels**, each vowel counted once, so a passage with more "ee"s doesn't read brighter than one with more "ah"s. Where too few steady vowels exist, the take says so instead of guessing.
- **High pitch**: above ~230 Hz the harmonics are too far apart for the first formant to be tracked. F1 locks onto a harmonic, which made resonance read wrong on real takes at 200–215 Hz. Readings that depend on F1 are withheld there and shown as "—" with the reason, rather than a wrong number.

## Requirements

- Python 3.10+
- A working microphone

## Setup

```bash
# Clone the repo
git clone https://github.com/nick5616/VoiceLab.git
cd VoiceLab

# Create and activate a virtual environment
python3 -m venv venv

# if: macOS / Linux
source venv/bin/activate

# if: Windows
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Optional: vocal isolation for imported songs (installs PyTorch, ~1 GB)
pip install -r requirements-isolation.txt
```

## Running

```bash
# if: not already active
source venv/bin/activate

python main.py
```

## Usage

1. The **Live** panel listens as soon as the app opens. Pick a view, and the meters underneath stay visible on every tab:
   - **Pitch**: scrolling pitch trace on a note-name axis that follows your range.
   - **Spectrum**: a scrolling spectrogram with F1–F4 tracks overlaid, and the current spectrum on the right with harmonics (blue dots) and formants (dashed lines) on the same frequency axis. Use it for vowel shaping and formant tuning, for example lining up a harmonic with F1. F1 drops out wherever it can't be told apart from the pitch, usually above ~230 Hz, and resonance isn't judged there, but you can still see harmonics brighten as they pass through a resonance.
   - **Pitch × Resonance**: your live position with a 3-second trail, rough population regions as orientation (not targets), and each recorded take as a numbered point.
2. Click the red microphone button to record a take, speak or sing, then click stop.
3. The app analyzes the take and adds a card to the top of the list.
4. Compare takes as you experiment with different placements, vowel shapes, or resonance strategies.
5. Click **Play** on any card to hear it back alongside its metrics.

On a wide window the live panel and your takes sit side by side; narrower, they stack, with the live panel kept compact. Drag the divider to give either more room. The mouse wheel scrolls the takes wherever the pointer is; a Shift slider only takes the wheel after you click it.

**Mic** (bottom right) picks the input device and is remembered. Only devices of the system's default audio API are listed, so each mic appears once. When the device has two or more inputs, an **EGG** choice appears next to it (see [EGG](#egg)).

### Takes folder

On first launch VoiceLab offers to save takes to a folder you choose; the folder button at the bottom chooses or changes it any time. Each take is saved there as a WAV file plus a JSON file with the same name holding its analysis, so the folder loads instantly next session:

```
2026-10-18 10-32-05.wav            recorded take
2026-10-18 10-32-05.json           its analysis
2026-10-18 10-40-11 bright ee.wav  after naming it "bright ee"
```

- **Name** a take by clicking its title on the card; its files are renamed to match.
- **Delete** (trash button) removes the take and both its files, after asking.
- **Add** recordings by copying audio files (WAV, FLAC, OGG, MP3, AIFF) into the folder. They're analysed when the folder loads and get a JSON file then; they're treated like imported files.
- **Reload** (↻) picks up files added, renamed or deleted outside VoiceLab.
- The newest 20 takes are shown; **Show older takes** pages in the rest.
- Choosing a folder saves the current session's unsaved takes into it.

Without a folder, takes last only for the session (their summaries still go to the progress log).

### Progress

**Progress** opens a chart per measurement over time: a dot per take, a yellow weekly median, your baseline (dashed) and target (green band), and a headline such as `this week 1094 Hz · +66 Hz since week of Sep 14`. Pitch changes are given in semitones.

It covers pitch, resonance, the vowel check's vowel-balanced ΔF, Fold Contact, Clarity, Ring, intonation range, head and chest share of the register estimate, contact quotient (with an EGG), and for singing, exercise accuracy (share of notes in tune and average error), vibrato rate and extent, and register breaks per minute. Filter by Speak or Sing and by period. Imported files are left out unless **Include imported files** is ticked, since they may be someone else's voice. Weekly medians matter more than any single take: one tired evening shouldn't look like a setback.

### Clipping

If the input overloads, a red **CLIPPING** badge appears in the live panel, and the take card says how many times it happened. Clipping squares off the waveform, adding harshness that inflates Ring and lowers Clarity, so lower the mic gain or move back a little.

### Noise and how much to trust a take

**Every take gets a quality rating: good, fair or poor, with its reasons**, e.g. "quiet: a little closer to the mic", "desk rumble" or "steady electrical whine detected". It comes from the voice level, the voice-to-noise ratio (SNR), clipping, and how much of the pitch signal looked like noise rather than voice. A reading the recording can't support shows **"—" and the reason** instead of a bar. For example, Clarity and the Heavy ↔ Light weight reading need about 20 dB SNR, because at 16 dB Clarity already reads 4–5 dB low. A poor take only suggests a re-record, and Progress leaves poor takes out unless you tick them back in.

The **noise badge** in the live panel names the problem: whine, rumble, "quiet: move closer", clipping, or the SNR when nothing specific stands out. Click it to open the **mic check**: 3 s of silence and then a 3 s "ah". It reports your noise floor, voice level and SNR, any rumble or whine, and the audio path. It then suggests what to change. Run it once when you set up, and again after you change mic, port or position.

What VoiceLab does about noise, measured on real takes from a cheap USB desk mic:

| Problem | What it did | What VoiceLab does now |
| --- | --- | --- |
| Desk rumble and mains hum (most of the noise power was below 1 kHz) | The pitch tracker read rumble, and a subharmonic of the whine, as "voice". 15–45% of voiced frames were fake, so intonation range read 44–49 semitones. | A frame counts as voice only when it is clearly periodic, clearly above the take's noise floor, and part of a run. Doubtful octaves are cross-checked with a second tracker (WORLD harvest). On the real takes, agreement with an independent reference went from 75% to 94%, fake voice in silence to 0%, and intonation range to 2.5–15 semitones. |
| Steady whine at exact multiples of 1 kHz (strongest at 4 kHz) | It sits on the 4th formant and skewed resonance by up to ~20 Hz. | The tones are found in the pauses and subtracted precisely. The voice's own harmonics are protected, and there is no notch filter, because a notch at 4 kHz would cut into a real formant of a brighter voice. This cut the 4 kHz line by 21–30 dB on real takes. |
| Broadband hiss | It lowers Clarity and the weight reading. | Nothing to remove without harming the voice, so those readings are withheld when the SNR is too low. |

VoiceLab still doesn't run general noise suppression on your voice. Denoisers remove breath noise and fine harmonic detail along with the room noise, which would make a breathy voice measure clearer than it is. Spectral gating was tested, and it moved F1 by up to 100 Hz on real takes.

**Audio path.** VoiceLab opens your mic through Windows' WASAPI at the mic's own sample rate, and resamples once in-app. Many USB mics, including the one these measurements came from, only run at 48 kHz. Windows' older MME path (the fallback) converts the rate itself and adds about 90 ms of delay. The live panel says "slow audio path" when it had to fall back.

### Your mic

The biggest improvement is usually the setup, not the software:

- **Get closer.** Sit about 10–15 cm from the mic, slightly off to the side so your breath doesn't hit it. Each halving of distance gives about 6 dB more voice over the same noise. The takes these measurements came from sat at −38 to −47 dBFS, which is very quiet.
- **Then raise the input level** (Windows Sound settings → your mic → Properties → Levels) so your voice peaks well below clipping. Raising the level without moving closer turns the noise up too.
- **Put the mic on something soft** or an arm, off the desk, to cut rumble from typing and the desk itself.
- **Try a different USB port**, ideally one on the back of the PC (on the motherboard), or a short powered hub. Keep the cable away from power adapters and wireless dongles.
- **Turn off Windows audio "enhancements"** and any automatic gain for the mic.
- **The real fix for noise** is a small USB audio interface with an XLR microphone. It has a proper preamp and no USB whine.

**About the whine.** A steady high-pitched tone in your recordings isn't caused by a missing DAC. A DAC turns digital audio into sound for playback; recording goes the other way, through an ADC, and a USB mic has its own. Tones at exact multiples of 1 kHz match USB interference. USB sends data every millisecond, and cheap USB mics can pick that up in their own electronics. It is already in the recording, which is why VoiceLab finds and subtracts it. To find out whether it comes from the mic or the PC, record 10 s of silence on this mic and 10 s on another one in the same spot. Then compare them in the mic check.

### Register

Singers and voice trainers talk about **chest** and **head** voice. In the voice-science terms, chest is laryngeal mechanism **M1** (thicker, shorter vocal-fold contact, the usual speaking voice) and head or falsetto is **M2** (thinner, lighter folds), with **fry** as a third, very slow, creaky mechanism (M0), and **mix** for the ambiguous zone between (Henrich 2006; Roubeau, Henrich & Castellengo 2009). People raising their pitch or lightening their voice for feminization often aim to sit more in M2.

- **Live**: the **Register** meter runs from Chest to Head, with the mix zone marked on the bar, in both Speak and Sing modes. It reads "fry" when the pitch drops below about 70 Hz or tracks erratically.
- **On each take**: a stacked bar of chest / mix / head / fry shares, the number of **breaks** (an abrupt chest-to-head or head-to-chest switch within 150 ms), and the pitch track on the graph dotted blue (chest), purple (mix), red (head) or grey (fry).
- **How it's estimated**: from the first-minus-second harmonic level (H1\*–H2\*, higher in head voice), the spectral tilt from the first harmonic to the F3 peak, and Clarity, plus a weak prior from pitch (Kreiman, Gerratt, Garellek and colleagues; Henrich, d'Alessandro & Doval 2001). With an EGG it also uses contact quotient, which is much lower in M2 (Henrich, Smith & Wolfe 2011).
- **It is an estimate.** The defaults were tuned on synthetic voices only. A high chest-voice note (say 450 Hz) can read as "mix", and a breathy chest voice can read as head. Treat the colours as a guide to compare your own takes, not a verdict. Until calibrated, the meter and cards say "estimate".
- **Calibrating it to your voice**: on a take's card choose **Label register…** and pick **Chest** or **Head** (or **Clear label**). Label several takes of each, such as a spoken passage or a sung phrase in chest, and the same notes in head or falsetto. Once there are about 2 seconds of voiced audio on each side, VoiceLab fits the model to your labels, stores it in your profile, and the status bar says so. From then on it reads "calibrated to your voice", and shown takes are re-scored (older ones when you scroll to them). Labels live in each take's JSON file, so they need a takes folder; without one the calibration still sticks, but the labels are lost at the end of the session.
- **Targets**: **Register (head share)** is a target like the others, the percentage of voiced time in head voice. Setting it to 70–100% means "mostly M2-leaning".
- **Progress** charts head share, chest share and, for singing, register breaks per minute.

The sources are the ones cited at the top of `register.py` and `egg.py`.

### EGG

An **electroglottograph** (EGG) measures vocal-fold contact directly through two electrodes on the neck, so it gives **contact quotient** (CQ), the share of each cycle the folds touch. A higher CQ is a more pressed, heavier phonation; a lower one is lighter. CQ is the measure that tracked vocal weight best in the Netzorg et al. study above, and it also separates chest from head voice (Henrich, Smith & Wolfe 2011).

VoiceLab reads the EGG as a second audio input channel, the way FonaDyn and VoceVista do, so you need an EGG whose output reaches a **stereo interface**, with the EGG on one channel and the mic on the other.

**Setup**

1. Plug the EGG's output into one input of a stereo (2-input) audio interface, and the mic into the other. A device with two real line or mic inputs is better than a laptop's built-in jack.
2. Pick that device under **Mic**. When it has two or more inputs, an **EGG** choice appears beside it: set it to the channel the EGG is on (**channel 1** or **channel 2**). The choice is remembered. If the stream can't open with two channels, the error is shown and VoiceLab goes back to the mic alone; it never silently drops the EGG.
3. Set the gain so the EGG signal is **well above the noise but doesn't clip**.
4. **Turn off AGC (automatic gain control), compressors, noise gates and any DC-block or "enhance" processing** for both channels. They distort the contact waveform the CQ is read from.

**What you get.** While an EGG is on, an **EGG ●** badge shows in the live panel (grey when no usable signal arrives) with a **Contact quotient (EGG)** meter, and CQ feeds the register estimate. Takes are saved as stereo WAV files (mic first, EGG second), with the EGG channel noted in the take's JSON file, so they reload with the EGG intact. Each card shows its CQ, and the Voice Map offers CQ as a colour once an EGG has filled some cells. If the EGG channel looks flat or isn't picking up regular vocal-fold cycles, the status bar and card say so.

**Importing a stereo file** asks whether one channel is an EGG, and which. If so it's analysed with the EGG; if not, it's mixed to mono as before.

**Method.** Cycles are found from the peaks of the EGG's derivative (closing), and the contact phase ends where the signal falls to 3/7 of the cycle's height (the hybrid method of Howard 1995, after Rothenberg & Mahshie 1988 and Henrich et al. 2004). Compare CQ between sessions on the same set-up, not between devices or methods: electrode placement and the method itself shift the absolute number by a few hundredths.

### Shifting a take

**Shift** on a take card opens sliders that re-render the take with your voice moved. Let go of a slider and the shifted version renders and plays. **▶ Original** and **▶ Shifted** switch between them, and while the panel is open the card's Play button plays the shifted version.

| Slider           | Range          | How                                  |
| ---------------- | -------------- | ------------------------------------ |
| Pitch            | ±12 semitones  | Praat "Change gender" (PSOLA)        |
| Resonance        | −20% to +25%   | Praat: scales the formants           |
| Intonation range | 50–200%        | Praat: widens or flattens pitch movement |
| Tempo            | 70–140%        | Praat: changes speed without pitch   |
| Breathiness      | −100 to +100   | WORLD vocoder: aperiodicity          |
| Brightness       | ±6 dB/octave   | WORLD vocoder: spectral tilt         |

Each render is also measured with the same metrics (e.g. `ΔF 1270 Hz (+174)`). **Use shifted as target** turns a version you like into your targets. Breathiness and brightness together approximate vocal weight.

Treat a shift as a preview of a direction, not proof your voice can make that exact sound: large shifts leave the range any real vocal tract produces, and the WORLD vocoder sounds slightly buzzy at extreme settings.

### Importing recordings

**Import audio…** analyses a file (WAV, FLAC, OGG, MP3, AIFF; mono-mixed unless you say one channel of a stereo file is an EGG, resampled, and trimmed to the first 90 s), such as a take recorded elsewhere or a reference singer or speaker. Imported files don't change your live formant calibration, since they may be someone else's voice.

If the optional isolation extras are installed, you'll be asked whether there's music behind the voice. If so, **Demucs** (htdemucs) separates the vocals first. It runs on the CPU (roughly 10–15 s for a short clip) and downloads its model (~80 MB) on first use.

### Speak and Sing modes

The **Speak / Sing** switch at the top of the live panel picks the tabs and meters for what you're practising, and is remembered between sessions.

| Mode  | Tabs                                         | Meters                                                              |
| ----- | -------------------------------------------- | ------------------------------------------------------------------- |
| Speak | Pitch, Spectrum, Pitch × Resonance, Vowels   | Resonance, Fold Contact, Clarity, Ring, Register, Intonation Range, Level |
| Sing  | Exercise, Pitch, Spectrum, Voice Map         | Tuning, Vibrato, Fold Contact, Clarity, Ring, Register, Level       |

With an EGG connected, a Contact quotient meter joins both lists.

### Singing practice

- **Exercise** tab: pick a pattern and a starting note, or load a MIDI melody. The patterns are a five-note scale, major and minor arpeggios, an octave scale, and a long tone for vibrato and sustain. Press Start for a four-beat count-in, then sing along with the scrolling piano roll.
  - **Guide tone**: plays each note. Use headphones, or the mic will hear the guide instead of you.
  - **Octave doesn't matter**: singing the melody an octave lower (or higher) scores the same.
  - **Afterwards**: the take card shows notes in tune (within ±25¢), average error, share of note time in tune, and a per-note chart of how sharp or flat each note was.
  - **MIDI files**: the busiest non-drum channel is used as the melody, keeping the top note of any chord. Use Transpose to fit your range.
- **Tuning meter**: cents from the exercise's current note, or from the nearest note when no exercise is running. It turns green inside ±25¢.
- **Vibrato meter**: rate and extent over the last ~1.2 s while you hold a note, or "straight tone" if there's no vibrato.
- **Every take with held notes** also reports:
  - vibrato rate, extent (±cents) and cycle-to-cycle variation (lower is steadier)
  - slow pitch drift
  - how far your notes sit from A440 equal temperament

  Operatic and jazz vibrato differ mainly in extent, regularity and duration rather than rate ([Manfredi et al., 2015](https://flore.unifi.it/bitstream/2158/953249/5/Automatic-Assessment-of-Acoustic-Parameters-of-the-Singing-Voic_2015_Journal.pdf)).
- **Voice Map** tab: a pitch × level grid that fills in as you sing or speak, coloured by Clarity, Ring or Fold Contact, after FonaDyn ([Ternström](https://www.kth.se/tmh/projects/fonadyn-phonatory-dynamics-1.694179)).
  - It shows your range and where in it your voice is clearest or brightest.
  - It's saved to `~/.voicelab/voicemap.npz` and builds up across sessions.
  - Level is uncalibrated, so keep mic gain and distance the same.

### Speaking practice

- **Read aloud** (top of the live panel) shows a passage to read. There are five, covering a vowel tour, questions and answers, a story, a phone call, and counting. Reading the same passage each session makes takes comparable over time.
- **Vowels** tab: a hands-free vowel check. Press Start and say each prompted word (heed, hid, head, had, hod, hud, hood, who'd) once, then pause. Each word is captured automatically. The result is:
  - your vowel space (F2 against F1) drawn over your saved vowel baseline
  - a **vowel-balanced ΔF**: each vowel counts equally, so it isn't skewed by which vowels a sentence happened to contain, making it the cleanest resonance comparison between sessions
  - **Save as vowel baseline** keeps it for later checks
- **Time in target**: once you have pitch or resonance targets, each take shows the share of voiced time spent inside them. Holding a voice through a whole passage matters more than its median.

### Baseline and targets

Progress is measured against your own voice, which also cancels out most mic and room coloration.

- **Set as baseline** on a take of your everyday or starting voice. Every meter then shows a white baseline tick and change from it (e.g. `+3.2 st vs baseline`, `+45 Hz vs baseline`). The pitch view shows a dashed baseline line, and the plane shows a star.
- **Use as target** on a take you liked. Each metric gets a range around that take: ±1.5 semitones for pitch, ±30 Hz for resonance, ±3 dB for the voice-quality metrics. Meters show the range as a green band and turn green when you're inside it. The pitch view and the Pitch × Resonance plane show it too, and the live note turns green inside the pitch target.
- **Targets** (top of the live panel) opens a dialog for setting ranges by hand, turning targets off one by one, and clearing the baseline. Keep only one or two targets on while practising, since watching many at once makes feedback harder to use.

The profile is stored in `~/.voicelab/profile.json` (override the folder with the `VOICELAB_HOME` environment variable). It also remembers the last formant calibration, the takes folder and the mic, so live resonance is calibrated from launch. Vowel checks, and takes recorded without a takes folder, are summarized (no audio) in `~/.voicelab/history.jsonl`; with a takes folder, the folder is the record of your takes, so deleting a take there also removes it from Progress.

## Tips

- Record a take early in a session. It calibrates the formant ceiling that the live resonance meter uses.
- Very quiet input may not yield enough voiced frames to analyze.
- Longer takes (3–5 seconds of voice) produce more stable values. For resonance, connected speech with several vowels works better than one held vowel.
- Spectral metrics (Ring, Clarity, Fold Contact) depend on the mic and room. Compare takes recorded with the same setup.

## Building a Windows app

To get a folder you can run without Python installed, run this from the repo root with your venv's Python:

```powershell
.\packaging\build.ps1
```

Pass `-Python C:\path\to\python.exe` to use a different interpreter. The script installs PyInstaller, builds, and zips the result. You'll find `dist\VoiceLab\VoiceLab.exe` and `dist\VoiceLab-windows.zip` (about 255 MB unzipped, 105 MB zipped).

Demucs vocal isolation isn't included in the build, since it needs PyTorch. The app runs without it, as it does when the extras aren't installed.

## Tests

```bash
python -m pytest tests
```

The tests synthesize vowels with known pitch, formants, glottal source shape and noise, then check that each metric recovers them. `tests/test_app.py` drives the whole app window headless (no mic or speakers needed): live meters, recording, the takes folder, exercises, the vowel check, shifting, Progress, register calibration, and a simulated two-channel input with a synthetic EGG.
