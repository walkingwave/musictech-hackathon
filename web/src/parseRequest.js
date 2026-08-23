// Turn a plain-English request into a list of tracks to generate.
//
//   "give me a bassline, drums and piano in a bossa nova style"
//     -> [{part:'bass', style:'bossa nova'},
//         {part:'drums', style:'bossa nova'},
//         {part:'piano', style:'bossa nova'}]
//
// This is deliberately pattern matching, not a language model. It runs
// offline and instantly, which matters when the whole point of the project
// is that it works on a laptop with no network. The trade-off is that it
// only understands the vocabulary below — anything it cannot place becomes
// the style, which is the useful failure mode.

const PART_WORDS = {
  bass: ['bassline', 'bass line', 'bass', 'sub', '808'],
  drums: ['drums', 'drum', 'percussion', 'beat', 'kick', 'groove', 'kit'],
  piano: ['piano', 'keys', 'keyboard', 'rhodes', 'wurli', 'organ', 'chords'],
  harmony: ['harmony', 'harmonies', 'choir', 'pad', 'strings', 'backing vocal'],
};

// Phrases meaning "all of them".
const EVERYTHING = ['full band', 'whole band', 'everything', 'full arrangement', 'all of them'];

// Stripped before what remains is treated as the style.
const FILLER = [
  'i want', 'i need', 'i would like', 'give me', 'can you', 'please',
  'add', 'make', 'generate', 'create', 'build', 'me', 'just',
  'a', 'an', 'the', 'some', 'with', 'and', 'plus', 'also', 'for',
  'track', 'tracks', 'part', 'parts', 'backing', 'behind this',
  'in', 'of', 'style', 'styled', 'sounding', 'sound like', 'vibe', 'feel',
  'that is', 'that', 'thats', "that's", 'to', 'it', 'like',
];

const PART_ORDER = ['bass', 'drums', 'piano', 'harmony'];

// Escape a phrase and match it only on word boundaries. Substring removal
// would eat the "a" inside "bossa nova" and leave "boss nov".
const wordRegex = (phrase) =>
  new RegExp(`\\b${phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'g');

export function parseRequest(text) {
  const lower = ` ${text.toLowerCase()} `;

  const wantsAll = EVERYTHING.some((phrase) => lower.includes(phrase));
  const parts = wantsAll
    ? [...PART_ORDER]
    : PART_ORDER.filter((part) =>
        PART_WORDS[part].some((word) => wordRegex(word).test(lower)),
      );

  // Whatever is left after removing the part words and the filler is the
  // style. Longest phrases first, so "bass line" does not leave a stray
  // "line" behind and "in a" is gone before "in".
  const byLength = (a, b) => b.length - a.length;
  let rest = lower;
  for (const phrase of [...EVERYTHING, ...Object.values(PART_WORDS).flat()].sort(byLength)) {
    rest = rest.replace(wordRegex(phrase), ' ');
  }
  for (const phrase of [...FILLER].sort(byLength)) {
    rest = rest.replace(wordRegex(phrase), ' ');
  }

  const style = rest
    .replace(/[,.;!?]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  return { parts, style };
}

// One-line summary of what will happen, so the user can confirm before
// spending a minute of generation on a misread request.
export function describePlan({ parts, style }) {
  if (!parts.length) return 'No instruments recognised — try "bass, drums and piano".';
  const names = parts.join(', ');
  return style ? `${names} — style: ${style}` : names;
}
