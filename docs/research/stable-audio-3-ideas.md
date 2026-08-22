# Stable Audio 3 Hackathon Ideas

## Research question and scope

What does the Music Hackspace Montréal August 2026 Stable Audio 3 challenge ask teams to build, what can Stable Audio 3 do, and what additional project ideas are feasible during the two-day hackathon?

**Date conducted:** 2026-08-22

## Executive summary

Stable Audio 3 is a family of open-weight generative-audio models for text-to-audio, audio-to-audio editing, continuation, and inpainting. The challenge asks for a publicly available, accessible tool for music producers that demonstrates local open models and encourages open development.

The strongest hackathon strategy is to build a focused musician workflow around generation—not a new model. The best candidates are:

1. **Local sample explorer/sampler:** generate, audition, tag, and export samples.
2. **Audio repair and continuation tool:** upload a loop, describe a replacement or extension, and use inpainting.
3. **Generative drum machine:** turn generated one-shots into a playable pattern.
4. **DAW companion:** send prompts and generated clips to Max for Live, a VST, or a simple drag-and-drop web workflow.

Use the Small model for the most reliable local demo: the official repository describes it as CPU-capable, with music and sound-effects variants and generation up to 120 seconds. Medium offers higher quality but requires a CUDA GPU and supports up to 380 seconds. Large is API-only. Confirm Hugging Face access, licenses, and hardware before committing.

## What the organizers ask for

The event’s Stability AI challenge says Stable Audio 3 is a new family of open-weight models that can generate full songs, solo instruments, and sound effects, with fast inference on consumer hardware and support for personal customization. Teams should create a publicly available and accessible tool for music producers, showing the strengths of local open models.

The organizer’s concrete thought starters are:

- An interactive drum machine or sampler using generated samples.
- Integration with existing tools and workflows, such as Max for Live or VSTs.
- A personal streaming radio station.
- A personal LoRA trainer adapted to a user’s own sample library.

The event links teams to the official repository and Stable Audio 3 Hugging Face collection:

- [Stable Audio 3 repository](https://github.com/Stability-AI/stable-audio-3)
- [Stable Audio 3 Hugging Face collection](https://huggingface.co/collections/stabilityai/stable-audio-3)

## Verified capabilities and practical constraints

According to the official repository and technical report, Stable Audio 3 supports:

- Text-to-audio generation.
- Audio-to-audio editing.
- Inpainting of selected regions.
- Continuation/extension of recordings.
- Variable-length generation.
- Stereo, 44.1 kHz audio through the SAME semantic-acoustic autoencoder.
- LoRA fine-tuning and runtime personalization.

The repository currently describes these model options:

| Model | Local hardware | Maximum length | Intended use |
|---|---|---:|---|
| Small-Music | CPU; also Apple Silicon/CoreML paths | 120 seconds | Music generation |
| Small-SFX | CPU; also Apple Silicon/CoreML paths | 120 seconds | Sound effects |
| Medium | CUDA GPU | 380 seconds | Higher-quality music and effects |
| Large | API only | 380 seconds | Highest quality |

Important caveats:

- “Open weights” does not necessarily mean immediate, unauthenticated access. The Hugging Face model pages currently indicate restricted access, so teams should apply for access and test downloads early.
- The event page says Stable Audio 3 is fast on consumer hardware; actual speed depends on model, machine, decoding settings, and audio length.
- Generated audio should not be treated as guaranteed to obey exact BPM, key, bar structure, instrumentation, or loop points. Add trimming, crossfading, normalization, and manual approval.
- LoRA training is a stretch goal. It requires a clean, well-described sample library, training time, storage, and a simple way to demonstrate the result.
- Review the current model and output licenses before publishing generated audio or accepting user uploads.

## Additional feasible ideas

### 1. Local sample explorer and sampler — **high feasibility; recommended**

A desktop or browser interface generates several variants from a prompt, shows waveforms and metadata, lets a musician audition and bookmark them, and exports WAV files.

**MVP:** prompt field, duration selector, generate button, waveform player, favorites, WAV export, JSON sidecar containing prompt/model/date.

**Why it fits:** It directly extends the organizer’s sampler idea and can run locally with Small. A mock generator can keep the UI demoable while models download or generate.

### 2. Inpainting and continuation assistant — **high/medium feasibility**

A musician uploads a loop or short recording, selects a region on the waveform, describes what should replace it, and previews the result. A second mode extends the clip.

**MVP:** upload, waveform region selection, prompt, one-click inpaint/continue, A/B playback, export.

**Why it fits:** Inpainting is a distinctive Stable Audio 3 capability and produces a clearer before/after demo than generic text-to-music generation.

### 3. Generative drum machine — **high feasibility**

Generate kick, snare, hat, percussion, and texture candidates, then map them to pads or a step sequencer.

**MVP:** four instrument lanes, eight generated candidates per lane, 16-step sequencer, BPM control for playback, WAV/MIDI export.

**Risk:** the model may generate mixed or longer sounds rather than isolated one-shots. Use trimming, silence detection, and a small curated prompt set.

### 4. Prompt-to-DAW bridge — **medium feasibility**

A Max for Live device, VST prototype, or lightweight local web app lets a producer generate a clip and drag it directly into a session.

**MVP:** local HTTP service plus a Max for Live device or drag-and-drop export. Avoid building a full cross-platform plugin in two days.

**Why it fits:** It directly answers the organizer’s integration suggestion while keeping the model adapter independent of the UI.

### 5. Session-aware contrast generator — **medium feasibility**

Given a user’s loop, generate candidates that are intentionally brighter, darker, sparser, denser, more percussive, or more reverberant.

**MVP:** extract loudness, spectral centroid, onset density, and estimated tempo; convert a selected contrast into a prompt; rank generated results by simple feature distance.

**Caveat:** “Contrasting” is perceptual. Present the ranking as a useful heuristic, not an objective musical judgment.

### 6. Personal sample-library LoRA wizard — **medium/low feasibility; stretch**

Guide a producer through organizing a sample library, writing captions, launching a LoRA training job, and comparing base versus personalized generations.

**MVP:** dataset validator, caption template generator, training launcher, and side-by-side comparison using a small prepared dataset.

**Risk:** full training may exceed hackathon time or available hardware. Make the trainer an optional extension of the sample explorer, not the core demo.

### 7. Community sound radio — **medium feasibility**

A local “radio station” continuously generates short themed segments or transitions from open prompts, with a queue that listeners can remix or save.

**MVP:** curated prompt playlist, background generation, crossfaded playback, save/remix controls.

**Risk:** continuous generation, content moderation, and licensing make this more complex than a sample tool. Keep it local and finite for the hackathon.

### 8. Generation provenance and remix notebook — **very high feasibility**

Save every prompt, model, generation setting, source clip, edit region, LoRA, and user rating in a searchable project notebook.

**Why it matters:** It is useful infrastructure for every other idea and makes open-model experimentation reproducible. It can be paired with the sample explorer or inpainting tool.

## Recommended implementation plan

1. Build a model adapter with a single internal operation such as `generate_audio(prompt, audio_input, options)`.
2. Start with the official Small model and short clips; add Medium only if a suitable GPU is available.
3. Cache outputs and provide a mock mode for UI development and judging.
4. Store prompt, model ID, timestamp, seed/settings when available, and source attribution.
5. Add basic audio hygiene: format validation, silence trimming, loudness normalization, and crossfades.
6. Deliver one complete musician workflow with a visible before/after result.
7. Publish a small README with setup instructions, model-access requirements, license notes, and a few example prompts.

### Suggested team choice

Build **“Stable Audio Sample Lab”**: a local sample explorer combining the organizer’s sampler idea with provenance, simple tags, and optional inpainting. It has a compelling demo, uses the open-weight Small model, remains useful even when outputs are imperfect, and can grow into a DAW bridge if time permits.

## Methodology and source-selection notes

The event page and Stability AI’s official repository/product page were treated as primary sources for the challenge requirements and model capabilities. The Stable Audio 3 technical report was used for architectural and performance context. The Hugging Face collection was checked for currently listed models and access status. Scholarly sources were prioritized where available; the Stable Audio 3 paper is an arXiv preprint and should not be described as peer-reviewed unless a later venue publication is confirmed.

## Limitations and uncertainty

- Model cards and access policies can change before or during the event.
- The repository and technical report contain slightly different parameter-count presentations for Small; this does not change the recommended hardware strategy.
- Benchmarks reported by the authors are not guarantees for every consumer machine or workflow.
- Musical quality, prompt adherence, loopability, and “contrast” are partly subjective and need human evaluation.
- Licensing and rights for model weights, user uploads, LoRA datasets, and generated outputs must be checked against the current terms before public release.

## References

1. Music Hackspace. “Music Technology Hackathon: Build the Future of Creative Tools — Montréal, August 2026,” Challenges tab. Event/organizer webpage, accessed 2026-08-22. Not peer-reviewed.  
   https://musichackspace.org/events/hackathon-montreal-august-2026?tab=challenges

2. Evans, Zach, Julian D. Parker, Matthew Rice, CJ Carr, Zack Zukowski, Josiah Taylor, and Jordi Pons. “Stable Audio 3.” arXiv:2605.17991, 18 May 2026. Scholarly technical preprint; peer-review status not established.  
   https://arxiv.org/abs/2605.17991

3. Stability AI. “Stable Audio 3.” Official product page, accessed 2026-08-22. Not peer-reviewed.  
   https://stability.ai/stable-audio

4. Stability AI. *Stable Audio 3* GitHub repository. Official source code, README, and MIT license, 2026; accessed 2026-08-22. Not peer-reviewed.  
   https://github.com/Stability-AI/stable-audio-3

5. Stability AI. “Stable Audio 3” Hugging Face model collection. Official model collection, accessed 2026-08-22. Model pages are access-restricted at the time of research. Not peer-reviewed.  
   https://huggingface.co/collections/stabilityai/stable-audio-3

6. Stability AI. “Stable Audio 2.0.” Official product announcement, April 2024. Not peer-reviewed; included only as historical context for the Stable Audio model family.  
   https://stability.ai/news/stable-audio-2-0
