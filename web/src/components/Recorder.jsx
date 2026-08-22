import { useRef, useState } from 'react';

// RECORD / UPLOAD as the two big spec-sheet buttons, then a file row with a
// (static) waveform once a vocal is loaded. Hands blob + filename to onSubmit.
export default function Recorder({ onSubmit, fileName }) {
  const recorderRef = useRef(null);
  const [recording, setRecording] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);

  const submit = (blob, filename) => {
    setPreviewUrl(URL.createObjectURL(blob));
    onSubmit(blob, filename);
  };

  const toggleRecord = async () => {
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      recorderRef.current.stop();
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    const recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      submit(new Blob(chunks, { type: recorder.mimeType }), 'recording.webm');
    };
    recorder.start();
    recorderRef.current = recorder;
    setRecording(true);
  };

  return (
    <>
      <div className="big-buttons">
        <button type="button" onClick={toggleRecord}>
          <span className={`dot${recording ? ' rec' : ''}`} />
          {recording ? 'Stop' : 'Record'}
        </button>
        <label className="upload">
          <span className="box" />
          Upload
          <input
            type="file"
            accept="audio/*"
            hidden
            onChange={(e) => {
              const file = e.target.files[0];
              if (file) submit(file, file.name);
            }}
          />
        </label>
      </div>

      {fileName && (
        <div className="file-row">
          <div className="file-meta">
            <div className="file-name">{fileName}</div>
            <div className="file-spec">loaded · analyzed</div>
          </div>
          <div className="file-wave">
            <Waveform />
          </div>
          <button className="file-x" type="button" title="clear" onClick={() => window.location.reload()}>
            ×
          </button>
        </div>
      )}

      {previewUrl && <audio className="preview" src={previewUrl} controls />}
    </>
  );
}

// Purely decorative waveform bars, matching the mockup. Deterministic so it
// does not reshuffle on every render.
function Waveform() {
  const bars = Array.from({ length: 90 }, (_, i) => 20 + Math.abs(Math.sin(i * 0.7) * 60) + (i % 5) * 4);
  return (
    <svg width="100%" height="40" preserveAspectRatio="none" viewBox="0 0 450 40">
      {bars.map((h, i) => (
        <rect key={i} x={i * 5} y={(40 - h / 2.2) / 2} width="2" height={h / 2.2} fill="#1a1a1a" />
      ))}
    </svg>
  );
}
