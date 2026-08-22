# Frontend Design

## Purpose

- Reference for the Backing Track Generator frontend.
- Complements `PLAN.md`; does not repeat backend or API design.

## Application States

- **Input:** add source audio and a backing-track prompt.
- **Tracks:** review and export generated stems in a focused studio.
- Selecting **Generate** moves from Input to Tracks after analysis and generation begin.

## Input Screen

- Header: product name, session name, Input/Tracks view control.
- Audio row: **Record** and **Upload** actions; show filename and duration when available.
- Prompt field: one free-text description of the requested backing track.
- Settings row: Tempo, Key, Backend, and Stems.
- Tempo and Key default to detected values.
- Primary action: **Generate**.
- Supporting note: BPM, key, and chords can be edited after analysis.

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
