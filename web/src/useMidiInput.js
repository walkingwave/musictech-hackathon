import { useCallback, useEffect, useRef, useState } from 'react';

// Live input from a connected MIDI controller, via the Web MIDI API.
//
// Two things come out of this hook:
//   - `active`, the notes currently held down, for lighting up the keys
//   - recorded notes, delivered through the `onNote` callback once a note
//     is released and its length is known
//
// Note-on with velocity 0 is a note-off. Plenty of controllers never send
// an actual note-off message, so treating 0x90 as unconditionally "on"
// leaves notes stuck down forever.

const NOTE_ON = 0x90;
const NOTE_OFF = 0x80;

export function useMidiInput({ enabled = true, onNote } = {}) {
  const [devices, setDevices] = useState([]);
  const [active, setActive] = useState(() => new Map()); // pitch -> velocity
  const [error, setError] = useState(null);

  // Held notes waiting for their note-off, keyed by pitch.
  const heldRef = useRef(new Map());
  const onNoteRef = useRef(onNote);
  onNoteRef.current = onNote;

  const handleMessage = useCallback((event) => {
    const [status, pitch, velocity] = event.data;
    const command = status & 0xf0;

    const isNoteOn = command === NOTE_ON && velocity > 0;
    const isNoteOff = command === NOTE_OFF || (command === NOTE_ON && velocity === 0);

    if (isNoteOn) {
      heldRef.current.set(pitch, { at: performance.now(), velocity });
      setActive((prev) => new Map(prev).set(pitch, velocity));
    } else if (isNoteOff) {
      const held = heldRef.current.get(pitch);
      heldRef.current.delete(pitch);
      setActive((prev) => {
        const next = new Map(prev);
        next.delete(pitch);
        return next;
      });
      if (held) {
        onNoteRef.current?.({
          pitch,
          velocity: held.velocity,
          startedAt: held.at,
          endedAt: performance.now(),
        });
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    if (!navigator.requestMIDIAccess) {
      setError('This browser has no Web MIDI support — try Chrome or Edge.');
      return undefined;
    }

    let access = null;
    let cancelled = false;

    const attach = (midiAccess) => {
      const inputs = [...midiAccess.inputs.values()];
      setDevices(inputs.map((i) => ({ id: i.id, name: i.name, manufacturer: i.manufacturer })));
      inputs.forEach((input) => {
        input.onmidimessage = handleMessage;
      });
    };

    navigator
      .requestMIDIAccess()
      .then((midiAccess) => {
        if (cancelled) return;
        access = midiAccess;
        attach(midiAccess);
        // Controllers get plugged in after the page loads more often than not.
        midiAccess.onstatechange = () => attach(midiAccess);
      })
      .catch((e) => setError(`Could not access MIDI — ${e.message}`));

    return () => {
      cancelled = true;
      if (access) {
        [...access.inputs.values()].forEach((input) => {
          input.onmidimessage = null;
        });
        access.onstatechange = null;
      }
    };
  }, [enabled, handleMessage]);

  // Release everything — otherwise a note held while recording stops stays
  // stuck on the keyboard display.
  const panic = useCallback(() => {
    heldRef.current.clear();
    setActive(new Map());
  }, []);

  return { devices, active, error, panic };
}
