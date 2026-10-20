import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { ChevronLeft, ChevronRight, ListVideo, Loader2, Maximize, X } from 'lucide-react'
import { toast } from 'sonner'
import type { Job } from '@/api'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { artifactUrl, downloadUrl, errorMessage, loadSaved, save, useTranscript } from '@/lib/api'
import type { JobSort } from '@/lib/jobs'
import { cn } from '@/lib/utils'
import { mkvHint, neighbour, placeIn, playback, VIDEO, type Source } from '@/lib/playback'

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
 * Back (the URL holds ?play=). With 'Tự chuyển bài' on, a video ended or failing to play counts down to the next. */
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
  // the language is kept from the moment the job is known, for the whole playlist: the list loading later must not
  // restart the video
  const [kept, keep] = useState<{ lang?: string }>()
  if (job && !kept) keep({ lang })
  const wanted = kept ? kept.lang : lang
  const best = job && playback(job, wanted)

  const [settings, setSettings] = useState(() => loadSaved('player', { playlist: true, auto: true }))
  const set = (change: Partial<typeof settings>) => {
    const value = { ...settings, ...change }
    setSettings(value)
    save('player', value)
  }
  const column = settings.playlist
  const jobs = playlist?.jobs ?? []
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

  const keys = useRef<{ onClose: () => void; step: (dir: 1 | -1) => void }>(undefined)
  useEffect(() => {
    keys.current = {
      onClose,
      step: (dir) => {
        const to = dir > 0 ? next : previous
        if (to) onPlay(to.id)
        else if (dir > 0 && playlist && !loading) toast.info(END)
      },
    }
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Esc in fullscreen leaves fullscreen only
      if (e.key === 'Escape' && !document.fullscreenElement) keys.current?.onClose()
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return
      if (e.key === 'n' || e.key === 'N') keys.current?.step(1)
      if (e.key === 'p' || e.key === 'P') keys.current?.step(-1)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
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
          <Button size="icon" variant="ghost" className={DARK} title="Toàn màn hình"
            onClick={() => void root.current?.requestFullscreen()}>
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
                : <Media key={best.source.id} jobId={job.id} source={best.source} subtitles={best.subtitles}
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
      {playlist && column && <PlaylistColumn playlist={playlist} current={job?.id} lang={wanted} onPlay={onPlay} />}
    </div>
  )
}

/** The jobs of the playlist with something to watch, the one watched marked. */
function PlaylistColumn({ playlist, current, lang, onPlay }: {
  playlist: Playlist
  current?: string
  lang?: string
  onPlay: (id: string) => void
}) {
  const playing = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    playing.current?.scrollIntoView({ block: 'nearest' })
  }, [current])
  return (
    <aside aria-label="Danh sách phát" className="flex w-72 shrink-0 flex-col border-l border-white/10">
      <ol className="min-h-0 flex-1 overflow-y-auto py-1">
        {playlist.jobs.map((job) => {
          const best = playback(job, lang)
          if (!best) return null
          const on = job.id === current
          return (
            <li key={job.id}>
              <button type="button" ref={on ? playing : undefined} aria-current={on || undefined}
                onClick={() => onPlay(job.id)}
                className={cn('grid w-full gap-0.5 px-4 py-2 text-left text-sm hover:bg-white/10', on && 'bg-white/15')}>
                <span className={cn('line-clamp-2', on && 'font-medium')}>{job.title}</span>
                <span className="text-xs text-white/50">{best.dubbed ? 'Lồng tiếng' : 'Gốc'}</span>
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
 * decode (.mkv, codec). onPlaying: it plays (again). */
function Media({ jobId, source, subtitles, onStop, onPlaying }: {
  jobId: string
  source: Source
  subtitles?: string
  onStop: (failed: boolean) => void
  onPlaying: () => void
}) {
  const transcript = useTranscript(jobId, subtitles)
  const [time, setTime] = useState(0)
  const cue = transcript.data?.cues.find((c) => c.start <= time && time < c.end)
  const props = {
    src: artifactUrl(source.artifact), controls: true, autoPlay: true,
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => setTime(e.currentTarget.currentTime),
    onEnded: () => onStop(false),
    onError: () => onStop(true),
    onPlaying,
  }
  return (
    <>
      {VIDEO.test(source.path)
        // fullscreen is the whole player's (the subtitles are drawn over the video, not by it)
        ? <video {...props} controlsList="nofullscreen"
          onLoadedMetadata={(e) => e.currentTarget.videoWidth || onStop(true)} className="size-full object-contain" />
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
