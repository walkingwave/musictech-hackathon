import { useEffect, useMemo, useRef } from 'react';

// One clip: a block with a title strip, waveform, edge trim handles, and an
// optional highlighted section. Behaviour depends on the active tool:
//   move  — drag body to reposition (snapped), drag edges to trim
//   split — click to cut the clip at the cursor
//   range — drag to highlight a section (for regen / loop / delete)
export default function ClipView({
  clip,
  buffer,
  color,
  pps,
  height,
  tool,
  snapSec,
  selected,
  region,
  onSelect,
  onMove,
  onTrim,
  onSplit,
  onRange,
}) {
  const canvasRef = useRef(null);
  const width = Math.max(6, clip.duration * pps);
  const left = clip.start * pps;
  const TITLE_H = 15;
  const snap = (t) => (snapSec > 0 ? Math.round(t / snapSec) * snapSec : t);

  const peaks = useMemo(() => {
    if (!buffer) return null;
    const sr = buffer.sampleRate;
    const data = buffer.getChannelData(0);
    const startF = Math.floor(clip.offset * sr);
    const frames = Math.floor(clip.duration * sr);
    const cols = Math.max(1, Math.floor(width));
    const step = Math.max(1, Math.floor(frames / cols));
    const out = new Float32Array(cols * 2);
    for (let x = 0; x < cols; x++) {
      let min = 1;
      let max = -1;
      const s = startF + x * step;
      for (let i = 0; i < step; i++) {
        const v = data[s + i] || 0;
        if (v < min) min = v;
        if (v > max) max = v;
      }
      out[x * 2] = min;
      out[x * 2 + 1] = max;
    }
    return out;
  }, [buffer, clip.offset, clip.duration, width]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks) return;
    const h = height - TITLE_H;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, h);
    const mid = h / 2;
    ctx.fillStyle = '#1a1a1a';
    const cols = peaks.length / 2;
    for (let x = 0; x < cols; x++) {
      const y1 = mid - peaks[x * 2 + 1] * (mid - 1);
      const y2 = mid - peaks[x * 2] * (mid - 1);
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
  }, [peaks, width, height]);

  // Timeline seconds from a pointer event, relative to the lane.
  const eventTime = (ev, laneLeft) => (ev.clientX - laneLeft) / pps;

  const onPointerDown = (e) => {
    e.stopPropagation();
    onSelect();
    const laneLeft = e.currentTarget.parentElement.getBoundingClientRect().left;

    if (tool === 'split') {
      onSplit(snap(eventTime(e, laneLeft)));
      return;
    }
    if (tool === 'range') {
      const startT = eventTime(e, laneLeft);
      const move = (ev) => {
        const cur = eventTime(ev, laneLeft);
        const a = Math.max(clip.start, snap(Math.min(startT, cur)));
        const b = Math.min(clip.start + clip.duration, snap(Math.max(startT, cur)));
        onRange(a, b);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      return;
    }
    // move tool — reposition whole clip
    const startX = e.clientX;
    const origStart = clip.start;
    const move = (ev) => onMove(snap(origStart + (ev.clientX - startX) / pps));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const trimDown = (side) => (e) => {
    e.stopPropagation();
    onSelect();
    const laneLeft = e.currentTarget.parentElement.parentElement.getBoundingClientRect().left;
    const move = (ev) => {
      const t = snap(eventTime(ev, laneLeft));
      onTrim(side, t);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  return (
    <div
      className={`clip${selected ? ' selected' : ''}${tool === 'split' ? ' cut' : ''}`}
      style={{ left, width, height }}
      onPointerDown={onPointerDown}
    >
      <div className="clip-title" style={{ height: TITLE_H, background: color }}>
        {clip.part || 'audio'}
      </div>
      <canvas ref={canvasRef} style={{ width, height: height - TITLE_H, display: 'block' }} />
      {region && (
        <div
          className="clip-region"
          style={{ left: (region.a - clip.start) * pps, width: (region.b - region.a) * pps }}
        />
      )}
      {tool === 'move' && (
        <>
          <span className="trim-handle left" onPointerDown={trimDown('left')} />
          <span className="trim-handle right" onPointerDown={trimDown('right')} />
        </>
      )}
    </div>
  );
}
