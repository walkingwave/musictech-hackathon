import { useEffect, useRef, useState } from 'react';

// A MIDI track's instrument slot — the device slot, in Ableton terms.
//
// Empty until you load something into it. Swapping the instrument leaves
// the notes untouched, which is the point: the part is the part, and the
// sound is a choice you can change your mind about.

export default function InstrumentSlot({ instrument, instruments, onLoad, onClear, compact }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Close on an outside click, so the picker does not linger over the
  // timeline while you are trying to work on it.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (!ref.current?.contains(e.target)) setOpen(false);
    };
    window.addEventListener('pointerdown', onDown);
    return () => window.removeEventListener('pointerdown', onDown);
  }, [open]);

  return (
    <div className={`slot${compact ? ' compact' : ''}`} ref={ref}>
      <button
        className={`slot-btn${instrument ? ' loaded' : ''}`}
        onClick={() => setOpen((v) => !v)}
        title={instrument ? instrument.prompt : 'Load an instrument into this track'}
      >
        {instrument ? instrument.name : 'empty slot'}
      </button>

      {open && (
        <div className="slot-pop">
          <div className="slot-pop-head">Load instrument</div>
          {instruments.length === 0 && (
            <div className="slot-empty">
              No instruments yet — make one on the New Instrument tab.
            </div>
          )}
          {instruments.map((i) => (
            <button
              key={i.id}
              className={i.id === instrument?.id ? 'on' : ''}
              onClick={() => {
                onLoad(i);
                setOpen(false);
              }}
            >
              <span className="slot-name">{i.name}</span>
              <span className="slot-desc">{i.prompt}</span>
            </button>
          ))}
          {instrument && (
            <button
              className="slot-clear"
              onClick={() => {
                onClear();
                setOpen(false);
              }}
            >
              Clear slot
            </button>
          )}
        </div>
      )}
    </div>
  );
}
