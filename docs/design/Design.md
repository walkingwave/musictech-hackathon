# Frontend Design

## Purpose

- Reference for the Backing Track Generator frontend.
- Complements `PLAN.md`; does not repeat backend or API design.

## Application States

- **Input:** add a monophonic hum, choose or describe its target musical role.
- **Review:** inspect detected tempo/key and the transformed MIDI before committing to audio generation.
- **Tracks:** review, edit, render, and export the resulting MIDI, guide, and audio in a focused studio.
- Selecting **Transform** moves from Input to Review once analysis has produced usable pitched notes.

## Hum-First Product Contract

The source is a single hummed line, not a vocal performance to be retained in the
result. The application must turn raw audio into a musician-usable intermediate
before Stable Audio is involved:

1. Extract pitched note events and phrase/rest timing from the hum.
2. Transform them into one explicit target: **Melody** or **Bassline**.
3. Save that target as editable MIDI and render the same MIDI as a guide WAV.
4. Use only the guide WAV—not the raw hum—as Stable Audio audio-to-audio input.

A melody transformation preserves recognizable contour and timing. A bassline
transformation places the contour in a playable low register, reduces excessive
note density, and conforms it to the editable harmonic grid. If pitch confidence
is insufficient, the UI must request a new hum rather than silently generate an
unrelated part.

## Input Screen

- Header: product name, session name, Input/Tracks view control.
- Audio row: **Record Hum** and **Upload Hum** actions; explain that one clear, unaccompanied voice works best.
- Prompt field: target and sound request, for example “turn this into an overarching synth melody” or “make this a warm bassline.”
- Target control: **Melody** or **Bassline**; prompt interpretation may preselect either target.
- Settings row: Tempo, Key, Backend, and harmonic grid.
- Tempo and Key default to detected values.
- Primary action: **Transform Hum**.
- Supporting note: detected notes, BPM, key, chords, and MIDI remain editable before rendering audio.

## Tracks Screen

- Left track list and right timeline share the same row boundaries.
- Track rows: Vocal, Bass, Rhodes, Drums, Harmony.
- Each row shows stem name, source/backend label, and a per-stem options control.
- Timeline has bar labels, a shared playhead, and one waveform lane per track.
- Transport header: play control, elapsed/total time, BPM, key, and bar count.
- Chord control: show chord sequence and provide **Edit Chords**.
- Export action: **Export Stems + MIDI**.

## Visual Rules

- Use black, white, and neutral grey only.
- Use grey surfaces to separate header chrome, sidebar, controls, and alternating track lanes.
- Use square corners and thin black dividers.
- Use compact uppercase labels and concise descriptive copy.
- Use a solid black primary action with white text.
- Do not use hero statements, glass effects, gradients, rounded cards, decorative metrics, or promotional copy.

## Scope

- The studio is a focused multitrack player, not a full DAW.
- Prioritize synchronized listening, stem selection, chord editing, regeneration access, and export.
- The monochrome focused-studio mockup is the visual reference.
