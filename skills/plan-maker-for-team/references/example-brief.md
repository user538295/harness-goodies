# Feature Brief: Multi-Sound Simultaneous Playback

## Problem
Users can only play one audio track at a time, limiting the ability to create a layered ambient soundscape (e.g., rain + white noise) that aids sleep onset.

## Goal
Users can select multiple sounds from a unified list and all selected sounds play simultaneously while the timer is running, fading in volume together with the screen darkness.

## Users & Context
A user winding down for sleep, in the timer setup screen, who wants to compose a personal ambient soundscape before starting their session. They are calm, low-interaction mode — the fewer taps the better.

## Core Flow
1. User opens the sound/music setup screen (MusicSetupView).
2. User sees a unified list of all available tracks and sounds.
3. User taps one or more items — each selected item is visually marked (checkmark or highlight).
4. User taps confirm (✓).
5. Timer starts — all selected sounds play simultaneously, looping, fading in volume as screen darkens.
6. Timer stops — all sounds stop.

## In Scope
- Multi-select in `TrackListView` (replace single-select with toggle-select)
- Simultaneous playback of all selected sounds via multiple `AVAudioPlayer` instances
- All selected sounds fade in volume proportionally with darkness (same formula as today)
- Pause/resume applies to all active players
- Persistence: selected sound set saved and restored
- 4–6 bundled ambient sound files (nature sounds: rain, white noise, ocean, etc.)
- "None" / clear-all option remains

## Out of Scope
- Per-sound volume control — adds UI complexity not justified in v1
- Music vs. sounds category separation — deferred to a future iteration
- Binaural beats or tonal content — avoid mixing complexity
- User-imported audio — out of scope
- Sound preview/test button — not needed for ambient sounds users already know
- Crossfade or smart mixing — v2

## Key Decisions
- **Unified list**: Music tracks and ambient sounds in a single list. Separation into categories is a future iteration — avoids over-engineering the UI now.
- **Flat volume**: All selected sounds play at the same volume level. No per-sound sliders. Simplicity over granularity.
- **Free layering with music**: Music and sounds can play simultaneously. No auto-dimming or mutual exclusion. Trust the user to compose what they want.
- **Multiple AVAudioPlayer instances**: One player per selected sound, all managed by the audio service. Cleanest implementation with no external dependencies.

## Edge Cases & Constraints
- **"None" selected**: Timer starts with no audio — same as today.
- **Many sounds selected**: No artificial cap, but recommend UI communicates selection clearly. Device audio performance is not a concern for 2–6 simultaneous loops.
- **App interrupted** (call, notification): All players pause; all resume on interruption end — existing AVAudioSession interruption handling extends naturally.
- **Route change** (headphones unplugged): All players stop — same existing behavior applies to all.
- **Persistence**: `MusicPreference` model must change from `selectedTrack: AudioTrack?` to `selectedTracks: [AudioTrack]`. Migration: if a single track was previously saved, wrap it in an array.
- **Volume sync**: All active players receive the same `setVolume()` call on each timer tick — no per-player logic needed.

## Open Questions
- Final list of bundled sound files: names, filenames, and count (4–6 to be decided).
- Should the "None" row deselect everything, or just be a no-op when nothing is selected?

## Future Iterations
- Category separation: Music (single-select) vs. Sounds (multi-select) with distinct sections in the list.
- Per-sound volume sliders.
- Sound preview on tap-and-hold.
- User-imported audio.
- Smart mix presets ("Deep Sleep", "Focus") with curated sound combinations.

## Recommendation
This is the right feature to build now — it meaningfully improves the core sleep experience with low UI cost. The hardest part is the data model migration (`selectedTrack` → `selectedTracks`) and extending `AudioServiceProtocol` to manage a pool of players cleanly. Do not compromise on simplicity of the selection UI — multi-select must feel as natural as single-select, not like a checklist form.

---

## References

- **Team plan:** [multi_sound_playback-team-plan.md](./multi_sound_playback-team-plan.md)
