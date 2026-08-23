import { useRef, useState } from 'react';

// Live recording inside the studio. Captures the mic and hands the raw blob
// up to onRecorded, which decodes it and drops a clip at the playhead. Any
// length — start recording, perform, stop.
export default function StudioRecorder({ onRecorded }) {
  const recorderRef = useRef(null);
  const [recording, setRecording] = useState(false);

  const toggle = async () => {
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      recorderRef.current.stop();
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      onRecorded(new Blob(chunks, { type: rec.mimeType }));
    };
    rec.start();
    recorderRef.current = rec;
    setRecording(true);
  };

  return (
    <button className={`rec-btn${recording ? ' on' : ''}`} onClick={toggle}>
      <span className="rec-dot" />
      {recording ? 'Stop' : 'Record clip'}
    </button>
  );
}
