# 02 Voice report (wave A)

**Goal:** a take report that a person new to voice training understands at a glance, like Voice Tools' results and Harmonics screens. Our version is honest, measures more than pitch, and gives tips specific to this take for the next one. Pure Python. Plain data out, no UI.

**Files you own:**
- new `report.py`, new `tests/test_report.py`;
- `profile_store.py`, for the `voice_goal` field and its setter only;
- `tests/test_profile.py`, for that field.

**Parallel specs:**
- **01** may add take keys:
  - `confidence` (`{metric: "high"|"medium"|"low"|"none"}` plus reasons);
  - smoothed formant tracks.

  Use them when present (`take.get("confidence")`), and fall back to `coach.take_quality(take, take.get("noise"))["trust"]` when absent.
- **03** builds the core and **wave B** the UI. Both will call your API exactly as specified below. Don't import Qt.

## Read first
- `coach.py`, for `take_quality`, `advice`, its tone and its cited thresholds.
- `profile_store.py`, for targets, baseline, `TARGET_PRESETS` and `time_in_target`.
- `analysis.analyze_take`'s summary keys and `tracks` (`times`, `f0`, `delta_f`, `f1`–`f3`, `h1h2`, `cpp`, …).
- `ui_common.METRIC_DEFS`, for the current ranges and their sources.
- A few real sidecars in `C:\Users\nickb\VoiceLab\*.json`, read-only.

## What the report must do
1. **Dimensions (speak mode).**
   - **Pitch:** median f0 of voiced speech.
   - **Pitch floor:** the low points, often at sentence ends.
   - **Intonation:** pitch range in semitones, 10–90th percentile.
   - **Resonance / brightness:** ΔF and apparent tract length. Use F1/F2 of vowels if the take has a vowel check.
   - **Vocal weight:** H1\*–H2\*, labelled a proxy.
   - **Breathiness / clarity:** CPP, noise-sensitive.

   Optionally add others you can source well (e.g. speech rate is out of scope unless trivially available). In **sing** mode, produce a singing report from the existing singing and exercise stats instead (range, tuning, steadiness, register). No gender placement in sing mode.
2. **Zones per dimension.**
   - Three zones on one scale: `"masculine"`, `"overlap"`, `"feminine"`. Each has `lo`/`hi` and a display label: "typically masculine", "overlap / androgynous", "typically feminine".
   - Plus the scale's own min/max and whether it's log (pitch).
   - **Research the ranges and cite them**: speaking f0 of cis men and women and the gender-ambiguous band; resonance and formant scaling; H1–H2 differences; intonation range differences; CPP and breathiness. Good starting points, all to verify:
     - Leung, Oates & Chan 2018 (JSLHR 61(2)): f0 alone explained about 42% of the variance in perceived gender, with resonance as important;
     - Gelfer & Schofield 2000;
     - Hillenbrand & Clark 2009;
     - Titze 1994;
     - Klatt & Klatt 1990 (H1–H2);
     - Gelfer & Mikos 2005;
     - Davies, Papp & Antoni 2015 (trans voice guidelines).
   - Write each zone's numbers and source next to them in a `ZONES` table, the way `METRIC_DEFS` does.
   - Where the evidence is weak (weight, clarity), say so in the zone's `note` and give that dimension a lower weight in the overall placement.
   - These are population tendencies with large overlap, not rules. Say that once, in the report's `caveat`.
   - Note that ΔF depends on the formant ceiling; use the profile's ceiling when the take has one.
3. **Overall placement, honest.**
   - Never a pitch-only gender percentage. No numbers that sum to more than 100%.
   - Combine the trusted dimensions into one position on a masculine ↔ feminine scale, with an uncertainty range and a confidence. Weight each dimension by its cited contribution to perceived gender (pitch and resonance highest).
   - Name which dimensions pull which way, e.g. "Pitch is in the feminine range; resonance is still in the overlap zone, and it pulls the overall impression toward androgynous."
   - With fewer than 2 trusted dimensions, the overall placement is `None`, with the reason.
4. **Goal.** Add `profile.voice_goal` ∈ {`"feminine"`, `"androgynous"`, `"masculine"`, `None`} with a setter; `None` means "just exploring". Tips point toward the goal; with no goal, the report describes without pushing.
5. **Headlines and explanations.** Per dimension:
   - a plain headline ("Pitch sits in the overlap zone", "Brighter than your last take");
   - a one-sentence explanation of what it measures and why it matters;
   - the number and unit as secondary text;
   - a `details` string with the jargon (ΔF, H1\*–H2\*, CPP, dBFS) for the expanded view.

   Tone: kind, concrete, never alarmist. Follow `coach.py`.
6. **Take-specific tips for next time (up to 3, most useful first).**
   - Use **this take's tracks**, not just its summary. Examples:
     - "Your pitch dropped to about 110 Hz at 4.2 s and 9.8 s, the ends of sentences. Next take, keep the last word up."
     - "Your brightest stretch was 3.1–4.0 s; replay it and notice how it felt."
     - "Resonance darkened on the 'oo' and 'oh' words."
   - Each tip carries `moments: [(start_s, end_s)]` so the UI can play or highlight them.
   - Include one **best moment** (closest to the goal, or the targets), as positive reinforcement.
   - Compare with the previous comparable take (same task, via `profile_store.task_of`) and with the baseline when given: "brighter than your last take", "pitch steadier than your baseline".
   - Reuse `coach.advice` rules where they fit. Never comment on a reading the take can't support; say what couldn't be judged instead, once.
7. **Confidence everywhere.** Each dimension has `confidence` (`high`/`medium`/`low`/`none`) and `reason`. When it's `none`, the dimension has no position or zone, and its headline says why in plain words ("Too much background noise to judge brightness"). Several untrusted dimensions with the same cause collapse into one `issues` entry, so the UI shows one banner, not four hatched bars.

## API (wave B builds against exactly this)
```python
report.ZONES                      # {key: {"label", "unit", "scale": (min, max), "log": bool,
                                  #        "zones": [{"id", "label", "lo", "hi"}], "source", "note"}}
report.DIMENSIONS_SPEAK           # ordered keys
report.zones(key)                 # ZONES[key] or None
report.position(key, value)       # 0-1 on the dimension's scale (log for pitch), clipped
report.zone_of(key, value)        # "masculine" | "overlap" | "feminine" | None
report.build_report(take, profile=None, previous=None, goal=None)
# -> {
#   "mode": "speak"|"sing",
#   "headline": str,               # one sentence for the whole take
#   "overall": {"position": 0-1 or None, "range": (lo, hi) or None, "zone": id or None,
#               "confidence": str, "pulls": [{"key", "direction": "feminine"|"masculine"|"neutral"}],
#               "text": str, "reason": str or None},
#   "dimensions": [{"key", "name", "value", "display", "unit", "position", "zone", "zone_label",
#                   "confidence", "reason", "headline", "explanation", "details",
#                   "target": (lo, hi) or None, "baseline": float or None,
#                   "change": {"vs": "previous"|"baseline", "text": str} or None}],
#   "tips": [{"key", "text", "moments": [(s, e)], "kind": "tip"|"best"|"info", "dimension": key or None}],
#   "issues": [{"text", "affects": [keys]}],   # recording problems, collapsed
#   "caveat": str,
#   "goal": goal,
# }
report.live_zones(mode)           # the zones the live pitch graph shades (speak: pitch zones; sing: None)
```
`profile` can be None. `goal` defaults to `profile.voice_goal`. `previous` is a take dict or None. Every field is plain data (str, float, None, lists, dicts), so it is JSON-serialisable.

## Verify (`tests/test_report.py`)
- **Synthetic takes:**
  - pitch 210 Hz with dark resonance → overall is not "feminine", and "pulls" names resonance;
  - pitch alone can't push the overall past the overlap zone when resonance is masculine.
- **Untrusted:**
  - a poor take → overall None, one collapsed issue, no dimension positions;
  - pitch 300 Hz → resonance confidence "none", with a reason.
- **Tips:** a track with three sentence-final dips → a pitch-floor tip whose `moments` cover the dips; the best moment lies inside the take.
- **Goal:** goal "masculine" flips the tip direction; `None` gives no "raise/lower" instructions.
- Every zone has a source string, and zones are contiguous and ordered.
- The report is JSON-serialisable.
- **Real takes**, read-only: run `build_report` on every sidecar in `C:\Users\nickb\VoiceLab\` and save the outputs to `C:\tmp\phase10-work\02-report\`. Paste two of them into your report, rendered as plain text, so they can be judged for readability.
