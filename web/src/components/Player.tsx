import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type SyntheticEvent } from 'react'
import { ArrowLeft, ListVideo, Loader2, SkipBack, SkipForward } from 'lucide-react'
import { Audio, AudioSkin } from '@videojs/react/audio'
import { usePlayer, Video, VideoPlayer } from '@videojs/react/video'
import '@videojs/react/audio/skin.css'
import { toast } from 'sonner'
import type { Job } from '@/api'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Button as MediaButton } from '@/components/videojs/ui/button'
import { ButtonTooltip } from '@/components/videojs/ui/button-tooltip'
import { langName } from '@/lib/langName'
import { VideoSkin } from '@/components/videojs/video/skin'
import { artifactUrl, downloadUrl, errorMessage, loadSaved, save, slotArtifact } from '@/lib/api'
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
/** Shown while the pointer is over the player or the focus in it. */
const FADE = 'opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100'
/** Seconds ← → go back / on: the Video.js skin's own step. */
const STEP = 10
/** The Video.js skins edge to edge, dark. */
const SKIN = { '--media-border-radius': '0', '--media-border-color': 'transparent', colorScheme: 'dark' } as CSSProperties

/** The page covered by one job's best version (see lib/playback) with its subtitles: watching only.
 * Played by Video.js (its skin, installed in components/videojs: controls, the title, its keys while it has the focus).
 * With a playlist, ⏮ ⏭ in its column's header (P / N) or the column's jobs go to the jobs around it. Esc or ← at the top
 * left closes it, as does Back (the URL holds ?play=). With 'Tự chuyển bài' on, a video ended or failing to play counts
 * down to the next. No bar above the video: the whole playlist is watched in the list's most common language; the
 * button where picture-in-picture was shows or hides the playlist column, whose header holds ⏮ ⏭ and
 * 'Tự chuyển bài'. The subtitles of every language are the video's
 * tracks: on or off (C, remembered) and which one in the skin, as is the audio track of a file that has several.
 * Space plays / pauses, ← → go 10 s back / on, F is fullscreen: the video with its subtitles and the countdown, kept
 * from one video to the next. A job is played from where it was left in that
 * language (see lib/watched), from the start once watched to the end. */
export function Player(props: PlayerProps) {
  return (
    <VideoPlayer title={props.job?.title ?? null}>
      <PlayerPage {...props} />
    </VideoPlayer>
  )
}

type PlayerProps = {
  /** undefined while loading */
  job?: Job
  error?: unknown
  /** the language wanted: the list's most common one */
  lang?: string
  playlist?: Playlist
  onPlay: (id: string) => void
  onClose: () => void
}

function PlayerPage({ job, error, lang, playlist, onPlay, onClose }: PlayerProps) {
  const store = usePlayer()
  // the video and what is drawn over it: its keys are the Video.js skin's while the focus is in it
  const stage = useRef<HTMLDivElement>(null)
  const [settings, setSettings] = useState(() =>
    loadSaved<{ playlist: boolean; auto: boolean; subtitles: boolean }>('player', { playlist: true, auto: true, subtitles: true }))
  const set = (change: Partial<typeof settings>) => {
    const value = { ...settings, ...change }
    setSettings(value)
    save('player', value)
  }
  const column = settings.playlist
  const jobs = playlist?.jobs ?? []
  // the language is kept from the moment the job is known, for the whole playlist: the list loading later must not
  // restart the video. The list's most common one; the job's own when the list is not loaded yet.
  const [kept, keep] = useState<{ lang?: string }>()
  if (job && !kept) keep({ lang: lang ?? job.langs[0] })
  const wanted = kept ? kept.lang : lang
  const best = job && playback(job, wanted)
  const media = useRef<HTMLMediaElement>(null)
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

  // closing at the top left: in the video's skin, else over the corners of the page (an audio's: shown while the pointer
  // is over it or the focus in it, the playlist column shown or hidden at the top right)
  const inSkin = !!best && VIDEO.test(best.source.path)
  const columnLabel = column ? 'Ẩn danh sách phát' : 'Hiện danh sách phát'
  // a video's, where picture-in-picture was
  const columnButton = playlist && (
    <ButtonTooltip side="top" label={columnLabel}>
      <MediaButton aria-label={columnLabel} aria-pressed={column} onClick={() => set({ playlist: !column })}>
        <ListVideo className="size-media-icon" />
      </MediaButton>
    </ButtonTooltip>
  )

  // the playlist column over the video in its fullscreen (the skin's alone), above its controls; beside it else
  const [full, setFull] = useState(false)
  useEffect(() => {
    const sync = () => setFull(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [])
  const overVideo = full && inSkin
  const list = playlist && column && (
    <PlaylistColumn playlist={playlist} current={job?.id} lang={wanted} onPlay={onPlay}
      className={overVideo ? 'absolute top-2 right-2 bottom-16 z-20 rounded-lg border bg-black/80 backdrop-blur' : 'border-l'}>
      <Button size="icon" variant="ghost" className={DARK} title="Video trước (P)" aria-label="Video trước"
        disabled={!previous} onClick={() => previous && onPlay(previous.id)}>
        <SkipBack fill="currentColor" />
      </Button>
      <Button size="icon" variant="ghost" className={DARK}
        title={next ? 'Video kế tiếp (N)' : loading ? 'Đang tải thêm…' : END} aria-label="Video kế tiếp"
        disabled={!next} onClick={() => next && onPlay(next.id)}>
        <SkipForward fill="currentColor" />
      </Button>
      <label className="ms-auto flex items-center gap-2 text-sm text-white/80" title="Hết video thì sang video kế">
        <Switch size="sm" checked={settings.auto} onCheckedChange={(auto) => set({ auto })} />
        Tự chuyển bài
      </label>
    </PlaylistColumn>
  )

  const fullscreen = () => void (document.fullscreenElement ? store.exitFullscreen() : store.requestFullscreen()).catch(() => {})
  const subtitles = () => store.toggleSubtitles()

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
      // Esc in fullscreen leaves fullscreen only, in a menu of the skin closes it only (prevented on the document)
      if (e.key === 'Escape' && !e.defaultPrevented && !document.fullscreenElement) keys.current?.onClose()
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e)) return
      const m = media.current
      const seek = (by: number) => m && (m.currentTime = Math.max(0, Math.min(m.duration || Infinity, m.currentTime + by)))
      const key = e.key.toLowerCase()
      // the skin's own while the focus is in it
      const skin = e.target instanceof Node && !!stage.current?.contains(e.target)
      if (!skin) {
        // not those of a button focused
        if ([' ', 'arrowleft', 'arrowright'].includes(key)) e.preventDefault()
        if (key === ' ' && !e.repeat) void (m?.paused ? m.play().catch(() => {}) : m?.pause())
        if (key === 'arrowleft') seek(-STEP)
        if (key === 'arrowright') seek(STEP)
        if (key === 'f' && !e.repeat) keys.current?.fullscreen()
        if (key === 'c' && !e.repeat) keys.current?.subtitles()
      }
      if (e.repeat) return
      if (key === 'n') keys.current?.step(1)
      if (key === 'p') keys.current?.step(-1)
    }
    // a button focused is clicked by Space let go
    const onKeyUp = (e: KeyboardEvent) => e.key === ' ' && !typing(e)
      && !(e.target instanceof Node && stage.current?.contains(e.target)) && e.preventDefault()
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

  // over the video, fullscreen too: it cannot be played, what comes next
  const over = stopped && (stopped.failed || counting || atEnd) && (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-black/70 p-6 text-center">
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
  )

  return (
    <div role="dialog" aria-modal aria-label={job?.title ?? 'Xem video'}
      className="fixed inset-0 z-50 flex bg-black text-white">
      <div className="group relative flex min-w-0 flex-1 flex-col">
        {!inSkin && (
          <>
            <div className={cn('absolute left-2 top-2 z-30', best && FADE)}><Back skin={false} onClose={onClose} /></div>
            {playlist && (
              <div className={cn('absolute right-2 top-2 z-30', best && FADE)}>
                <Button size="icon" variant="ghost" className={cn(DARK, 'bg-black/60', column && 'bg-white/15')}
                  title={columnLabel} aria-label={columnLabel} aria-pressed={column} onClick={() => set({ playlist: !column })}>
                  <ListVideo />
                </Button>
              </div>
            )}
          </>
        )}
        <div ref={stage} className="relative flex min-h-0 flex-1 items-center justify-center">
          {error ? <p className="text-sm text-red-300">{errorMessage(error)}</p>
            : !job ? <Loader2 className="animate-spin text-white/60" />
              : !best ? <p className="text-sm text-white/70">Việc này chưa có video hoặc audio để xem.</p>
                : (
                  // one skin for the videos watched one after another: fullscreen kept from one to the next
                  <Skin video={inSkin} over={<>{over}{overVideo && list}</>} playlist={columnButton}
                    back={<Back skin onClose={onClose} />}>
                    <Media key={best.source.id} source={best.source}
                      media={(element) => {
                        media.current = element
                      }}
                      subtitles={subtitlesOf(job)} shown={settings.subtitles ? best.subtitles : undefined}
                      onSubtitles={(shown) => shown !== settings.subtitles && set({ subtitles: shown })}
                      start={resumeAt(watchedIn(job.id, wanted))}
                      onProgress={(time, duration, now) => saveWatched(job.id, wanted, time, duration, now)}
                      onStop={(failed) => setStop({ id: best.source.id, failed })}
                      onPlaying={() => stopped && !stopped.failed && setStop(undefined)} />
                  </Skin>
                )}
        </div>
      </div>
      {!overVideo && list}
    </div>
  )
}

/** The jobs of the playlist with something to watch, the one watched marked, how much of each was watched under it.
 * children: its header (⏮ ⏭, Tự chuyển bài). */
function PlaylistColumn({ playlist, current, lang, onPlay, className, children }: {
  playlist: Playlist
  current?: string
  lang?: string
  onPlay: (id: string) => void
  className: string
  children: ReactNode
}) {
  const playing = useRef<HTMLButtonElement>(null)
  const saved = useWatched()
  useEffect(() => {
    playing.current?.scrollIntoView({ block: 'nearest' })
  }, [current])
  return (
    <aside aria-label="Danh sách phát" className={cn('flex w-72 shrink-0 flex-col border-white/10', className)}>
      <div className="flex items-center gap-1 border-b border-white/10 py-2 pe-4 ps-2">{children}</div>
      <ol className="min-h-0 flex-1 overflow-y-auto py-1">
        {playlist.jobs.map((job) => {
          const best = playback(job, lang)
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

/** Leaving the player, at its top left: away from the playlist column, not taken for closing it. skin: in the Video.js
 * skin, styled as its controls. */
function Back({ skin, onClose }: { skin: boolean; onClose: () => void }) {
  const label = 'Thoát trình phát (Esc)'
  return skin ? (
    <ButtonTooltip side="bottom" label={label}>
      <MediaButton aria-label="Thoát trình phát" onClick={onClose}>
        <ArrowLeft className="size-media-icon-sm" />
      </MediaButton>
    </ButtonTooltip>
  ) : (
    <Button size="icon" variant="ghost" className={cn(DARK, 'bg-black/60')} title={label} aria-label="Thoát trình phát"
      onClick={onClose}>
      <ArrowLeft />
    </Button>
  )
}

/** The Video.js skin of a video, drawn over by `over` (fullscreen too); an audio's bar, `over` above it. */
function Skin({ video, over, playlist, back, children }: {
  video: boolean
  over: ReactNode
  /** a video's only, in its controls: */
  playlist?: ReactNode
  back: ReactNode
  children: ReactNode
}) {
  return video ? (
    <VideoSkin className="size-full" style={SKIN} playlist={playlist} back={back}>
      {children}
      {over}
    </VideoSkin>
  ) : (
    <>
      <AudioSkin className="w-full max-w-2xl" style={SKIN}>{children}</AudioSkin>
      {over}
    </>
  )
}

/** The subtitles of the job, one track per language: "src" for the original's. */
function subtitlesOf(job: Job) {
  return ['src', ...job.langs].flatMap((lang) => {
    const artifact = slotArtifact(job, `transcript.${lang}`)
    return artifact ? [{ lang, artifact: artifact.id }] : []
  })
}

/** onStop: the media ended, or (failed) cannot be played: an error, or a video whose picture the browser cannot
 * decode (.mkv, codec). onPlaying: it plays (again). start: the second it is played from. onProgress: where it is
 * as it plays; now: paused or ended. subtitles: its tracks, `shown` the one shown; onSubtitles: they were turned on
 * or off. */
function Media({ source, subtitles, shown, start, media, onStop, onPlaying, onSubtitles, onProgress }: {
  source: Source
  subtitles: { lang: string; artifact: string }[]
  shown?: string
  start?: number
  /** given the element playing */
  media: (element: HTMLMediaElement | null) => void
  onStop: (failed: boolean) => void
  onPlaying: () => void
  onSubtitles: (shown: boolean) => void
  onProgress: (time: number, duration: number, now: boolean) => void
}) {
  const element = useRef<HTMLMediaElement>(null)
  const latest = useRef(onProgress)
  const toggled = useRef(onSubtitles)
  useEffect(() => {
    latest.current = onProgress
    toggled.current = onSubtitles
  })
  // on or off as picked in the skin (C too): remembered for the next video
  useEffect(() => {
    const tracks = element.current?.textTracks
    if (!tracks) return
    const onChange = () => toggled.current([...tracks].some((t) => t.mode === 'showing'))
    tracks.addEventListener('change', onChange)
    return () => tracks.removeEventListener('change', onChange)
  }, [])
  // another language picked while the same file plays: its subtitles shown instead, if any are
  useEffect(() => {
    const tracks = [...element.current?.textTracks ?? []]
    if (!shown || !tracks.some((t) => t.mode === 'showing')) return
    for (const t of tracks) t.mode = t.language === shown ? 'showing' : 'disabled'
  }, [shown])
  // closed or another video: where it was left, the last seconds too
  useEffect(() => () => {
    const m = element.current
    if (m && !m.ended && m.currentTime) latest.current(m.currentTime, m.duration, true)
  }, [])
  const props = {
    src: artifactUrl(source.artifact), autoPlay: true,
    ref: (m: HTMLMediaElement | null) => {
      if (m) element.current = m
      media(m)
    },
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => {
      if (start) e.currentTarget.currentTime = start
    },
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => {
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
  const tracks = subtitles.map(({ lang, artifact }) => (
    <track key={artifact} kind="subtitles" src={`${artifactUrl(artifact)}/vtt`} srcLang={lang}
      label={lang === 'src' ? 'Gốc' : langName(lang)} default={lang === shown} />
  ))
  return (
    <>
      {VIDEO.test(source.path)
        ? <Video {...props} playsInline
          onLoadedMetadata={(e) => {
            props.onLoadedMetadata(e)
            if (!e.currentTarget.videoWidth) onStop(true)
          }}>{tracks}</Video>
        : <Audio {...props}>{tracks}</Audio>}
      {mkvHint(source.path) && <p className="absolute inset-x-0 top-2 z-10 text-center text-xs text-white/60">{mkvHint(source.path)}</p>}
    </>
  )
}
