// Structural edits of a subtitle draft: merge, split and moving words between neighbouring cues.
// Pure functions over the row list; times come from the timed words of the transcript when the spoken line
// still matches them, else are estimated from the share of characters moved.

export type Row = { key: number; start: number; end: number; source: string; text: string }
export type Field = 'text' | 'source'
export type Word = { word: string; start: number; end: number }

const MIN_DURATION = 0.2 // seconds a cue keeps when words are moved out of it
let nextKey = 0
export const newKey = () => ++nextKey

const ms = (seconds: number) => Math.round(seconds * 1000) / 1000
const clamp = (x: number, lo: number, hi: number) => Math.min(Math.max(x, lo), hi)
const join = (a: string, b: string) => [a.trimEnd(), b.trimStart()].filter(Boolean).join(' ')
const tokens = (text: string) => text.split(/\s+/).filter(Boolean)
const EPSILON = 0.001 // .srt times are whole milliseconds

/** The silence between the first `k` spoken words of `row` and the rest, when its spoken line `text` still has
 * as many words as were heard in its time span (an edited line no longer matches the transcript). */
function wordGap(words: Word[] | undefined, row: Row, text: string, k: number) {
  if (!words) return null
  const heard = words.filter((w) => w.start >= row.start - EPSILON && w.end <= row.end + EPSILON)
  if (heard.length !== tokens(text).length || k <= 0 || k >= heard.length) return null
  return { end: heard[k - 1].end, start: heard[k].start }
}

/** How many words of `text` lie before `at`; null when `at` cuts a word in two. */
function wordsBefore(text: string, at: number) {
  const [head, tail] = [tokens(text.slice(0, at)), tokens(text.slice(at))]
  return head.length + tail.length === tokens(text).length ? head.length : null
}

/** The word boundary of `text` closest to `ratio` of its length. */
function boundary(text: string, ratio: number) {
  const target = ratio * text.length
  let best = ratio < 0.5 ? 0 : text.length
  for (const m of text.matchAll(/\s+/g)) {
    if (Math.abs(m.index - target) < Math.abs(best - target)) best = m.index
  }
  return best
}

/** Cue i and the one after it become one. */
export function mergeNext(rows: Row[], i: number): Row[] {
  const [a, b] = [rows[i], rows[i + 1]]
  if (!b) return rows
  const merged = { key: a.key, start: a.start, end: b.end, source: join(a.source, b.source), text: join(a.text, b.text) }
  return [...rows.slice(0, i), merged, ...rows.slice(i + 2)]
}

/** Cue i cut at character `at` of its `field`; the other field is cut at the word boundary in the same proportion.
 * The cut time is where the `spoken` field's words are cut when `words` know it. */
export function split(rows: Row[], i: number, field: Field, at: number, spoken: Field, words?: Word[]): Row[] {
  const row = rows[i]
  const value = row[field]
  if (at <= 0 || at >= value.length || row.end - row.start < 2 * MIN_DURATION) return rows
  const ratio = at / value.length
  const other: Field = field === 'text' ? 'source' : 'text'
  const cut = boundary(row[other], ratio)
  const k = wordsBefore(row[spoken], field === spoken ? at : cut)
  const gap = k === null ? null : wordGap(words, row, row[spoken], k)
  const time = ms(clamp(row.start + ratio * (row.end - row.start), row.start + MIN_DURATION, row.end - MIN_DURATION))
  const head = {
    ...row, end: gap?.end ?? time,
    [field]: value.slice(0, at).trimEnd(), [other]: row[other].slice(0, cut).trimEnd(),
  }
  const tail = {
    key: newKey(), start: gap?.start ?? time, end: row.end,
    [field]: value.slice(at).trimStart(), [other]: row[other].slice(cut).trimStart(),
  } as Row
  return [...rows.slice(0, i), head, tail, ...rows.slice(i + 1)]
}

/** Moves the words of cue i's `field` before `at` to the end of the cue above (`up`) or those after it to the
 * start of the cue below. When `field` holds the spoken words (`retime`), the boundary between the two cues
 * follows: to the silence between the moved words when `words` know it, else by the share of characters moved. */
export function moveWords(rows: Row[], i: number, field: Field, at: number, up: boolean, retime: boolean,
  words?: Word[]): Row[] {
  const j = up ? i - 1 : i + 1
  const [row, neighbour] = [rows[i], rows[j]]
  const value = row[field]
  const moved = (up ? value.slice(0, at) : value.slice(at)).trim()
  if (!neighbour || !moved) return rows
  const rest = (up ? value.slice(at) : value.slice(0, at)).trim()
  const next = [...rows]
  next[i] = { ...row, [field]: rest }
  next[j] = { ...neighbour, [field]: up ? join(neighbour[field], moved) : join(moved, neighbour[field]) }
  const k = retime ? wordsBefore(value, at) : null
  const gap = k === null ? null : wordGap(words, row, value, k)
  if (gap) {
    next[i] = { ...next[i], ...(up ? { start: gap.start } : { end: gap.end }) }
    next[j] = { ...next[j], ...(up ? { end: gap.end } : { start: gap.start }) }
  } else if (retime && value.trim()) {
    const share = moved.length / value.trim().length
    const duration = row.end - row.start
    if (up) {
      const time = ms(clamp(row.start + share * duration, row.start, row.end - MIN_DURATION))
      next[i] = { ...next[i], start: time }
      next[j] = { ...next[j], end: Math.max(next[j].end, time) }
    } else {
      const time = ms(clamp(row.end - share * duration, row.start + MIN_DURATION, row.end))
      next[i] = { ...next[i], end: time }
      next[j] = { ...next[j], start: Math.min(next[j].start, time) }
    }
  }
  return next
}
