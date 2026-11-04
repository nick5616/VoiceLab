# 02 UX audit: journeys, gaps, flows (wave A, runs alongside 01)

Read `00-overview.md` and `specs/phase10/00-overview.md` first.

**Goal:** be the user's eyes. Use the app the way real people would, step by step, look at every screen, and write down:
- what is **broken**;
- what is **confusing**: you can't tell what the app is doing, what it heard, or whether to trust it;
- what is **missing**: functions, feedback, guidance;
- how the **flows** should go instead.

Your findings become the work list for wave B (specs 03–06). They must be concrete, have evidence behind them, and come with acceptance criteria.

**You change no repo files.**
- Worktree (for running the app): `C:\tmp\wt11-02-ux-audit`, branch `phase11-02-ux-audit`, a checkout of `main`. Don't commit to it except, optionally, nothing.
- Your outputs go in `C:\tmp\phase11-work\02-audit\`.

## How to observe
Spec 01's lab is being built at the same time, so drive the app yourself:
- Start from `C:\tmp\phase10-work\05-shell\shots.py`. Copy it into your work folder, point it at your worktree, and extend it. It builds the real window offscreen with a profile, a copy of the real takes, and live frames fed through `engine.feed` / `engine.publish`.
- Use `tests/test_shell.py`'s `play()` for audio, and `tests/mic_noise.py` and `tests/test_analysis.synth_vowel` for voices and noise.
- Real takes: copy `C:\Users\nickb\VoiceLab\*.wav|*.json` into your work folder first, and never touch the originals. Feed real WAVs live to see what a user saw while speaking.
- Drive clicks with `QTest` or by calling the widgets' slots; type with `QTest.keyClicks`.
- Screenshot every step with `window.grab()`, at 1920×1080 and 1280×720 and in both themes where it matters. **Read every PNG you produce.** That's the point of this spec.
- Read the code of a screen when you need to know what it's *supposed* to do or what states it has (`ui/`, `app_core.py`, `live_session.py`, `report.py`, `take_view.py`).
- If spec 01's lab lands on `main` while you work (`python -m ui.lab`), you may use it.
- You may use web search lightly to compare with other voice-training and singing apps (Voice Tools, Eva, Christella VoiceUp, Vocal Pitch Monitor and similar). Cite the URL for any claim about them and mark claims from memory "unverified".

## Personas (walk every journey as each one where it applies)
1. **Mira:** starting voice feminization. Non-technical, anxious about her voice, wants to know "am I doing it right right now?".
2. **Sam:** six months in, with targets and a baseline. Wants to see progress over weeks, and why today felt worse.
3. **Jordan:** masculinizing / androgynous goal. Checks that nothing assumes the feminine direction.
4. **Ana:** a singer. Wants range, tuning, held notes, register, and exercises with MIDI.
5. **Lee:** "just exploring". Curious, will click everything, and has a noisy room and a cheap USB mic (the user's real mic problems: desk rumble, an integer-kHz whine, 44.1 kHz through MME).

## Journeys
For each step, record:
- what the person wants;
- what they see (the screenshot path);
- what they have to *guess*;
- what goes wrong;
- what is missing.

The journeys:
1. **First run:** onboarding → first dashboard → "what do I do now?".
2. **First take:** speak → record → wait → read the result → open the full report → replay a moment.
3. **Live practice:** 10 minutes of trying to move pitch and resonance into the target, using only the live screen. Can she tell when she's in target, how long she's held it, and whether she's improving within the session? What happens in the silences between phrases?
4. **Standard passage:** read aloud → result → compare with last time.
5. **Progress:** "am I getting better?" across the 11 real takes.
6. **Trust:** a reading looks wrong. Can the user find out why: noise, a low-confidence reading, a reading that doesn't apply, the mic? Does the app say what to do?
7. **Bad environment:** desk rumble, whine, clipping, too quiet, no mic, mic unplugged mid-session.
8. **Import** a recording (with and without isolation).
9. **Singing:** sing mode → exercise → held notes → voice map → tuning/vibrato readings.
10. **Housekeeping:** rename, delete (and undo?), set baseline and targets, change folder, change goal, change theme, reopen onboarding.
11. **Long-running work:** re-analysis of all takes, register refit, voice map rebuild. Does the user know what's happening, what's affected, and when it's done?

## Lenses (apply to every screen)
1. **Visibility of state:**
   - Can the user tell, at a glance and without reading small text: listening or not; hearing a voice or silence; recording; analysing; busy with background work; which readings are live and which come from the last take; which numbers to trust right now?
   - Is every state visibly different where the user is looking?
2. **Truth and trust:**
   - Do numbers agree with each other?
   - Are smoothing and windows explained ("last 3 s")?
   - Does confidence come with a reason and a fix?
   - Are readings that don't apply hidden rather than shown as broken?
3. **Plain language:** a headline first and the number second; no jargon outside Details or tooltips; one name per thing.
4. **One idea per area:** what's the single thing each area wants you to look at? Is there visual noise competing with it?
5. **Feedback loops:**
   - Does the app react while the user practises (in target, held for N seconds, a gentle cue)?
   - Does it close the loop after a take ("you did the tip from last time")?
6. **Flow:** what's the natural next step after each screen, and is it one obvious click away? Dead ends?
7. **Kindness and safety:** tone, rest nudges, no shaming wording, nothing that implies a "correct" gender.
8. **Desktop depth:** is the detail still there for Sam and Ana without crowding Mira?
9. **Layout quality:** truncation, clipping, overlaps, alignment, density, contrast, at both sizes and in both themes.

## What "missing" means (look for these explicitly)
- feedback and guidance during live practice;
- session structure (a warm-up → exercise → passage → result routine);
- comparing takes;
- listening back with the readings in sync;
- explaining *why* a reading is what it is;
- guidance for the next step;
- streaks or gentle progress cues;
- undo;
- search, filter or tag for takes;
- export or share;
- calibration (baseline) prompts;
- a mic setup wizard;
- keyboard and accessibility.

Don't stop at this list. Rank by user impact, not by ease.

## Deliverables (in `C:\tmp\phase11-work\02-audit\`)
1. **`findings.json`:** one entry per finding, with these fields:
   - `id` (A-001…);
   - `group`: `live` (dashboard + live), `takes` (takes, report, progress), `shell` (top bar, nav, activity, toasts, banners, onboarding, settings, mic check, design-system components), `practice`, or `cross`;
   - `type`: broken, confusing, missing or flow;
   - `severity`: blocker, major or minor;
   - `persona` / `journey` / `step`;
   - `evidence`: screenshot path(s), and the widget or text quoted;
   - `user_impact`: one sentence, in the user's words;
   - `proposal`: concrete; what it looks like and says; a sketch in ASCII if layout matters;
   - `acceptance`: a check a wave B agent can verify, ideally one spec 01's evals could automate ("the readout and the Pitch card show the same value, or are labelled 'now' / 'last 3 s'");
   - `owner_files`: the files it touches, per the ownership table in `00-overview.md`.
2. **`REPORT.md`:**
   - **The top 15:** the findings that matter most for the user's goals, with a one-paragraph rationale each.
   - **Journeys:** each journey as a short storyboard (step → screenshot → verdict).
   - **Flow redesign:** proposed screen-to-screen flows and the information architecture. For example: what the dashboard is for vs the Live screen; where "now" vs "last take" live; how a practice session starts and ends; where the user goes after a take. Use before/after ASCII wireframes. Stay inside the phase 10 shell (nav rail + screens) unless there's a strong reason, and give it.
   - **State model:** the list of app states a user must be able to perceive, with the visible signature each one should have, in a table. Wave B and spec 01's state oracle will use it.
   - **Vocabulary sheet:** one name, unit and zone wording per quantity, for the whole app (e.g. "Pitch · Hz", "Resonance (brightness)", "Intonation · semitones"). Wave B will apply it everywhere.
   - **Missing functionality:** a ranked list, each item with its value, its cost (S/M/L) and a proposal.
   - **Per group:** for live, takes, shell and practice, the ordered work list for that wave B agent.
   - **New eval ideas** for spec 01's lab.
3. **`shots/`:** every screenshot you cite.

Be blunt and specific. "The take card is busy" is useless. Write instead: "The take card shows 6 confidence chips, 3 identical 'Details' links and 2 zone bars before the first sentence a user can act on; at 1280×720 the tips start below the fold." Every finding needs evidence.
