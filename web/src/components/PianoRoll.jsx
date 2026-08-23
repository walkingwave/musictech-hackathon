import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

// An editable piano roll. Notes are {id, pitch, start, length, velocity}
// with start/length in beats — the same unit the backend takes, so nothing
// here needs to know the tempo.
//
//   click empty grid      add a note
//   drag a note           move it (pitch and time, snapped)
//   drag either edge      resize from that end
//   drag empty grid       marquee-select
//   shift-click           add to the selection
//   alt-click / right-click  delete
//   Delete / Backspace    delete the selection
//   arrows                nudge (with shift: by a bar / an octave)
//   Cmd-D                 duplicate
//   drag the velocity lane set velocity
//   click the ruler       move the playhead
//
// Grid and notes are drawn to a canvas rather than as DOM nodes: a few
// bars of sixteenths is already hundreds of cells, and that many elements
// makes dragging visibly stutter.

const PITCH_H = 11;
const MIN_PITCH = 24; // C1
const MAX_PITCH = 96; // C7
const RULER_H = 20;
const VEL_H = 46;
const EDGE_PX = 6; // grab zone for resizing

const BLACK = new Set([1, 3, 6, 8, 10]);
const isBlack = (p) => BLACK.has(p % 12);
const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const noteName = (p) => NAMES[p % 12] + (Math.floor(p / 12) - 1);

let counter = 0;
const uid = () => `n-${++counter}`;

export default function PianoRoll({
  notes,
  onChange,
  onBeginEdit,
  bars = 4,
  beatsPerBar = 4,
  snap = 0.25,
  activePitches,
  playhead = null,
  onScrub,
  onPreview, // sound a pitch when it is added or dragged
  height = 300,
}) {
  const gridRef = useRef(null);
  const velRef = useRef(null);
  const wrapRef = useRef(null);
  const scrollRef = useRef(null);
  const [width, setWidth] = useState(900);
  const [selection, setSelection] = useState(() => new Set());
  const [marquee, setMarquee] = useState(null);

  const totalBeats = bars * beatsPerBar;
  const gridH = (MAX_PITCH - MIN_PITCH) * PITCH_H;
  const pxPerBeat = width / totalBeats;

  const beatToX = useCallback((b) => b * pxPerBeat, [pxPerBeat]);
  const xToBeat = useCallback((x) => x / pxPerBeat, [pxPerBeat]);
  const pitchToY = (p) => (MAX_PITCH - p - 1) * PITCH_H;
  const yToPitch = (y) => MAX_PITCH - Math.floor(y / PITCH_H) - 1;
  const snapBeat = useCallback(
    (b) => Math.max(0, Math.round(b / snap) * snap),
    [snap],
  );

  useEffect(() => {
    const element = wrapRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Scroll to the notes on first load, so an empty roll does not open on a
  // silent corner of the range.
  useEffect(() => {
    const element = scrollRef.current;
    if (!element || !notes.length) return;
    const mid = notes.reduce((s, n) => s + n.pitch, 0) / notes.length;
    element.scrollTop = Math.max(0, pitchToY(Math.round(mid)) - element.clientHeight / 2);
    // Only on mount: re-centring while editing would fight the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- drawing -----------------------------------------------------------

  useEffect(() => {
    const canvas = gridRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = gridH * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, gridH);

    for (let p = MIN_PITCH; p < MAX_PITCH; p++) {
      ctx.fillStyle = isBlack(p) ? '#f1efe8' : '#faf9f5';
      ctx.fillRect(0, pitchToY(p), width, PITCH_H);
      if (p % 12 === 0) {
        ctx.fillStyle = 'rgba(0,0,0,0.10)';
        ctx.fillRect(0, pitchToY(p) + PITCH_H - 1, width, 1);
      }
    }

    for (let b = 0; b <= totalBeats; b += snap) {
      const onBar = Math.abs(b % beatsPerBar) < 1e-6;
      const onBeat = Math.abs(b % 1) < 1e-6;
      if (!onBar && !onBeat && pxPerBeat * snap < 6) continue;
      ctx.fillStyle = onBar
        ? 'rgba(0,0,0,0.24)'
        : onBeat
          ? 'rgba(0,0,0,0.10)'
          : 'rgba(0,0,0,0.045)';
      ctx.fillRect(beatToX(b), 0, onBar ? 1.5 : 1, gridH);
    }

    if (activePitches?.size) {
      ctx.fillStyle = 'rgba(214,69,69,0.22)';
      activePitches.forEach((_, pitch) => {
        if (pitch >= MIN_PITCH && pitch < MAX_PITCH) {
          ctx.fillRect(0, pitchToY(pitch), width, PITCH_H);
        }
      });
    }

    notes.forEach((n) => {
      const x = beatToX(n.start);
      const w = Math.max(3, beatToX(n.length));
      const y = pitchToY(n.pitch);
      const chosen = selection.has(n.id);
      // Velocity reads as opacity, so dynamics are visible without having
      // to look down at the velocity lane.
      const alpha = 0.35 + 0.65 * ((n.velocity ?? 90) / 127);
      ctx.fillStyle = chosen ? `rgba(214,69,69,${alpha})` : `rgba(108,124,255,${alpha})`;
      ctx.fillRect(x, y + 1, w, PITCH_H - 2);
      ctx.fillStyle = 'rgba(0,0,0,0.30)';
      ctx.fillRect(x + w - 2, y + 1, 2, PITCH_H - 2);
      if (chosen) {
        ctx.strokeStyle = '#1a1a1a';
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, y + 1.5, w - 1, PITCH_H - 3);
      }
    });

    if (marquee) {
      const { x0, y0, x1, y1 } = marquee;
      ctx.fillStyle = 'rgba(26,26,26,0.08)';
      ctx.strokeStyle = 'rgba(26,26,26,0.5)';
      ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
      ctx.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    }

    if (playhead != null) {
      ctx.fillStyle = '#d64545';
      ctx.fillRect(beatToX(playhead), 0, 1.5, gridH);
    }
  }, [
    notes, width, gridH, totalBeats, beatsPerBar, snap, pxPerBeat,
    activePitches, playhead, selection, marquee, beatToX,
  ]);

  // Velocity lane
  useEffect(() => {
    const canvas = velRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = VEL_H * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, VEL_H);

    ctx.fillStyle = '#faf9f5';
    ctx.fillRect(0, 0, width, VEL_H);
    for (let b = 0; b <= totalBeats; b++) {
      ctx.fillStyle = b % beatsPerBar === 0 ? 'rgba(0,0,0,0.20)' : 'rgba(0,0,0,0.07)';
      ctx.fillRect(beatToX(b), 0, 1, VEL_H);
    }

    notes.forEach((n) => {
      const x = beatToX(n.start);
      const h = ((n.velocity ?? 90) / 127) * (VEL_H - 4);
      ctx.fillStyle = selection.has(n.id) ? '#d64545' : '#6c7cff';
      ctx.fillRect(x, VEL_H - h, Math.max(2, Math.min(6, beatToX(n.length))), h);
    });

    if (playhead != null) {
      ctx.fillStyle = '#d64545';
      ctx.fillRect(beatToX(playhead), 0, 1.5, VEL_H);
    }
  }, [notes, width, totalBeats, beatsPerBar, playhead, selection, beatToX]);

  // --- helpers -----------------------------------------------------------

  const hitTest = (beat, pitch) =>
    [...notes].reverse().find(
      (n) => n.pitch === pitch && beat >= n.start && beat <= n.start + n.length,
    );

  const patch = (ids, fn) =>
    onChange(notes.map((n) => (ids.has(n.id) ? { ...n, ...fn(n) } : n)));

  const dragLoop = (onMove) => {
    const up = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', up);
  };

  // --- grid interaction --------------------------------------------------

  const onGridDown = (e) => {
    const rect = gridRef.current.getBoundingClientRect();
    const beat = xToBeat(e.clientX - rect.left);
    const pitch = yToPitch(e.clientY - rect.top);
    const hit = hitTest(beat, pitch);

    if (e.button === 2 || e.altKey) {
      if (hit) {
        onBeginEdit?.();
        onChange(notes.filter((n) => n.id !== hit.id));
        setSelection((s) => {
          const next = new Set(s);
          next.delete(hit.id);
          return next;
        });
      }
      return;
    }

    // Empty space: marquee-select, or add a note if it was just a click.
    if (!hit) {
      const x0 = e.clientX - rect.left;
      const y0 = e.clientY - rect.top;
      let dragged = false;

      const move = (ev) => {
        const x1 = ev.clientX - rect.left;
        const y1 = ev.clientY - rect.top;
        if (Math.abs(x1 - x0) > 3 || Math.abs(y1 - y0) > 3) dragged = true;
        setMarquee({ x0, y0, x1, y1 });
      };
      const up = (ev) => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        setMarquee(null);

        if (!dragged) {
          onBeginEdit?.();
          const note = {
            id: uid(),
            pitch,
            start: snapBeat(beat),
            length: snap * 4,
            velocity: 90,
          };
          onChange([...notes, note]);
          setSelection(new Set([note.id]));
          onPreview?.(pitch, 90);
          return;
        }

        const x1 = ev.clientX - rect.left;
        const y1 = ev.clientY - rect.top;
        const [bLo, bHi] = [xToBeat(Math.min(x0, x1)), xToBeat(Math.max(x0, x1))];
        const [pLo, pHi] = [yToPitch(Math.max(y0, y1)), yToPitch(Math.min(y0, y1))];
        const inside = notes.filter(
          (n) =>
            n.pitch >= pLo && n.pitch <= pHi &&
            n.start + n.length >= bLo && n.start <= bHi,
        );
        setSelection((prev) => {
          const next = e.shiftKey ? new Set(prev) : new Set();
          inside.forEach((n) => next.add(n.id));
          return next;
        });
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      return;
    }

    // A note: select, then move or resize. Dragging one note in a
    // selection moves the whole selection, which is what makes editing a
    // chord bearable.
    const chosen = new Set(
      e.shiftKey ? [...selection, hit.id] : selection.has(hit.id) ? selection : [hit.id],
    );
    setSelection(chosen);
    onBeginEdit?.();

    const fromRight = beatToX(hit.start + hit.length) - (e.clientX - rect.left);
    const fromLeft = e.clientX - rect.left - beatToX(hit.start);
    const mode = fromRight < EDGE_PX ? 'right' : fromLeft < EDGE_PX ? 'left' : 'move';

    const startBeat = beat;
    const startPitch = pitch;
    const before = new Map(notes.map((n) => [n.id, { ...n }]));
    let lastPreview = hit.pitch;

    dragLoop((ev) => {
      const dBeat = xToBeat(ev.clientX - rect.left) - startBeat;
      const dPitch = yToPitch(ev.clientY - rect.top) - startPitch;

      patch(chosen, (n) => {
        const original = before.get(n.id);
        if (mode === 'right') {
          return { length: Math.max(snap, snapBeat(original.length + dBeat)) };
        }
        if (mode === 'left') {
          // Resizing from the left moves the start and shortens by the
          // same amount, so the far end stays put.
          const end = original.start + original.length;
          const start = Math.min(snapBeat(original.start + dBeat), end - snap);
          return { start: Math.max(0, start), length: end - Math.max(0, start) };
        }
        return {
          start: Math.max(0, snapBeat(original.start + dBeat)),
          pitch: Math.max(MIN_PITCH, Math.min(MAX_PITCH - 1, original.pitch + dPitch)),
        };
      });

      if (mode === 'move') {
        const next = hit.pitch + dPitch;
        if (next !== lastPreview) {
          lastPreview = next;
          onPreview?.(next, hit.velocity ?? 90);
        }
      }
    });
  };

  // --- velocity lane -----------------------------------------------------

  const onVelDown = (e) => {
    const rect = velRef.current.getBoundingClientRect();
    const set = (ev) => {
      const beat = xToBeat(ev.clientX - rect.left);
      const value = Math.max(
        1,
        Math.min(127, Math.round((1 - (ev.clientY - rect.top) / VEL_H) * 127)),
      );
      // Adjust whatever is selected, or the note under the pointer.
      const target = selection.size
        ? selection
        : new Set(
            notes
              .filter((n) => beat >= n.start && beat <= n.start + n.length)
              .map((n) => n.id),
          );
      if (target.size) patch(target, () => ({ velocity: value }));
    };
    onBeginEdit?.();
    set(e);
    dragLoop(set);
  };

  // --- keyboard ----------------------------------------------------------

  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (!selection.size) return;

      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        onBeginEdit?.();
        onChange(notes.filter((n) => !selection.has(n.id)));
        setSelection(new Set());
        return;
      }

      if ((e.metaKey || e.ctrlKey) && (e.key === 'd' || e.key === 'D')) {
        e.preventDefault();
        onBeginEdit?.();
        const copies = notes
          .filter((n) => selection.has(n.id))
          .map((n) => ({ ...n, id: uid(), start: n.start + n.length }));
        onChange([...notes, ...copies]);
        setSelection(new Set(copies.map((c) => c.id)));
        return;
      }

      const step = e.shiftKey ? beatsPerBar : snap;
      const octave = e.shiftKey ? 12 : 1;
      const moves = {
        ArrowLeft: (n) => ({ start: Math.max(0, n.start - step) }),
        ArrowRight: (n) => ({ start: n.start + step }),
        ArrowUp: (n) => ({ pitch: Math.min(MAX_PITCH - 1, n.pitch + octave) }),
        ArrowDown: (n) => ({ pitch: Math.max(MIN_PITCH, n.pitch - octave) }),
      };
      if (moves[e.key]) {
        e.preventDefault();
        onBeginEdit?.();
        patch(selection, moves[e.key]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [notes, selection, snap, beatsPerBar, onChange, onBeginEdit]);

  const labels = useMemo(() => {
    const out = [];
    for (let p = MIN_PITCH; p < MAX_PITCH; p++) {
      if (p % 12 === 0) out.push({ pitch: p, name: noteName(p), y: pitchToY(p) });
    }
    return out;
  }, []);

  const scrubTo = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onScrub?.(Math.max(0, xToBeat(e.clientX - rect.left)));
  };

  return (
    <div className="roll">
      <div className="roll-ruler-row">
        <div className="roll-gutter" />
        <div
          className="roll-ruler"
          style={{ height: RULER_H }}
          onPointerDown={(e) => {
            scrubTo(e);
            dragLoop(scrubTo);
          }}
        >
          {Array.from({ length: bars + 1 }, (_, i) => (
            <span key={i} style={{ left: beatToX(i * beatsPerBar) }}>
              {i + 1}
            </span>
          ))}
          {playhead != null && (
            <span className="roll-head" style={{ left: beatToX(playhead) }} />
          )}
        </div>
      </div>

      <div className="roll-scroll" ref={scrollRef} style={{ height }}>
        <div className="roll-body">
          <div className="roll-keys" style={{ height: gridH }}>
            {labels.map((l) => (
              <span key={l.pitch} style={{ top: l.y }}>
                {l.name}
              </span>
            ))}
          </div>
          <div className="roll-grid" ref={wrapRef} style={{ height: gridH }}>
            <canvas
              ref={gridRef}
              style={{ width: '100%', height: gridH, display: 'block' }}
              onPointerDown={onGridDown}
              onContextMenu={(e) => e.preventDefault()}
            />
          </div>
        </div>
      </div>

      <div className="roll-vel-row">
        <div className="roll-gutter">vel</div>
        <canvas
          ref={velRef}
          style={{ width: '100%', height: VEL_H, display: 'block' }}
          onPointerDown={onVelDown}
        />
      </div>
    </div>
  );
}
