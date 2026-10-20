// What of a job can be watched, shared by the detail sheet and the full-page player.
import type { Job, LiveAudio } from '@/api'
import { slotArtifact } from '@/lib/api'

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

/** The subtitles to show: a language code, "src" for the original's, or none. */
export type Playback = { source: Source; subtitles?: string }

/** The best way to watch the job in language `lang`: dubbed > original with the translated subtitles > original with
 * its own subtitles. A job without `lang`, or with nothing of it yet, is watched in its other languages the same way.
 * Undefined when there is nothing to watch yet. */
export function playback(job: Job, lang: string | undefined): Playback | undefined {
  const langs = [...new Set([lang, ...job.langs].filter((l) => !!l && job.langs.includes(l)))] as string[]
  const has = (l: string) => !!slotArtifact(job, `transcript.${l}`)
  const { original } = mediaOf(job, undefined)
  for (const l of langs) {
    const { out } = mediaOf(job, l)
    if (out) return { source: out, subtitles: has(l) ? l : undefined }
    if (original && has(l)) return { source: original, subtitles: l }
  }
  return original && { source: original, subtitles: has('src') ? 'src' : undefined }
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
