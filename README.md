# VoiceLab

A real-time vocal analysis tool for singers, voice actors, people doing gender-affirming voice training, and anyone training their voice. Live meters respond as you sing or speak, and each recorded take gets a detailed report, so you can consciously shape your instrument.

## What It Measures

| Metric                     | What it reflects                                                                                   | Live |
| -------------------------- | -------------------------------------------------------------------------------------------------- | ---- |
| **Pitch**                  | Fundamental frequency (F0), shown as note name, cents and Hz                                        | Yes  |
| **Resonance (ΔF)**         | Formant dispersion over F1–F4 and the apparent vocal tract length it implies (dark → bright)        | Yes  |
| **Fold Contact (H1*–H2*)** | First-minus-second harmonic level, corrected for F1/F2. A proxy for vocal weight (heavy → light)    | Yes  |
| **Clarity (CPP)**          | Cepstral peak prominence over 0–4 kHz: how periodic the voice is (breathy/rough → clear)            | Yes  |
| **Ring (2–4 kHz)**         | Harmonic energy in the singer's-formant region relative to 0–2 kHz (muffled → ringing)              | Yes  |
| **Intonation Range**       | 5th–95th percentile pitch spread in semitones (flat → expressive). The live meter uses the last 6 s | Yes  |
| **Level**                  | Voiced level in dBFS. Uncalibrated, so compare takes on the same mic and gain only                  | Yes  |

Each take also shows an interactive graph: pitch track on top, F1/F2/F3 formant tracks below. Hover to inspect values at any point in time.

### Why these metrics

- **Resonance** uses ΔF rather than individual formants because single formants mostly reflect which vowel you're saying. In a 2025 study a single speaker deliberately varied pitch, resonance and weight; ΔF tracked resonance with 94% ranking accuracy and stayed largely independent of the other two ([Netzorg et al., Interspeech 2025](https://www.isca-archive.org/interspeech_2025/netzorg25_interspeech.pdf)).
- **Weight** has no reliable single acoustic measure. The same study found that only electroglottograph contact quotient and loudness tracked it, while spectral slope also moved with resonance. Fold Contact, Clarity and Level are proxies, and are best used to compare your own takes against each other.
- **Formant ceiling** (the frequency range Praat searches for formants) is chosen per take. VoiceLab tests several ceilings and rejects any that produce fake formants: ones with very wide bandwidths, or two sitting almost on top of each other. Of the rest it keeps the one with the smoothest tracks. The live meters then use that ceiling.
- **High pitch**: above ~350 Hz the harmonics are too far apart for formant tracking, so resonance is greyed out rather than reporting a wrong number.

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
   - **Spectrum**: a scrolling spectrogram with F1–F4 tracks overlaid, and the current spectrum on the right with harmonics (blue dots) and formants (dashed lines) on the same frequency axis. Use it for vowel shaping and formant tuning, for example lining up a harmonic with F1. Formant tracks are hidden above ~350 Hz, but you can still see harmonics brighten as they pass through a resonance.
   - **Pitch × Resonance**: your live position with a 3-second trail, rough population regions as orientation (not targets), and each recorded take as a numbered point.
2. Click the red microphone button to record a take, speak or sing, then click stop.
3. The app analyzes the take and adds a card to the top of the list.
4. Compare takes as you experiment with different placements, vowel shapes, or resonance strategies.
5. Click **Play** on any card to hear it back alongside its metrics.

On a wide window the live panel and your takes sit side by side; narrower, they stack, with the live panel kept compact. Drag the divider to give either more room. The mouse wheel scrolls the takes wherever the pointer is; a Shift slider only takes the wheel after you click it.

**Mic** (bottom right) picks the input device and is remembered. Only devices of the system's default audio API are listed, so each mic appears once.

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

It covers pitch, resonance, the vowel check's vowel-balanced ΔF, Fold Contact, Clarity, Ring, intonation range, and for singing, exercise accuracy (share of notes in tune and average error) and vibrato rate and extent. Filter by Speak or Sing and by period. Imported files are left out unless **Include imported files** is ticked, since they may be someone else's voice. Weekly medians matter more than any single take: one tired evening shouldn't look like a setback.

### Clipping

If the input overloads, a red **CLIPPING** badge appears in the live panel, and the take card says how many times it happened. Clipping squares off the waveform, adding harshness that inflates Ring and lowers Clarity, so lower the mic gain or move back a little.

### Noise check

The badge in the live panel's header shows your **voice-to-noise ratio (SNR)** over the last 10 s. It's measured in the voice band (0–4 kHz), where VoiceLab measures, from the pauses between phrases, so it shows "SNR —" until you've paused. Below 30 dB it turns orange: Clarity, Fold Contact and Ring become unreliable in noise, while pitch and resonance hold up better ([review](https://pmc.ncbi.nlm.nih.gov/articles/PMC11673399)). Takes recorded below 30 dB carry the same warning.

**High-pitched mic hiss or whine is handled by keeping measurements where the voice is.** Every measurement works around it:

| Measurement     | How it avoids the hiss                                                                                                |
| --------------- | --------------------------------------------------------------------------------------------------------------------- |
| Pitch           | Tracked on a 0–4 kHz copy of the signal. On raw audio, hiss made Praat drop up to 85% of voiced frames.               |
| Clarity (CPP)   | Computed over 0–4 kHz only. At 20 dB SNR of hiss it stays within ~1 dB of the clean value, versus 4–6 dB low over the full band. |
| Ring            | Sums only the harmonic peaks at multiples of your pitch, so noise between harmonics isn't counted.                    |
| Formants        | Already limited by the formant ceiling (≈5–6.5 kHz).                                                                  |

When most of the noise sits above 4 kHz, the badge tooltip and take cards say so, and show the full-band SNR for reference.

VoiceLab deliberately doesn't run noise suppression on your voice. Denoisers remove breath noise and fine harmonic detail along with the room noise, which would make a breathy voice measure clearer than it is. Spectral subtraction of a learned noise profile was also tested, and it made Clarity less accurate, not more.

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

**Import audio…** analyses a file (WAV, FLAC, OGG, MP3, AIFF; mono-mixed, resampled, and trimmed to the first 90 s), such as a take recorded elsewhere or a reference singer or speaker. Imported files don't change your live formant calibration, since they may be someone else's voice.

If the optional isolation extras are installed, you'll be asked whether there's music behind the voice. If so, **Demucs** (htdemucs) separates the vocals first. It runs on the CPU (roughly 10–15 s for a short clip) and downloads its model (~80 MB) on first use.

### Speak and Sing modes

The **Speak / Sing** switch at the top of the live panel picks the tabs and meters for what you're practising, and is remembered between sessions.

| Mode  | Tabs                                         | Meters                                                              |
| ----- | -------------------------------------------- | ------------------------------------------------------------------- |
| Speak | Pitch, Spectrum, Pitch × Resonance, Vowels   | Resonance, Fold Contact, Clarity, Ring, Intonation Range, Level     |
| Sing  | Exercise, Pitch, Spectrum, Voice Map         | Tuning, Vibrato, Fold Contact, Clarity, Ring, Level                 |

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

The tests synthesize vowels with known pitch, formants, glottal source shape and noise, then check that each metric recovers them. `tests/test_app.py` drives the whole app window headless (no mic or speakers needed): live meters, recording, the takes folder, exercises, the vowel check, shifting and Progress.
