# Voice-to-Beat / Voice-to-Instrument: Research and Hackathon Proposal

## Scope

Can a musician record a short microphone clip—such as beatboxing, a vocal sample, or humming—and turn it into editable drum or guitar material, then add a fitting bassline or backing track? This report assesses existing implementations and a feasible two-day project for the Music Hackspace Montréal 2026 Stable Audio 3 challenge.

**Date conducted:** 2026-08-22

## Executive summary

This is feasible as a **controllable musical reinterpretation** tool, not as universal voice-to-instrument conversion.

A robust MVP is: record 6–12 seconds of beatbox or humming; derive an editable drum grid or melody MIDI; render it with curated drum/guitar/bass sounds; then offer Stable Audio 3 as a local AI sound-design or variation layer. Keeping MIDI/events as the source of truth preserves timing and gives the musician correction controls when recognition or generation is imperfect.

Stable Audio 3 is a good fit for generating samples, audio-to-audio variation, fills, continuation, and optional final renders. It is **not** the best core mechanism for exact beatbox transcription, fixed BPM/key, or faithful vocal-to-guitar conversion. For those tasks, use beatbox-to-MIDI and vocal-to-MIDI tooling first, then render/edit with conventional synthesis and optionally Stable Audio 3.

## Verified challenge requirements

The Music Hackspace Stability AI Challenge asks teams to build a **publicly available, accessible, community-facing tool for music producers** using Stable Audio 3 and to show the strengths of local, open models. The page describes Stable Audio 3 as a family of open-weight models for full songs, solo instruments, and sound effects, with fast inference on consumer hardware and personal customization.

Its suggested directions include:

- An interactive drum machine/sampler with generated samples.
- Integration with Max for Live, VSTs, or other existing workflows.
- A personal streaming radio station.
- A personal LoRA trainer adapted to a sample library.

This project directly fits the first two directions: it can turn a beatbox performance into a playable sampler/sequencer and export audio/MIDI to a DAW. [1]

## Current implementations and useful components

| Problem | Current implementation / approach | Practical role in this project |
|---|---|---|
| Beatbox to drums/MIDI | [Deepbeat](https://github.com/eupston/Deepbeat-beatbox2midi); [BBX2Drum-Transcription](https://github.com/s0radummy/BBX2Drum-Transcription) | Concrete precedents for detecting vocal-percussion events, categorizing kick/snare/hi-hat-like sounds, and producing MIDI. Use as research/code starting points; test carefully. |
| Onsets and beat grid | Web Audio/Tone.js, plus basic onset analysis | For an MVP, detect candidate hits but let the user correct the grid and labels. A transparent editable interface is more dependable than claiming perfect classification. [10] |
| Hum/vocal to MIDI | Spotify [Basic Pitch](https://github.com/spotify/basic-pitch) | Practical audio-to-MIDI route. It exports MIDI with pitch bends and works best on one source at a time, making it suitable for a clean hummed/sung melody. [7] |
| Heavier transcription | Magenta [MT3](https://github.com/magenta/mt3) | A pretrained multi-instrument transcription research option, but too heavy/risky for the hackathon core. |
| Timbre transfer | [DDSP](https://github.com/magenta/ddsp); [RAVE](https://github.com/acids-ircam/RAVE) | Better precedents for learned timbre modeling than Stable Audio alone. Both require suitable models/training and are post-MVP work. [5, 6] |
| Backing/accompaniment | [Symbolic Accompaniment Transformer](https://github.com/stemkwk/symbolic-accompaniment-transformer) | Vocal-melody-to-symbolic-accompaniment precedent. For the event, use simple deterministic bass patterns rather than depend on another generative model. |
| Bass accompaniment | [Bass Tablature Accompaniment Generation](https://github.com/adhooge/BassTablatureGeneration) | Research/code precedent for generated bass accompaniment; use only as inspiration unless it is tested in advance. |
| MIDI rendering | [FluidSynth](https://github.com/FluidSynth/fluidsynth), Tone.js, or a licensed/self-made sample library | Render editable MIDI using an appropriate SoundFont, synth, or curated samples. |

## Stable Audio 3 applicability

### What is supported

Stable Audio 3’s official repository describes three inference modes: text-to-audio, audio-to-audio editing, and inpainting/continuation. It also describes variable-length generation, LoRA fine-tuning, and a 44.1 kHz stereo semantic-acoustic autoencoder. The accompanying technical report describes latent-diffusion models for variable-length audio generation and editing. [2, 3]

The repository currently lists:

| Model | Hardware described by repository | Maximum length | Suitable use |
|---|---|---:|---|
| Small-Music | CPU; Apple Silicon/CoreML path documented | 120 seconds | Local music-oriented prototype |
| Small-SFX | CPU; Apple Silicon/CoreML path documented | 120 seconds | Generated effects/one-shots |
| Medium | CUDA GPU | 380 seconds | Higher-quality local audio generation/editing |
| Large | API only | 380 seconds | Not suitable as the local demo dependency |

### Best role in the pipeline

Use Stable Audio 3 as a **generation/rendering layer**, not the transcription engine:

1. Record a participant-owned, consented vocal/beatbox clip.
2. Extract beat events/classes or a monophonic melody as MIDI.
3. Quantize and let the user correct BPM, key, grid, and labels.
4. Render deterministic drum, guitar, and bass parts locally.
5. Optionally use Stable Audio 3 locally to:
   - generate drum one-shots or texture layers from prompts;
   - make an audio-to-audio variation using the captured clip plus a constrained prompt;
   - inpaint a fill;
   - continue a short loop; or
   - produce an optional 10–30 second polished variation.
6. Export the mixed audio **and** editable MIDI/events.

Example constrained prompts:

- “Tight acoustic drum kit; preserve the rhythmic accents.”
- “Muted funk electric guitar riff; preserve the rhythmic contour.”
- “Warm sub-bass line at 120 BPM in A minor, sparse and syncopated.”

Label this action **Generate AI variation**, not “convert exactly.” Stable Audio documentation demonstrates audio restyling/editing; it does not establish guaranteed preservation of vocal timing, pitch contour, guitar voicing, or instrumental technique.

### Access and license caveats

The challenge links both the GitHub repository and Hugging Face model collection. At research time, model access on Hugging Face is gated. “Open weights” therefore does not mean anonymous or unrestricted access. Apply/test access early and check the current Stability AI Community License and model-specific terms before distributing a public tool or output. [1, 2, 4, 5]

## Recommended MVP: “Beatbox/Hum → Editable Groove”

### User experience

1. Press record and capture a 6–12 second beatbox or hum.
2. Select **Drums** or **Guitar riff** mode.
3. The app proposes BPM/onsets and a drum grid or melody MIDI.
4. The musician edits the proposed hits, notes, BPM, key, swing, and genre preset.
5. The app adds a simple bassline and backing layer.
6. Optionally select **Generate Stable Audio variation**.
7. Export a mixed WAV plus MIDI and a JSON provenance manifest.

### Architecture

```text
Browser microphone (getUserMedia)
  → local WAV capture / trim / high-pass / normalize
  → user BPM/key controls + onset detection
       ├─ Drums: onsets → suggested kick/snare/hat labels → editable grid → local kit
       ├─ Guitar: clean monophonic hum → Basic Pitch → editable MIDI → sampler/synth
       ├─ Bass/backing: BPM + key + genre → deterministic MIDI patterns → local synth
       └─ Optional SA3 adapter: prompt + audio → local SA3 output / inpaint / continuation
  → local/offline mix render
  → WAV/MP3 + MIDI + JSON manifest
```

### Core stack

- **UI/audio capture:** browser `getUserMedia`, Web Audio API, Tone.js.
- **Drums:** onset detection plus a small, editable kick/snare/closed-hat/open-hat vocabulary.
- **Melody:** Basic Pitch for clean monophonic humming; provide a piano-roll editor as a fallback.
- **Bass:** a small rule-based vocabulary (root, fifth, octave, passing note) tied to user-selected key and genre.
- **Rendering:** Tone.js, FluidSynth, or self-made/licensed samples.
- **Stable Audio:** a server-side or local adapter with a single `generate_audio(prompt, init_audio, options)` interface. Keep weights/API credentials out of browser code.

## Feasibility assessment

| Feature | Feasibility in two days | Notes |
|---|---|---|
| Record, trim, playback, waveform, WAV export | High | Standard browser work; do it first. |
| Editable drum sequencer and curated kit | High | Dependable and demo-friendly. |
| Suggested beatbox onsets/three-class labels | Medium-high | Start with onset detection and correction; do not promise universal beatbox recognition. |
| Hum to editable MIDI | Medium-high | Limit to a clean single voice; use Basic Pitch or manual piano roll fallback. |
| Deterministic bassline/backing | High | Keep harmony/pattern rules small and editable. |
| Local SA3-generated samples/variations | Medium | Requires gated model access and advance hardware smoke test. |
| SA3 Medium GPU render | Medium | Good if a compatible CUDA GPU is available; do not make it essential. |
| LoRA training/personal voice-to-instrument model | Low | Dataset preparation, training, testing, and licensing make this a stretch goal. |
| Universal convincing voice-to-guitar conversion | Low | Voice does not specify guitar fingering, voicing, articulation, amp tone, or arrangement. |

## Two-day implementation plan

1. **Hours 0–3:** microphone capture, trimming, waveform/playback, BPM/key overrides, fixed 16-step sequencer.
2. **Hours 3–8:** onset suggestions, editable labels, curated kit, transport, WAV export.
3. **Hours 8–13:** deterministic bassline and two genre presets.
4. **Hours 13–18:** Basic Pitch or simple manual piano roll for the humming-to-guitar path.
5. **Hours 18–24:** only after a successful access, format, latency, and license smoke test, connect Stable Audio 3.
6. **Day 2:** add provenance, test with noisy captures, preload consented examples, and make the demo work fully without network/GPU generation.

## Key limitations, risks, and mitigations

| Risk or limitation | Mitigation |
|---|---|
| Beatboxing does not unambiguously identify intended drum instruments | Treat classifications as suggestions; expose editable labels/grid. |
| Humming gives pitch contour but not guitar voicing/articulation | Output editable MIDI and use a selected guitar sound; frame it as reinterpretation. |
| Stable Audio output changes timing or meter | Keep deterministic MIDI/grid output as the source of truth. |
| Noisy hackathon venue | Use short clips, headphones, input meter, trim/threshold/quantize controls. |
| Network/API/model access failure | Make the sequencer/sampler entirely local; cache/preload demo outputs. |
| Key/BPM estimation error | Explicit user override controls. |
| Privacy and consent | Default to local processing; request affirmative consent before any upload; explain deletion/retention. |
| Rights and licensing uncertainty | Only accept authorized recordings; maintain an asset/model manifest; verify model/sample licenses. |

## Methodology and source selection

Research was delegated to a dedicated sub-agent. Primary sources were prioritized: the event challenge, Stable Audio 3 repository, Stability AI product/licensing materials, Hugging Face model information, official repositories, and academic papers. The Stable Audio 3 paper is an arXiv preprint, not confirmed peer-reviewed. Repository examples demonstrate implementation precedents but do not independently establish their accuracy for all microphones, languages, beatbox vocabularies, or singing styles.

## References

1. Music Hackspace. “Music Technology Hackathon: Build the Future of Creative Tools — Montréal, August 2026,” Challenges tab. Organizer webpage, accessed 2026-08-22. Not peer-reviewed.  
   https://musichackspace.org/events/hackathon-montreal-august-2026?tab=challenges

2. Stability AI. *Stable Audio 3* GitHub repository and README, 2026; accessed 2026-08-22. Official source documentation; not peer-reviewed.  
   https://github.com/Stability-AI/stable-audio-3

3. Evans, Zach, Julian D. Parker, Matthew Rice, CJ Carr, Zack Zukowski, Josiah Taylor, and Jordi Pons. “Stable Audio 3.” arXiv:2605.17991, 18 May 2026. Scholarly technical preprint; peer-review status not established.  
   https://arxiv.org/abs/2605.17991

4. Stability AI. “Stable Audio 3” Hugging Face model collection, accessed 2026-08-22. Official model collection; not peer-reviewed.  
   https://huggingface.co/collections/stabilityai/stable-audio-3

5. Stability AI. “License Information.” Official licensing page, accessed 2026-08-22. Not peer-reviewed.  
   https://stability.ai/license

6. Engel, Jesse, Lamtharn Hantrakul, Chenjie Gu, and Adam Roberts. “DDSP: Differentiable Digital Signal Processing.” *International Conference on Learning Representations*, 2020. Peer-reviewed conference paper.  
   https://doi.org/10.48550/arXiv.2001.04643

7. Spotify Audio Intelligence Lab. *Basic Pitch* repository and documentation, accessed 2026-08-22. Official implementation documentation; the associated ICASSP 2022 work is peer-reviewed.  
   https://github.com/spotify/basic-pitch

8. Caillon, Antoine, and Philippe Esling. “RAVE: A Variational Autoencoder for Fast and High-Quality Neural Audio Synthesis.” arXiv:2111.05011, 2021. Scholarly preprint; peer-review status not established.  
   https://doi.org/10.48550/arXiv.2111.05011

9. ACIDS-IRCAM. *RAVE* repository. Official implementation documentation, accessed 2026-08-22. Not peer-reviewed.  
   https://github.com/acids-ircam/RAVE

10. Tone.js contributors. *Tone.js* repository and documentation, accessed 2026-08-22. Implementation documentation; not peer-reviewed.  
    https://github.com/Tonejs/Tone.js

11. Copet, Jade, et al. “Simple and Controllable Music Generation.” *Advances in Neural Information Processing Systems*, 2023. Peer-reviewed conference paper.  
    https://doi.org/10.48550/arXiv.2306.05284

12. Meta AI. *MusicGen Small* model card, accessed 2026-08-22. Official model card; not peer-reviewed.  
    https://huggingface.co/facebook/musicgen-small

13. Rouard, Simon, Francisco Massa, and Alexandre Défossez. “Hybrid Transformers for Music Source Separation.” arXiv:2211.08553, 2022. Scholarly preprint; peer-review status not established.  
    https://doi.org/10.48550/arXiv.2211.08553
