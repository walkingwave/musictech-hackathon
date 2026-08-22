import { useEffect, useMemo, useRef, useState } from 'react';

// An editable piano roll. Notes are {id, pitch, start, length, velocity}
// with start/length in beats, which is the same unit the backend takes —
// so nothing here needs to know the tempo.
//
//   click empty grid   add a note
//   drag a note        move it (snapped)
//   drag its right edge resize it
//   click with alt     delete
//
// Drawn on a canvas rather than as DOM nodes: a few bars of sixteenths is
// already hundreds of grid cells, and that many elements makes dragging
// visibly stutter.

const PITCH_H = 12;
const MIN_PITCH = 36; // C2
const MAX_PITCH = 84; // C6
const PITCHES = MAX_PITCH - MIN_PITCH;

const BLACK_KEYS = new Set([1, 3, 6, 8, 10]);
const isBlack = (pitch) => BLACK_KEYS.has(pitch % 12);
const noteName = (pitch) =>
  ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'][pitch % 12] +
  (Math.floor(pitch / 12) - 1);

let counter = 0;
const uid = () => `n-${++counter}`;

export default function PianoRoll({
  notes,
  onChange,
  bars = 4,
  beatsPerBar = 4,
  snap = 0.25,
  activePitches, // Map of pitch -> velocity, from the live MIDI controller
  playhead = null, // beats, or null when not playing
}) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(900);

  const totalBeats = bars * beatsPerBar;
  const height = PITCHES * PITCH_H;
  const pxPerBeat = width / totalBeats;

  const beatToX = (b) => b * pxPerBeat;
  const xToBeat = (x) => x / pxPerBeat;
  const pitchToY = (p) => (MAX_PITCH - p - 1) * PITCH_H;
  const yToPitch = (y) => MAX_PITCH - Math.floor(y / PITCH_H) - 1;
  const snapBeat = (b) => Math.max(0, Math.round(b / snap) * snap);

  // Track the container width so the roll fills whatever space it is given.
  useEffect(() => {
    const element = wrapRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // --- drawing -----------------------------------------------------------

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Row shading: black-key rows sit darker, so octaves are findable.
    for (let p = MIN_PITCH; p < MAX_PITCH; p++) {
      ctx.fillStyle = isBlack(p) ? '#f1efe8' : '#faf9f5';
      ctx.fillRect(0, pitchToY(p), width, PITCH_H);
      if (p % 12 === 0) {
        ctx.fillStyle = 'rgba(0,0,0,0.10)';
        ctx.fillRect(0, pitchToY(p) + PITCH_H - 1, width, 1);
      }
    }

    // Beat and bar lines.
    for (let b = 0; b <= totalBeats; b++) {
      const onBar = b % beatsPerBar === 0;
      ctx.fillStyle = onBar ? 'rgba(0,0,0,0.22)' : 'rgba(0,0,0,0.07)';
      ctx.fillRect(beatToX(b), 0, onBar ? 1.5 : 1, height);
    }

    // Notes.
    notes.forEach((note) => {
      const x = beatToX(note.start);
      const w = Math.max(3, beatToX(note.length));
      const y = pitchToY(note.pitch);
      ctx.fillStyle = '#6c7cff';
      ctx.fillRect(x, y + 1, w, PITCH_H - 2);
      ctx.fillStyle = 'rgba(0,0,0,0.28)';
      ctx.fillRect(x + w - 2, y + 1, 2, PITCH_H - 2); // resize edge
    });

    // Keys currently held on the controller.
    if (activePitches?.size) {
      ctx.fillStyle = 'rgba(214,69,69,0.30)';
      activePitches.forEach((_, pitch) => {
        if (pitch >= MIN_PITCH && pitch < MAX_PITCH) {
          ctx.fillRect(0, pitchToY(pitch), width, PITCH_H);
        }
      });
    }

    if (playhead != null) {
      ctx.fillStyle = '#d64545';
      ctx.fillRect(beatToX(playhead), 0, 1.5, height);
    }
  }, [notes, width, height, totalBeats, beatsPerBar, activePitches, playhead]);

  // --- editing -----------------------------------------------------------

  const hitTest = (beat, pitch) =>
    notes.find(
      (n) => n.pitch === pitch && beat >= n.start && beat <= n.start + n.length,
    );

  const onPointerDown = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const beat = xToBeat(e.clientX - rect.left);
    const pitch = yToPitch(e.clientY - rect.top);
    const hit = hitTest(beat, pitch);

    if (e.altKey) {
      if (hit) onChange(notes.filter((n) => n.id !== hit.id));
      return;
    }

    if (!hit) {
      onChange([
        ...notes,
        { id: uid(), pitch, start: snapBeat(beat), length: snap * 4, velocity: 90 },
      ]);
      return;
    }

    // Within 6px of the right edge means resize, otherwise move.
    const fromEnd = beatToX(hit.start + hit.length) - (e.clientX - rect.left);
    const mode = fromEnd < 6 ? 'resize' : 'move';
    const startBeat = beat;
    const original = { ...hit };

    const move = (ev) => {
      const delta = xToBeat(ev.clientX - rect.left) - startBeat;
      const patch =
        mode === 'resize'
          ? { length: Math.max(snap, snapBeat(original.length + delta)) }
          : {
              start: snapBeat(original.start + delta),
              pitch: yToPitch(ev.clientY - rect.top),
            };
      onChange(notes.map((n) => (n.id === hit.id ? { ...n, ...patch } : n)));
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  // Key names down the left edge, at octave boundaries only.
  const labels = useMemo(() => {
    const out = [];
    for (let p = MIN_PITCH; p < MAX_PITCH; p++) {
      if (p % 12 === 0) out.push({ pitch: p, name: noteName(p), y: pitchToY(p) });
    }
    return out;
  }, []);

  return (
    <div className="roll">
      <div className="roll-keys" style={{ height }}>
        {labels.map((l) => (
          <span key={l.pitch} style={{ top: l.y }}>
            {l.name}
          </span>
        ))}
      </div>
      <div className="roll-grid" ref={wrapRef} style={{ height }}>
        <canvas
          ref={canvasRef}
          style={{ width: '100%', height, display: 'block', cursor: 'crosshair' }}
          onPointerDown={onPointerDown}
        />
      </div>
    </div>
  );
}
