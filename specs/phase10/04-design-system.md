# 04 Design system (wave A)

**Goal:** one coherent visual language and a kit of Qt components, so the wave B screens are calm, readable and consistent. The bar is Voice Tools: soft pink/blue zone colours, big plain headlines, one idea per card. Keep VoiceLab's desktop density, but give every number a clear hierarchy. Things that are working should *look* like they're working (motion, progress), without being distracting.

**Files you own (all new):**
- `ui/theme.py`, `ui/anim.py`, `ui/components/` (a package, one module per family);
- `ui/gallery.py`;
- `tests/test_ui_components.py`.

Create `ui/__init__.py` empty if it doesn't exist; spec 03 creates the same empty file. **Don't edit any other file.** Specs 01–03 run in parallel. In particular, spec 03 is moving the old widgets into `ui/` under other names (`ui/meters.py`, `ui/widgets.py`, `ui/live_panel.py`, …). Don't create files with those names.

## Read first
- `ui_common.py` (colours, `RangeBar`, `MetricBar`, `ShareBar`), `widgets.py` (`ElidedLabel`, `ResponsiveGrid`), `take_card.py` and `live_panel.py`: what exists and what the components replace.
- The `dataviz` skill (load it with the Skill tool before choosing chart and zone colours). Use its palette method and validator for the zone and series colours, in light and dark.

## The problems to solve (from the user's screenshots)
- **Take cards** have a lot of space but also not enough: everything has the same weight, and the colours and spacing don't group things.
- **Labels** are jargon-first.
- **Hatched bars** repeat on four rows.
- **Several near-identical greys.** Buttons all look alike.
- **No motion:** nothing shows that analysis, loading or re-analysis is running, beyond a status line.

## Deliver
1. **`ui/theme.py`: tokens and a QSS builder.**
   - **Colour tokens** for a dark theme (the current default) and a light one:
     - `bg`, `surface`, `surface_raised`, `border`;
     - `text`, `text_secondary`, `text_muted`;
     - `accent`, `accent_text`;
     - semantic `success` / `warning` / `error` / `info`;
     - `on_target` / `off_target`;
     - `recording`.
   - **Zone colours** `feminine`, `overlap` and `masculine` as fill + edge + text. Soft pink and soft blue like Voice Tools, with a neutral or lavender overlap. They must work on both backgrounds and be told apart without colour: zones always carry a text label, and patterns or edges are allowed. Validate the contrast and colour-blind distinguishability with the dataviz method and report the numbers.
   - **Register colours** chest / mix / head / fry, and **series colours** for graphs: pitch, F1–F3, the compare overlay.
   - **Spacing** scale (4, 8, 12, 16, 24, 32), **radii**, **elevation** (borders or shadows), and a **type scale**: display, title, heading, body, caption, and a numeric style with tabular figures. Use the Windows system font (Segoe UI Variable / Segoe UI).
   - `theme.current()`, `theme.set_theme("dark"|"light"|"system")`, `theme.qss()` for the app-wide stylesheet, and a `theme.changed` hook so components restyle live.
   - A `reduce_motion` flag.
2. **`ui/anim.py`: motion helpers** built on `QVariantAnimation` / `QPropertyAnimation`, all no-ops when `reduce_motion` is set:
   - `fade_in`, `fade_out`;
   - `slide_down` / `collapse`, for expanding sections;
   - `tween(value_setter, start, end, ms)`, for markers gliding to new values;
   - `pulse(widget)`, for the recording button;
   - `shimmer`, for skeleton placeholders.

   Durations: 120–250 ms, ease-out. Cite a motion guideline, or mark it own judgement.
3. **`ui/components/`.** Each component is a QWidget with a small API, drawn from theme tokens, restyled on theme change, and sized with sensible minimum and maximum widths. It must be readable from 1280×720 up to 4K at 150% scaling.
   - **Layout:** `Card` (title, optional subtitle and action buttons, body), `Section` (heading + optional "?" help), `Collapsible` (animated, remembers its state by key), `FlowGrid` (a responsive grid like `ResponsiveGrid` that also fills sensibly at full-screen widths), `Divider`.
   - **Text:** `Headline` (big plain sentence), `Caption`, `HelpTip` (a "?" with plain-language help on hover or click), `ElidedLabel` (re-implement here, don't import the old one).
   - **Data:**
     - **`ZoneBar`:** a horizontal scale with coloured zones and their labels, an optional target band, a baseline tick, the value marker (tweened), an uncertainty range (whisker), and log-scale support. It is the core of the report, the dashboard and the live meters.
     - **`MetricCard`:** headline + ZoneBar + value text + confidence chip + one-line explanation + a collapsible "details" with the jargon. An "unavailable" state shows one calm line, not a hatched bar.
     - **`BigNumber`:** a value + unit + sublabel, like Voice Tools' circle but rectangular is fine.
     - **`ShareBar`:** register shares.
     - **`Sparkline`:** small trends for progress.
     - **`ConfidenceChip`:** high / medium / low / none, with a tooltip.
     - **`Chip` / `Badge`**, **`StatusDot`**.
   - **Controls:**
     - `Button` variants: `primary`, `secondary`, `ghost`, `danger`; with an icon; a loading state with an inline spinner.
     - `IconButton`, `SegmentedControl` (Speak / Sing), `Tabs`.
     - `RecordButton`: idle, recording (pulse plus elapsed time), analysing (spinner ring), disabled.
     - `NavRail`: a vertical icon + label navigation with a selected state and badges, for wave B's screens.
     - `Toggle`, `Select`.
   - **Feedback:**
     - `Spinner` (animated);
     - `ProgressStrip` (thin, determinate or indeterminate);
     - `ActivityCenter` (a compact widget listing running jobs, `[{"id", "label", "done", "total", "text"}]`, the same shape as spec 03's `JobRunner.jobs()`, with progress bars; it collapses to a spinner and a count);
     - `Skeleton` (shimmer placeholder of a given shape);
     - `Toast` (transient message, success / warning / error / info, stacked, auto-dismiss, with an action button);
     - `Banner` (one persistent issue line with an action, e.g. "Too noisy to judge brightness · Run mic check");
     - `EmptyState` (icon, sentence, action).
   - **Moments:** `MomentChip` ("4.2 s", click to play) for report tips.
4. **`ui/gallery.py`:** `python -m ui.gallery [--theme dark|light] [--screenshot path.png]`.
   - It shows every component in its main states with realistic VoiceLab content: a pitch ZoneBar at 205 Hz in the overlap zone with a target band; a MetricCard with confidence none; an ActivityCenter with two jobs; a RecordButton in each state; a Toast stack.
   - It doubles as the visual spec for wave B.
   - Screenshots of both themes go in `C:\tmp\phase10-work\04-design\`.

## Verify (`tests/test_ui_components.py`, offscreen)
- Every component constructs in both themes, and switching theme restyles it.
- ZoneBar maps values correctly (log and linear, clipped) and draws the zone labels.
- MetricCard's unavailable state hides the bar.
- ActivityCenter updates from the job list.
- RecordButton states.
- With `reduce_motion`, `anim` helpers apply the end state immediately.
- The gallery renders to a PNG without error.
- Contrast check: text tokens on their backgrounds meet WCAG AA (4.5:1 for body, 3:1 for large text and UI). Write the test with the computed ratios.
