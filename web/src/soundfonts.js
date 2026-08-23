// Prompt -> real instrument.
//
// The AI one-shot pipeline can make *a* sound from any prompt, but a small
// audio model is unreliable at making a cello actually sound like a cello.
// For every instrument that has a name, there is a better source: General
// MIDI soundfonts — real multisampled recordings, one sample per note
// across the whole keyboard, publicly hosted.
//
// So the sampler first tries to read an instrument out of the prompt
// ("bowed cello, warm and woody" -> cello) and loads the matching
// soundfont. Only prompts that name no known instrument fall through to
// generation, which is exactly the case generation is for: sounds that do
// not exist in any library.

const CDN_BASES = [
  // MusyngKite first — noticeably better recordings; FluidR3 as fallback.
  'https://gleitz.github.io/midi-js-soundfonts/MusyngKite',
  'https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM',
];

// Catalog of GM instruments the matcher knows, each with the words that
// should map to it. Multi-word terms score higher than single words, so
// "electric piano" beats "piano" and "synth bass" beats "bass".
const CATALOG = [
  { gm: 'acoustic_grand_piano', terms: ['grand piano', 'acoustic piano', 'piano', 'upright piano', 'keys'] },
  { gm: 'electric_piano_1', terms: ['electric piano', 'rhodes', 'wurlitzer', 'tine', 'epiano', 'e-piano'] },
  { gm: 'harpsichord', terms: ['harpsichord'] },
  { gm: 'clavinet', terms: ['clavinet', 'clav'] },
  { gm: 'celesta', terms: ['celesta', 'celeste'] },
  { gm: 'glockenspiel', terms: ['glockenspiel', 'glock'] },
  { gm: 'music_box', terms: ['music box', 'musicbox'] },
  { gm: 'vibraphone', terms: ['vibraphone', 'vibes'] },
  { gm: 'marimba', terms: ['marimba'] },
  { gm: 'xylophone', terms: ['xylophone'] },
  { gm: 'tubular_bells', terms: ['tubular bells', 'church bell', 'bells', 'bell'] },
  { gm: 'dulcimer', terms: ['dulcimer', 'hammered dulcimer', 'cimbalom'] },
  { gm: 'drawbar_organ', terms: ['drawbar organ', 'hammond', 'electric organ', 'organ', 'b3'] },
  { gm: 'church_organ', terms: ['church organ', 'pipe organ', 'cathedral organ'] },
  { gm: 'accordion', terms: ['accordion', 'squeezebox'] },
  { gm: 'harmonica', terms: ['harmonica', 'blues harp'] },
  { gm: 'acoustic_guitar_nylon', terms: ['nylon guitar', 'nylon-string', 'nylon string', 'classical guitar', 'spanish guitar', 'flamenco guitar'] },
  { gm: 'acoustic_guitar_steel', terms: ['steel guitar', 'steel-string', 'acoustic guitar', 'folk guitar', 'fingerpicked guitar', 'guitar'] },
  { gm: 'electric_guitar_jazz', terms: ['jazz guitar', 'hollowbody', 'archtop'] },
  { gm: 'electric_guitar_clean', terms: ['clean electric guitar', 'electric guitar', 'strat', 'telecaster'] },
  { gm: 'electric_guitar_muted', terms: ['muted guitar', 'palm muted'] },
  { gm: 'overdriven_guitar', terms: ['overdriven guitar', 'overdrive guitar', 'crunch guitar'] },
  { gm: 'distortion_guitar', terms: ['distorted guitar', 'distortion guitar', 'metal guitar'] },
  { gm: 'acoustic_bass', terms: ['upright bass', 'double bass', 'acoustic bass', 'contrabass'] },
  { gm: 'electric_bass_finger', terms: ['electric bass', 'fingered bass', 'bass guitar', 'p-bass'] },
  { gm: 'electric_bass_pick', terms: ['picked bass', 'pick bass'] },
  { gm: 'fretless_bass', terms: ['fretless bass', 'fretless'] },
  { gm: 'slap_bass_1', terms: ['slap bass'] },
  { gm: 'synth_bass_1', terms: ['synth bass', 'analog bass', 'sub bass', '808 bass', 'moog bass', 'bass synth', 'bass'] },
  { gm: 'violin', terms: ['violin', 'fiddle'] },
  { gm: 'viola', terms: ['viola'] },
  { gm: 'cello', terms: ['cello', 'bowed cello', 'violoncello'] },
  { gm: 'orchestral_harp', terms: ['harp', 'concert harp'] },
  { gm: 'timpani', terms: ['timpani', 'kettle drum'] },
  { gm: 'string_ensemble_1', terms: ['string ensemble', 'strings', 'string section', 'orchestra strings'] },
  { gm: 'synth_strings_1', terms: ['synth strings'] },
  { gm: 'choir_aahs', terms: ['choir', 'voice', 'vocal', 'aah', 'aahs', 'singing', 'singer', 'soprano', 'alto'] },
  { gm: 'voice_oohs', terms: ['oohs', 'ooh'] },
  { gm: 'trumpet', terms: ['trumpet', 'cornet'] },
  { gm: 'trombone', terms: ['trombone'] },
  { gm: 'tuba', terms: ['tuba', 'sousaphone'] },
  { gm: 'muted_trumpet', terms: ['muted trumpet', 'harmon mute'] },
  { gm: 'french_horn', terms: ['french horn', 'horn'] },
  { gm: 'brass_section', terms: ['brass section', 'brass', 'horns', 'horn section'] },
  { gm: 'soprano_sax', terms: ['soprano sax', 'soprano saxophone'] },
  { gm: 'alto_sax', terms: ['alto sax', 'alto saxophone', 'sax', 'saxophone'] },
  { gm: 'tenor_sax', terms: ['tenor sax', 'tenor saxophone'] },
  { gm: 'baritone_sax', terms: ['baritone sax', 'bari sax'] },
  { gm: 'oboe', terms: ['oboe'] },
  { gm: 'english_horn', terms: ['english horn', 'cor anglais'] },
  { gm: 'bassoon', terms: ['bassoon'] },
  { gm: 'clarinet', terms: ['clarinet'] },
  { gm: 'piccolo', terms: ['piccolo'] },
  { gm: 'flute', terms: ['flute', 'concert flute'] },
  { gm: 'recorder', terms: ['recorder'] },
  { gm: 'pan_flute', terms: ['pan flute', 'panpipe', 'pan pipes'] },
  { gm: 'shakuhachi', terms: ['shakuhachi'] },
  { gm: 'whistle', terms: ['whistle', 'whistling'] },
  { gm: 'ocarina', terms: ['ocarina'] },
  { gm: 'lead_1_square', terms: ['square lead', 'square wave', 'chiptune', '8-bit', '8bit'] },
  { gm: 'lead_2_sawtooth', terms: ['saw lead', 'sawtooth', 'analog lead', 'synth lead', 'analog synthesizer lead', 'lead synth'] },
  { gm: 'pad_2_warm', terms: ['warm pad', 'synth pad', 'pad', 'ambient pad'] },
  { gm: 'pad_8_sweep', terms: ['sweep pad'] },
  { gm: 'sitar', terms: ['sitar'] },
  { gm: 'banjo', terms: ['banjo'] },
  { gm: 'shamisen', terms: ['shamisen'] },
  { gm: 'koto', terms: ['koto'] },
  { gm: 'kalimba', terms: ['kalimba', 'thumb piano', 'mbira'] },
  { gm: 'bagpipe', terms: ['bagpipe', 'bagpipes'] },
  { gm: 'shanai', terms: ['shanai', 'shehnai'] },
  { gm: 'steel_drums', terms: ['steel drum', 'steel drums', 'steel pan', 'steelpan'] },
  { gm: 'woodblock', terms: ['woodblock', 'wood block'] },
  { gm: 'taiko_drum', terms: ['taiko'] },
];

/**
 * The GM instrument a prompt describes, or null if it names none.
 *
 * Longest matched term wins, so "electric piano, glassy tine" resolves to
 * electric_piano_1 rather than acoustic_grand_piano even though "piano"
 * also matches. Ties break toward the catalog's order.
 */
export function matchPrompt(prompt) {
  const text = ` ${(prompt || '').toLowerCase().replace(/[^a-z0-9-]+/g, ' ')} `;
  let best = null;
  let bestLen = 0;
  for (const entry of CATALOG) {
    for (const term of entry.terms) {
      if (term.length > bestLen && text.includes(` ${term} `)) {
        best = entry.gm;
        bestLen = term.length;
      }
    }
  }
  return best;
}

const NOTE_OFFSETS = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

// "Ab4" / "C#3" / "A0" -> MIDI note number. Gleitz keys use flats.
function nameToMidi(name) {
  const m = /^([A-G])(b|#)?(-?\d+)$/.exec(name);
  if (!m) return null;
  const accidental = m[2] === 'b' ? -1 : m[2] === '#' ? 1 : 0;
  return NOTE_OFFSETS[m[1]] + accidental + (Number(m[3]) + 1) * 12;
}

function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/**
 * Load a GM soundfont: every note of the instrument, decoded and keyed by
 * MIDI pitch. One fetch per instrument (the gleitz -mp3.js bundle), then
 * cached by the browser like any static asset.
 */
export async function loadSoundfont(gmName, ctx) {
  let lastError = null;
  for (const base of CDN_BASES) {
    try {
      const response = await fetch(`${base}/${gmName}-mp3.js`);
      if (!response.ok) throw new Error(`${response.status} fetching ${gmName}`);
      const text = await response.text();

      // The file is JS assigning an object literal:
      //   MIDI.Soundfont.name = { "A0": "data:audio/mp3;base64,...", ... }
      // Slice the literal out rather than eval-ing the script. The first
      // lines also contain `{}` (guard assignments), so anchor on the
      // instrument assignment; and the literal ends with a trailing comma,
      // which JSON forbids, so strip it.
      const assign = /MIDI\.Soundfont\.\w+\s*=\s*\{/.exec(text);
      const end = text.lastIndexOf('}');
      if (!assign || end < 0) throw new Error('unexpected soundfont format');
      const literal = text
        .slice(assign.index + assign[0].length - 1, end + 1)
        .replace(/,\s*}$/, '}');
      const table = JSON.parse(literal);

      const buffers = new Map();
      await Promise.all(
        Object.entries(table).map(async ([note, dataUri]) => {
          const pitch = nameToMidi(note);
          if (pitch == null) return;
          const b64 = dataUri.slice(dataUri.indexOf(',') + 1);
          try {
            const buffer = await ctx.decodeAudioData(base64ToBytes(b64).buffer);
            buffers.set(pitch, buffer);
          } catch {
            // One undecodable note should not sink the other 87.
          }
        }),
      );

      if (!buffers.size) throw new Error('no notes decoded');
      return buffers;
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`could not load soundfont ${gmName}: ${lastError?.message}`);
}
