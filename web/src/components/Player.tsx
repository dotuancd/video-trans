import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { Captions, CaptionsOff, ChevronLeft, ChevronRight, ListVideo, Loader2, Maximize, X } from 'lucide-react'
import { toast } from 'sonner'
import type { Job } from '@/api'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { artifactUrl, downloadUrl, errorMessage, loadSaved, save, useTranscript } from '@/lib/api'
import type { JobSort } from '@/lib/jobs'
import { cn } from '@/lib/utils'
import { mkvHint, neighbour, placeIn, playback, VIDEO, type Source } from '@/lib/playback'
import { progress, resumeAt, saveWatched, useWatched, watchedIn } from '@/lib/watched'

/** The jobs watched one after another: the job list as shown, kept current. */
export type Playlist = {
  jobs: Job[]
  sort: JobSort
  /** more jobs to load: loaded as the job watched comes near the end of those loaded */
  more: boolean
  loadingMore: boolean
  onMore: () => void
}

/** Loaded once fewer jobs than this are left after the one watched. */
const AHEAD = 5
const END = 'Đã xem hết'
/** Seconds counted down before the next video. */
const COUNTDOWN = 5
const DARK = 'text-white hover:bg-white/15 hover:text-white disabled:opacity-40'

/** The page covered by one job's best version (see lib/playback) with its subtitles on one line: watching only.
 * With a playlist, ◀ ▶ (P / N) or its column go to the jobs around it. Esc or the close button closes it, as does
 * Back (the URL holds ?play=). With 'Tự chuyển bài' on, a video ended or failing to play counts down to the next.
 * Its top bar picks the language of the whole playlist (remembered), the dubbed video or the original (where it was
 * watched), the subtitles on or off (C). Space plays / pauses, ← → go 5 s back / on, F is fullscreen. A job is played
 * from where it was left in that language (see lib/watched), from the start once watched to the end. */
export function Player({ job, error, lang, playlist, onPlay, onClose }: {
  /** undefined while loading */
  job?: Job
  error?: unknown
  /** the language wanted: the list's most common one */
  lang?: string
  playlist?: Playlist
  onPlay: (id: string) => void
  onClose: () => void
}) {
  const root = useRef<HTMLDivElement>(null)
  const [settings, setSettings] = useState(() =>
    loadSaved<{ playlist: boolean; auto: boolean; subtitles: boolean; lang?: string }>('player', { playlist: true, auto: true, subtitles: true }))
  const set = (change: Partial<typeof settings>) => {
    const value = { ...settings, ...change }
    setSettings(value)
    save('player', value)
  }
  const column = settings.playlist
  const jobs = playlist?.jobs ?? []
  const langs = [...new Set([...job?.langs ?? [], ...jobs.flatMap((j) => j.langs)])]
  // the language is kept from the moment the job is known, for the whole playlist: the list loading later must not
  // restart the video. The one last picked wins where the list has it; the job's own when the list is not loaded yet.
  const [kept, keep] = useState<{ lang?: string }>()
  if (job && !kept) keep({ lang: settings.lang && langs.includes(settings.lang) ? settings.lang : lang ?? job.langs[0] })
  const wanted = kept ? kept.lang : lang
  // dub: the dubbed video is wanted over the original
  const [dub, setDub] = useState(true)
  const best = job && playback(job, wanted, dub)
  // both versions to pick from
  const both = !!job && !!best && playback(job, wanted, !dub)?.source.id !== best.source.id
  const media = useRef<HTMLMediaElement>(null)
  // where the job was left for its other version or language: played from there
  const [resume, setResume] = useState<{ job: string; time: number }>()
  if (resume && resume.job !== job?.id) setResume(undefined)
  const remember = () => job && setResume({ job: job.id, time: media.current?.ended ? 0 : media.current?.currentTime ?? 0 })
  const pick = (dubbed: boolean) => {
    if (!best || best.dubbed === dubbed) return
    remember()
    setDub(dubbed)
  }
  // the list as it was when the job watched was last in it: where it stood once filtered out
  const [seen, setSeen] = useState<string[]>([])
  if (job && jobs.some((j) => j.id === job.id) && (seen.length !== jobs.length || seen.some((id, i) => id !== jobs[i].id))) {
    setSeen(jobs.map((j) => j.id))
  }
  const next = job && playlist && neighbour(jobs, job, playlist.sort, wanted, 1, seen)
  const previous = job && playlist && neighbour(jobs, job, playlist.sort, wanted, -1, seen)
  const near = !!job && !!playlist && (!next || placeIn(jobs, job, playlist.sort, seen).index >= jobs.length - AHEAD)
  const loading = !!playlist?.more && !next
  // how the video watched stopped: ended, or could not be played; cancelled: the move to the next one called off
  const [stop, setStop] = useState<{ id: string; failed: boolean; cancelled?: boolean }>()
  const stopped = best && stop?.id === best.source.id ? stop : undefined
  const counting = !!stopped && !stopped.cancelled && settings.auto && !!next
  const atEnd = !!stopped && settings.auto && !next && !loading

  const fullscreen = () => void (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen())
  const subtitles = () => best?.subtitles && set({ subtitles: !settings.subtitles })

  const keys = useRef<{ onClose: () => void; step: (dir: 1 | -1) => void; subtitles: () => void; fullscreen: () => void }>(undefined)
  useEffect(() => {
    keys.current = {
      onClose,
      step: (dir) => {
        const to = dir > 0 ? next : previous
        if (to) onPlay(to.id)
        else if (dir > 0 && playlist && !loading) toast.info(END)
      },
      subtitles,
      fullscreen,
    }
  })
  useEffect(() => {
    // the keys are the page's, typing left alone (the language picker keeps ↑ ↓ only)
    const typing = (e: KeyboardEvent) => e.target instanceof HTMLElement
      && (e.target.isContentEditable || ['INPUT', 'TEXTAREA'].includes(e.target.tagName))
    const onKey = (e: KeyboardEvent) => {
      // Esc in fullscreen leaves fullscreen only
      if (e.key === 'Escape' && !document.fullscreenElement) keys.current?.onClose()
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e)) return
      const m = media.current
      const seek = (by: number) => m && (m.currentTime = Math.max(0, Math.min(m.duration || Infinity, m.currentTime + by)))
      const key = e.key.toLowerCase()
      // not those of a button or the media's own controls focused
      if ([' ', 'arrowleft', 'arrowright'].includes(key)) e.preventDefault()
      if (key === ' ' && !e.repeat) void (m?.paused ? m.play().catch(() => {}) : m?.pause())
      if (key === 'arrowleft') seek(-5)
      if (key === 'arrowright') seek(5)
      if (e.repeat) return
      if (key === 'n') keys.current?.step(1)
      if (key === 'p') keys.current?.step(-1)
      if (key === 'c') keys.current?.subtitles()
      if (key === 'f') keys.current?.fullscreen()
    }
    // a button focused is clicked by Space let go
    const onKeyUp = (e: KeyboardEvent) => e.key === ' ' && !typing(e) && e.preventDefault()
    addEventListener('keydown', onKey)
    addEventListener('keyup', onKeyUp)
    return () => {
      removeEventListener('keydown', onKey)
      removeEventListener('keyup', onKeyUp)
    }
  }, [])

  const loadMore = useRef(playlist?.onMore)
  useEffect(() => {
    loadMore.current = playlist?.onMore
  })
  const canLoad = near && !!playlist?.more && !playlist.loadingMore
  useEffect(() => {
    if (canLoad) loadMore.current?.()
  }, [canLoad])

  return (
    <div ref={root} role="dialog" aria-modal aria-label={job?.title ?? 'Xem video'}
      className="fixed inset-0 z-50 flex bg-black text-white">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 px-4 py-2">
          {playlist && (
            <>
              <Button size="icon" variant="ghost" className={DARK} title="Video trước (P)" disabled={!previous}
                onClick={() => previous && onPlay(previous.id)}>
                <ChevronLeft />
              </Button>
              <Button size="icon" variant="ghost" className={DARK} disabled={!next}
                title={next ? 'Video kế tiếp (N)' : loading ? 'Đang tải thêm…' : END}
                onClick={() => next && onPlay(next.id)}>
                <ChevronRight />
              </Button>
            </>
          )}
          <h2 className="min-w-0 flex-1 truncate font-medium">{job?.title}</h2>
          {langs.length > 0 && (
            <select aria-label="Ngôn ngữ" title="Ngôn ngữ xem cho cả danh sách" value={wanted ?? ''}
              className="h-8 rounded-md border border-white/20 bg-black px-2 text-sm uppercase text-white"
              onChange={(e) => {
                remember()
                keep({ lang: e.target.value })
                set({ lang: e.target.value })
                // the keys go back to the player
                e.target.blur()
              }}>
              {wanted === undefined && <option value="" />}
              {langs.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          )}
          {best && (
            <div role="group" aria-label="Bản phát" className="flex rounded-md border border-white/20 p-0.5">
              {[true, false].map((dubbed) => {
                const on = best.dubbed === dubbed
                return (
                  <Button key={String(dubbed)} size="sm" variant="ghost" aria-pressed={on}
                    className={cn(DARK, 'h-7', on && 'bg-white/15')} disabled={!on && !both}
                    title={on || both ? undefined : dubbed ? 'Chưa có bản lồng tiếng' : 'Không có bản gốc'}
                    onClick={() => pick(dubbed)}>
                    {dubbed ? 'Lồng tiếng' : 'Gốc'}
                  </Button>
                )
              })}
            </div>
          )}
          <Button size="icon" variant="ghost" className={cn(DARK, best?.subtitles && settings.subtitles && 'bg-white/15')}
            disabled={!best?.subtitles} title={!best?.subtitles ? 'Không có phụ đề' : settings.subtitles ? 'Tắt phụ đề (C)' : 'Bật phụ đề (C)'}
            onClick={subtitles}>
            {settings.subtitles ? <Captions /> : <CaptionsOff />}
          </Button>
          {playlist && (
            <label className="flex items-center gap-2 px-2 text-sm text-white/80" title="Hết video thì sang video kế">
              <Switch size="sm" checked={settings.auto} onCheckedChange={(auto) => set({ auto })} />
              Tự chuyển bài
            </label>
          )}
          {playlist && (
            <Button size="icon" variant="ghost" className={cn(DARK, column && 'bg-white/15')}
              title={column ? 'Ẩn danh sách phát' : 'Hiện danh sách phát'} onClick={() => set({ playlist: !column })}>
              <ListVideo />
            </Button>
          )}
          <Button size="icon" variant="ghost" className={DARK} title="Toàn màn hình (F)" onClick={fullscreen}>
            <Maximize />
          </Button>
          <Button size="icon" variant="ghost" className={DARK} title="Đóng (Esc)" onClick={onClose}>
            <X />
          </Button>
        </div>
        <div className="relative flex min-h-0 flex-1 items-center justify-center">
          {error ? <p className="text-sm text-red-300">{errorMessage(error)}</p>
            : !job ? <Loader2 className="animate-spin text-white/60" />
              : !best ? <p className="text-sm text-white/70">Việc này chưa có video hoặc audio để xem.</p>
                : <Media key={best.source.id} jobId={job.id} source={best.source}
                  media={(element) => {
                    media.current = element
                  }}
                  subtitles={settings.subtitles ? best.subtitles : undefined}
                  start={resume?.job === job.id ? resume.time : resumeAt(watchedIn(job.id, wanted))}
                  onProgress={(time, duration, now) => saveWatched(job.id, wanted, time, duration, now)}
                  onStop={(failed) => setStop({ id: best.source.id, failed })}
                  onPlaying={() => stopped && !stopped.failed && setStop(undefined)} />}
          {stopped && (stopped.failed || counting || atEnd) && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-black/70 p-6 text-center">
              {stopped.failed && (
                <p>
                  Không phát được — <a href={downloadUrl(best!.source.artifact)} download
                    className="underline underline-offset-4 hover:text-white/80">Mở bằng trình phát</a>
                </p>
              )}
              {counting ? (
                <NextUp key={next!.id} next={next!} onPlay={() => onPlay(next!.id)}
                  onCancel={() => setStop({ ...stopped, cancelled: true })} />
              ) : atEnd && <p className="text-sm text-white/60">{END}</p>}
            </div>
          )}
        </div>
      </div>
      {playlist && column && <PlaylistColumn playlist={playlist} current={job?.id} lang={wanted} dub={dub} onPlay={onPlay} />}
    </div>
  )
}

/** The jobs of the playlist with something to watch, the one watched marked, how much of each was watched under it. */
function PlaylistColumn({ playlist, current, lang, dub, onPlay }: {
  playlist: Playlist
  current?: string
  lang?: string
  dub: boolean
  onPlay: (id: string) => void
}) {
  const playing = useRef<HTMLButtonElement>(null)
  const saved = useWatched()
  useEffect(() => {
    playing.current?.scrollIntoView({ block: 'nearest' })
  }, [current])
  return (
    <aside aria-label="Danh sách phát" className="flex w-72 shrink-0 flex-col border-l border-white/10">
      <ol className="min-h-0 flex-1 overflow-y-auto py-1">
        {playlist.jobs.map((job) => {
          const best = playback(job, lang, dub)
          if (!best) return null
          const on = job.id === current
          const watched = watchedIn(job.id, lang, saved)
          const share = watched && progress(watched)
          return (
            <li key={job.id}>
              <button type="button" ref={on ? playing : undefined} aria-current={on || undefined}
                onClick={() => onPlay(job.id)}
                className={cn('grid w-full gap-0.5 px-4 py-2 text-left text-sm hover:bg-white/10', on && 'bg-white/15')}>
                <span className={cn('line-clamp-2', on && 'font-medium')}>{job.title}</span>
                <span className="text-xs text-white/50">{best.dubbed ? 'Lồng tiếng' : 'Gốc'}</span>
                {share !== undefined && (
                  <span className="mt-1 h-0.5 overflow-hidden rounded-full bg-white/15" title={`Đã xem ${Math.round(share * 100)}%`}>
                    <span className="block h-full bg-red-500" style={{ width: `${share * 100}%` }} />
                  </span>
                )}
              </button>
            </li>
          )
        })}
        <li className="flex justify-center px-4 py-3 text-xs text-white/50">
          {playlist.more ? (
            <Button size="sm" variant="ghost" className={DARK} disabled={playlist.loadingMore} onClick={playlist.onMore}>
              {playlist.loadingMore && <Loader2 className="animate-spin" />} Tải thêm
            </Button>
          ) : 'Hết danh sách'}
        </li>
      </ol>
    </aside>
  )
}

/** The countdown to the next video, played when it reaches 0. */
function NextUp({ next, onPlay, onCancel }: { next: Job; onPlay: () => void; onCancel: () => void }) {
  const [left, setLeft] = useState(COUNTDOWN)
  const play = useRef(onPlay)
  useEffect(() => {
    play.current = onPlay
  })
  useEffect(() => {
    if (left <= 0) return play.current()
    const timer = setTimeout(() => setLeft(left - 1), 1000)
    return () => clearTimeout(timer)
  }, [left])
  return (
    <div className="grid max-w-md justify-items-center gap-3">
      <p className="text-sm text-white/60">Tiếp theo sau {left} giây</p>
      <p className="line-clamp-2 text-lg font-medium">{next.title}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onPlay}>Phát ngay</Button>
        <Button size="sm" variant="ghost" className={DARK} onClick={onCancel}>Huỷ</Button>
      </div>
    </div>
  )
}

/** onStop: the media ended, or (failed) cannot be played: an error, or a video whose picture the browser cannot
 * decode (.mkv, codec). onPlaying: it plays (again). start: the second it is played from. onProgress: where it is
 * as it plays; now: paused or ended. */
function Media({ jobId, source, subtitles, start, media, onStop, onPlaying, onProgress }: {
  jobId: string
  source: Source
  subtitles?: string
  start?: number
  /** given the element playing */
  media: (element: HTMLMediaElement | null) => void
  onStop: (failed: boolean) => void
  onPlaying: () => void
  onProgress: (time: number, duration: number, now: boolean) => void
}) {
  const transcript = useTranscript(jobId, subtitles)
  const [time, setTime] = useState(0)
  const cue = transcript.data?.cues.find((c) => c.start <= time && time < c.end)
  const element = useRef<HTMLMediaElement>(null)
  const latest = useRef(onProgress)
  useEffect(() => {
    latest.current = onProgress
  })
  // closed or another video: where it was left, the last seconds too
  useEffect(() => () => {
    const m = element.current
    if (m && !m.ended && m.currentTime) latest.current(m.currentTime, m.duration, true)
  }, [])
  const props = {
    src: artifactUrl(source.artifact), controls: true, autoPlay: true,
    ref: (m: HTMLMediaElement | null) => {
      if (m) element.current = m
      media(m)
    },
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => {
      if (start) e.currentTarget.currentTime = start
    },
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => {
      setTime(e.currentTarget.currentTime)
      onProgress(e.currentTarget.currentTime, e.currentTarget.duration, false)
    },
    // paused at the end too: ended is saved as watched to the end
    onPause: (e: SyntheticEvent<HTMLMediaElement>) => !e.currentTarget.ended
      && onProgress(e.currentTarget.currentTime, e.currentTarget.duration, true),
    onEnded: (e: SyntheticEvent<HTMLMediaElement>) => {
      onProgress(e.currentTarget.duration, e.currentTarget.duration, true)
      onStop(false)
    },
    onError: () => onStop(true),
    onPlaying,
  }
  return (
    <>
      {VIDEO.test(source.path)
        // fullscreen is the whole player's (the subtitles are drawn over the video, not by it)
        ? <video {...props} controlsList="nofullscreen"
          onLoadedMetadata={(e) => {
            props.onLoadedMetadata(e)
            if (!e.currentTarget.videoWidth) onStop(true)
          }} className="size-full object-contain" />
        : <audio {...props} className="w-full max-w-2xl" />}
      {cue && (
        <div className="pointer-events-none absolute inset-x-0 bottom-16 flex justify-center px-6">
          <span className="max-w-4xl rounded bg-black/75 px-3 py-1 text-center text-xl sm:text-2xl">{cue.text.replace(/\s*\n\s*/g, ' ')}</span>
        </div>
      )}
      {mkvHint(source.path) && <p className="absolute top-2 text-xs text-white/60">{mkvHint(source.path)}</p>}
    </>
  )
}
