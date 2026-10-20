// What of a job can be watched, shared by the detail sheet and the full-page player.
import type { Job, LiveAudio } from '@/api'
import { slotArtifact } from '@/lib/api'
import { compareJobs, type JobSort } from '@/lib/jobs'

export const VIDEO = /\.(mp4|mov|m4v|webm|mkv)$/i
/** Shown under a file the browser may not play. */
export const mkvHint = (path: string) =>
  /\.mkv$/i.test(path) ? 'Trình duyệt có thể không phát được .mkv — mở bằng trình phát video.' : undefined

/** Something to play. live: the original with the dub made so far laid over it (see lib/liveDub). */
export type Source = { label: string; id: string; artifact: string; path: string; live?: { taskId: string; index: LiveAudio } }

/** The finished files to play for language `target`: the dubbed video, the original, the dub voice alone. */
export function mediaOf(job: Job, target: string | undefined) {
  const out = target ? slotArtifact(job, `media.out.${target}`) : undefined
  const original = slotArtifact(job, 'media.video')
  const voice = target ? slotArtifact(job, `audio.dub.${target}`) : undefined
  return {
    out: out && { label: 'Bản lồng tiếng', id: out.id, artifact: out.id, path: out.path } satisfies Source,
    original: original && { label: 'Bản gốc', id: original.id, artifact: original.id, path: original.path } satisfies Source,
    voice: voice && { label: 'Giọng đọc', id: voice.id, artifact: voice.id, path: voice.path } satisfies Source,
  }
}

/** How to watch a job. subtitles: a language code, "src" for the original's, or none. dubbed: source is the dubbed
 * video (else the original). */
export type Playback = { source: Source; subtitles?: string; dubbed: boolean }

/** The best way to watch the job in language `lang`: dubbed > original with the translated subtitles > original with
 * its own subtitles. A job without `lang`, or with nothing of it yet, is watched in its other languages the same way.
 * Undefined when there is nothing to watch yet. */
export function playback(job: Job, lang: string | undefined): Playback | undefined {
  const langs = [...new Set([lang, ...job.langs].filter((l) => !!l && job.langs.includes(l)))] as string[]
  const has = (l: string) => !!slotArtifact(job, `transcript.${l}`)
  const { original } = mediaOf(job, undefined)
  for (const l of langs) {
    const { out } = mediaOf(job, l)
    if (out) return { source: out, subtitles: has(l) ? l : undefined, dubbed: true }
    if (original && has(l)) return { source: original, subtitles: l, dubbed: false }
  }
  return original && { source: original, subtitles: has('src') ? 'src' : undefined, dubbed: false }
}

/** Where `current` stands in the list sorted by `sort`: its index, or (gone from the list: filtered out) the index
 * of the first job after it, between = true. That is the first of those after it in `seen` (the list when it was
 * last in it) still there, else where it sorts now. */
export function placeIn(jobs: Job[], current: Job, sort: JobSort, seen: string[] = []) {
  const at = jobs.findIndex((j) => j.id === current.id)
  if (at >= 0) return { index: at, between: false }
  const ids = jobs.map((j) => j.id)
  const from = seen.indexOf(current.id)
  for (const id of from < 0 ? [] : seen.slice(from + 1)) {
    const index = ids.indexOf(id)
    if (index >= 0) return { index, between: true }
  }
  const after = jobs.findIndex((j) => compareJobs(j, current, sort) > 0)
  return { index: after < 0 ? jobs.length : after, between: true }
}

/** The job to watch after (dir 1) or before (-1) `current` in the latest list, skipping those with nothing to watch
 * in `lang`; undefined at the end of the jobs loaded. */
export function neighbour(jobs: Job[], current: Job, sort: JobSort, lang: string | undefined, dir: 1 | -1,
  seen?: string[]) {
  const { index, between } = placeIn(jobs, current, sort, seen)
  // between: index is already the first job after it
  const first = between ? (dir > 0 ? index : index - 1) : index + dir
  for (let i = first; i >= 0 && i < jobs.length; i += dir) {
    if (playback(jobs[i], lang)) return jobs[i]
  }
}

/** The target language most jobs of the list have; the first seen wins a tie. */
export function commonLang(jobs: Job[]): string | undefined {
  const counts = new Map<string, number>()
  for (const job of jobs) for (const l of job.langs) counts.set(l, (counts.get(l) ?? 0) + 1)
  let best: string | undefined
  for (const [l, n] of counts) if (best === undefined || n > counts.get(best)!) best = l
  return best
}

export const escapeVtt = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
