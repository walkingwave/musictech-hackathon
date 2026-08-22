import { useEffect, useMemo, useRef } from 'react';

// One Ableton-style clip: a colored block with a title strip and a waveform.
// Drag the body to move it along the timeline (snapped to bars by the parent).
// Alt+drag marks a section (for section-regenerate). Click selects it.
export default function ClipView({
  clip,
  buffer,
  color,
  pps,
  height,
  selected,
  region,
  onSelect,
  onMove,
  onRegion,
}) {
  const canvasRef = useRef(null);
  const drag = useRef(null);
  const width = Math.max(6, clip.duration * pps);
  const left = clip.start * pps;
  const TITLE_H = 15;

  // Peaks for the clip's region [offset, offset+duration] of the buffer.
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

  const onPointerDown = (e) => {
    e.stopPropagation();
    onSelect();
    const parentLeft = e.currentTarget.parentElement.getBoundingClientRect().left;
    if (e.altKey) {
      // Section selection within the clip.
      const originX = e.clientX;
      const startT = clip.start + (e.clientX - (parentLeft + left)) / pps;
      const move = (ev) => {
        const curT = clip.start + (ev.clientX - (parentLeft + left)) / pps;
        const a = Math.max(clip.start, Math.min(startT, curT));
        const b = Math.min(clip.start + clip.duration, Math.max(startT, curT));
        onRegion(a, b);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      return;
    }
    // Move the whole clip.
    drag.current = { startX: e.clientX, origStart: clip.start };
    const move = (ev) => {
      const delta = (ev.clientX - drag.current.startX) / pps;
      onMove(drag.current.origStart + delta);
    };
    const up = () => {
      drag.current = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  return (
    <div
      className={`clip${selected ? ' selected' : ''}`}
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
          style={{
            left: (region.a - clip.start) * pps,
            width: (region.b - region.a) * pps,
          }}
        />
      )}
    </div>
  );
}
