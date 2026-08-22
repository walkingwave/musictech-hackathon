import { useEffect, useMemo, useRef } from 'react';

// Canvas waveform for one track. Peaks are computed once from the AudioBuffer
// (min/max per column), then redrawn cheaply as the shared playhead moves.
// Click or drag scrubs the transport via onSeek.
export default function Waveform({ buffer, duration, position, muted, onSeek }) {
  const canvasRef = useRef(null);
  const WIDTH = 620;
  const HEIGHT = 48;

  // Downsample to one [min,max] pair per pixel column. Memoized on the buffer.
  const peaks = useMemo(() => {
    if (!buffer) return null;
    const data = buffer.getChannelData(0);
    const step = Math.max(1, Math.floor(data.length / WIDTH));
    const out = new Float32Array(WIDTH * 2);
    for (let x = 0; x < WIDTH; x++) {
      let min = 1;
      let max = -1;
      const start = x * step;
      for (let i = 0; i < step; i++) {
        const v = data[start + i] || 0;
        if (v < min) min = v;
        if (v > max) max = v;
      }
      out[x * 2] = min;
      out[x * 2 + 1] = max;
    }
    return out;
  }, [buffer]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = WIDTH * dpr;
    canvas.height = HEIGHT * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, WIDTH, HEIGHT);

    const mid = HEIGHT / 2;
    const played = duration ? (position / duration) * WIDTH : 0;

    for (let x = 0; x < WIDTH; x++) {
      const min = peaks[x * 2];
      const max = peaks[x * 2 + 1];
      const y1 = mid - max * (mid - 2);
      const y2 = mid - min * (mid - 2);
      // Dim the unplayed remainder; a muted track renders faint throughout.
      ctx.fillStyle = muted ? '#cfccc3' : x <= played ? '#1a1a1a' : '#b8b5ac';
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
  }, [peaks, position, duration, muted]);

  const scrub = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    onSeek(Math.max(0, Math.min(1, frac)) * duration);
  };

  return (
    <div
      className="waveform"
      onClick={scrub}
      onMouseDown={(e) => e.buttons === 1 && scrub(e)}
      onMouseMove={(e) => e.buttons === 1 && scrub(e)}
    >
      <canvas ref={canvasRef} style={{ width: WIDTH, height: HEIGHT }} />
      {duration > 0 && (
        <span className="playhead" style={{ left: `${(position / duration) * 100}%` }} />
      )}
    </div>
  );
}
