import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Copy, FolderSearch, Loader2, Play, RotateCcw, ScrollText, Square, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { cancelJob, deleteJob, reveal, runStep, type Job, type Step, type SubtitlesSaved } from '@/api'
import { StepParams, SwitchField, type PreviewContext } from '@/components/StepParams'
import { STATUS_STYLE } from '@/components/JobList'
import { SubtitleEditor, useSubtitleDraft, type SubtitleDraft } from '@/components/SubtitleEditor'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { errorMessage, fileName, mediaUrl, STATUS_LABELS, STEP_LABELS, useStepLog, type StepOptions } from '@/lib/api'

const VIDEO = /\.(mp4|mov|m4v|webm|mkv)$/i

export function JobDetail({ job, onClose }: { job?: Job; onClose: () => void }) {
  return (
    <Sheet open={!!job} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        className="gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-3xl"
        // toasts (e.g. "Lồng tiếng lại" after saving subtitles) live outside the sheet: clicking them must not close it
        onInteractOutside={(e) => (e.target as Element | null)?.closest('[data-sonner-toaster]') && e.preventDefault()}
      >
        {job && <JobBody job={job} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  )
}

function JobBody({ job, onClose }: { job: Job; onClose: () => void }) {
  const [media, setMedia] = useState<HTMLMediaElement | null>(null)
  const [playing, setPlaying] = useState(-1) // cue under the playhead
  const active = job.status === 'running' || job.status === 'queued'
  const step = (name: Step['name']) => job.steps.find((s) => s.name === name)
  // a step's output may be on disk from an earlier run even while the step is stale or failed
  const output = (name: Step['name']) => {
    const s = step(name)
    return s?.exists && s.status !== 'running' ? s.output : undefined
  }
  const viSrt = job.start === 'vi_srt' ? job.source : output('translate')
  const srt = job.start === 'srt' ? job.source : output('transcribe')
  const dubbed = output('mux')
  const voice = output('dub')
  // what the dub step's voice preview clones from and reads: its video (2nd input) and the translation
  const previewContext: PreviewContext = {
    media: step('dub')?.inputs[1] ?? (job.start === 'media' ? job.source : undefined),
    subtitles: viSrt,
  }
  // what can be played, best first; the original stays reachable to check a translation against
  const sources = [
    dubbed && { label: 'Bản lồng tiếng', path: dubbed },
    job.start === 'media' && { label: 'Bản gốc', path: job.source },
    !dubbed && voice && { label: 'Giọng đọc', path: voice },
  ].filter((x) => !!x)

  const rerun = useMutation({
    mutationFn: async (args: { name: Step['name']; params?: StepOptions; cascade?: boolean }) =>
      (await runStep({ path: { job_id: job.id, name: args.name }, body: { params: args.params, cascade: args.cascade ?? true } })).data,
    onError: (error) => toast.error(errorMessage(error)),
  })
  const cancel = useMutation({
    mutationFn: async () => (await cancelJob({ path: { job_id: job.id } })).data,
    onError: (error) => toast.error(errorMessage(error)),
  })
  const remove = useMutation({
    mutationFn: async () => deleteJob({ path: { job_id: job.id } }),
    onSuccess: onClose,
    onError: (error) => toast.error(errorMessage(error)),
  })

  const seek = (seconds: number) => {
    if (!media) return
    media.currentTime = seconds
    void media.play()
  }
  const onSubtitlesSaved = ({ texts: changed, sources }: SubtitlesSaved) => {
    const saved = `Đã lưu ${changed + sources} câu`
    const translate = step('translate')
    const dub = step('dub')
    // the original changed: the translation (and what follows it) is now out of date
    if (sources && translate && viSrt) {
      return toast.success(saved, {
        description: 'Dịch lại để bản dịch theo phụ đề gốc mới — các câu tiếng Việt đã sửa tay sẽ bị ghi đè.',
        action: { label: 'Dịch lại', onClick: () => rerun.mutate({ name: 'translate' }) },
        duration: 10000,
      })
    }
    if (!changed || !dub || !viSrt) return toast.success(saved)
    toast.success(saved, {
      description: 'Lồng tiếng lại để giọng đọc khớp bản sửa (câu không đổi lấy từ cache, nên nhanh).',
      action: { label: 'Lồng tiếng lại', onClick: () => rerun.mutate({ name: 'dub' }) },
      duration: 10000,
    })
  }
  // the translation with its original next to it, or the original alone until it is translated
  const editing = viSrt ?? srt
  const draft = useSubtitleDraft(editing, onSubtitlesSaved)

  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="pr-8 break-all">{fileName(job.source)}</SheetTitle>
        <SheetDescription className="font-mono text-xs break-all">{job.source}</SheetDescription>
        <div className="flex flex-wrap gap-2 pt-2">
          <Button size="sm" variant="outline" onClick={() => reveal({ body: { path: (dubbed ?? job.source) } })}>
            <FolderSearch /> Mở thư mục
          </Button>
          {active && (
            <Button size="sm" variant="outline" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              <Square /> Dừng
            </Button>
          )}
          <Button size="sm" variant="ghost" className="text-muted-foreground" disabled={job.status === 'running'}
            onClick={() => remove.mutate()} title="Chỉ xoá khỏi danh sách, không xoá file">
            <Trash2 /> Xoá khỏi danh sách
          </Button>
        </div>
      </SheetHeader>

      <div className="grid gap-6 p-4">
        <ol className="grid gap-3">
          {job.steps.map((s, i) => (
            <StepRow key={s.id} job={job} step={s} last={i === job.steps.length - 1} pending={rerun.isPending} context={previewContext}
              onRun={(params, cascade) => rerun.mutate({ name: s.name, params, cascade })} />
          ))}
        </ol>

        {sources.length ? (
          <Preview sources={sources} draft={draft} media={media} onMedia={setMedia} onActive={setPlaying} />
        ) : (
          <p className="text-sm text-muted-foreground">Chưa có video hoặc audio để xem.</p>
        )}
        {editing && <SubtitleEditor draft={draft} active={playing} onSeek={seek} />}
      </div>
    </>
  )
}

const escapeVtt = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** Player showing the subtitle draft as it is typed (unsaved edits included), and reporting the cue under the playhead. */
function Preview({ sources, draft, media, onMedia, onActive }: {
  sources: { label: string; path: string }[]
  draft: SubtitleDraft
  media: HTMLMediaElement | null
  onMedia: (el: HTMLMediaElement | null) => void
  onActive: (index: number) => void
}) {
  const [choice, setChoice] = useState(sources[0].path)
  const src = sources.find((s) => s.path === choice)?.path ?? sources[0].path
  const resume = useRef(0) // keep the position when switching between sources
  const { rows } = draft
  const latestRows = useRef(rows)
  useEffect(() => { latestRows.current = rows })
  // the track is rebuilt when cues are merged, split or retimed, and its texts follow typing in place
  const timing = rows.map((r) => `${r.start}-${r.end}`).join()
  const vtt = useRef<VTTCue[]>([])

  // a script-made text track instead of <track src=…>: its cue texts can follow the editor live
  useEffect(() => {
    if (!(media instanceof HTMLVideoElement) || !timing) return
    const track = media.addTextTrack('subtitles', 'Tiếng Việt', 'vi')
    vtt.current = latestRows.current.map((r) => new VTTCue(r.start, r.end, escapeVtt(r.text)))
    vtt.current.forEach((c) => track.addCue(c))
    track.mode = 'showing'
    return () => {
      vtt.current.forEach((c) => track.removeCue(c))
      track.mode = 'disabled'
      vtt.current = []
    }
  }, [media, timing])
  useEffect(() => {
    vtt.current.forEach((c, i) => {
      const text = rows[i] === undefined ? undefined : escapeVtt(rows[i].text)
      if (text !== undefined && c.text !== text) c.text = text
    })
  }, [rows])

  const onTimeUpdate = (e: SyntheticEvent<HTMLMediaElement>) => {
    const t = e.currentTarget.currentTime
    resume.current = t
    onActive(latestRows.current.findIndex((r) => r.start <= t && t < r.end))
  }
  const props = {
    ref: onMedia, src: mediaUrl(src), controls: true, onTimeUpdate,
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => { e.currentTarget.currentTime = resume.current },
  }

  return (
    <div className="grid gap-2">
      {sources.length > 1 && (
        <div className="flex gap-1">
          {sources.map((s) => (
            <Button key={s.path} size="sm" variant={s.path === src ? 'secondary' : 'ghost'} onClick={() => setChoice(s.path)}>
              {s.label}
            </Button>
          ))}
        </div>
      )}
      {VIDEO.test(src)
        ? <video key={src} {...props} className="aspect-video w-full rounded-lg bg-black" />
        : <audio key={src} {...props} className="w-full" />}
      {/\.mkv$/i.test(src) && (
        <p className="text-xs text-muted-foreground">Trình duyệt có thể không phát được .mkv — mở bằng trình phát video.</p>
      )}
    </div>
  )
}

function StepRow({ job, step, last, pending, context, onRun }: {
  job: Job
  step: Step
  last: boolean
  pending: boolean
  context: PreviewContext
  onRun: (params?: StepOptions, cascade?: boolean) => void
}) {
  const { icon: Icon, className } = STATUS_STYLE[step.status]
  const percent = step.total ? Math.round((100 * (step.done ?? 0)) / step.total) : null
  const took = step.started_at && step.finished_at ? step.finished_at - step.started_at : null
  const canRun = step.status !== 'running' && step.status !== 'queued'
  const hasLog = step.attempt > 0
  // a failure opens the log: the traceback is the first thing to look at
  const [showLog, setShowLog] = useState(step.status === 'failed')
  const [prevStatus, setPrevStatus] = useState(step.status)
  if (prevStatus !== step.status) {
    setPrevStatus(step.status)
    if (step.status === 'failed') setShowLog(true)
  }

  return (
    <li className="grid grid-cols-[2rem_1fr] gap-3">
      <div className="flex flex-col items-center">
        <span className={cn('flex size-8 items-center justify-center rounded-full border', className)}>
          <Icon className={cn('size-4', step.status === 'running' && 'animate-spin')} />
        </span>
        {!last && <span className="mt-1 w-px flex-1 bg-border" />}
      </div>
      <div className="grid min-w-0 gap-1 pb-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium">{STEP_LABELS[step.name]}</span>
          <span className="text-xs text-muted-foreground">
            {STATUS_LABELS[step.status]}
            {step.status === 'running' && percent !== null && ` · ${step.done}/${step.total}`}
            {took !== null && step.status === 'done' && ` · ${took.toFixed(1)}s`}
            {step.attempt > 1 && ` · lần ${step.attempt}`}
          </span>
          <div className="ml-auto flex gap-1">
            {hasLog && (
              <Button size="sm" variant={showLog ? 'secondary' : 'ghost'} onClick={() => setShowLog(!showLog)}
                title="Xem log của bước này">
                <ScrollText /> Log
              </Button>
            )}
            {canRun && (<>
              {(step.status === 'failed' || step.status === 'stale' || step.status === 'cancelled') && (
                <Button size="sm" variant="outline" disabled={pending} onClick={() => onRun()}>
                  <RotateCcw /> {step.status === 'failed' ? 'Thử lại' : 'Chạy'}
                </Button>
              )}
              <RerunPopover step={step} pending={pending} context={context} onRun={onRun} />
            </>)}
          </div>
        </div>
        {step.status === 'running' && percent !== null && (
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-sky-500 transition-[width]" style={{ width: `${percent}%` }} />
          </div>
        )}
        {step.error && <pre className="overflow-x-auto rounded-md bg-red-50 p-2 text-xs whitespace-pre-wrap text-red-800 dark:bg-red-950/40 dark:text-red-200">{step.error}</pre>}
        {step.message && <p className="text-xs text-muted-foreground">{step.message}</p>}
        {hasLog && showLog && <LogPanel job={job} step={step} />}
        <p className="truncate font-mono text-xs text-muted-foreground/80" title={step.output}>→ {fileName(step.output)}</p>
      </div>
    </li>
  )
}

/** The step's log, following new lines while scrolled to the bottom. */
function LogPanel({ job, step }: { job: Job; step: Step }) {
  const { data, isPending, error } = useStepLog(job, step, true)
  const ref = useRef<HTMLPreElement>(null)
  const atBottom = useRef(true)
  useEffect(() => {
    const el = ref.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [data?.text])
  const copy = () => {
    if (!data) return
    navigator.clipboard.writeText(data.text).then(() => toast.success('Đã copy log'), () => toast.error('Không copy được'))
  }

  return (
    <div className="relative">
      <pre ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
        }}
        className="max-h-64 overflow-auto rounded-md bg-zinc-950 p-2 pr-10 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-zinc-200">
        {error ? errorMessage(error) : isPending ? 'Đang tải log…' : data.offset > 0 ? `[...]\n${data.text}` : data.text || 'Chưa có log.'}
      </pre>
      {data?.text && (
        <Button size="icon" variant="ghost" className="absolute top-1 right-1 size-7 text-zinc-400 hover:text-zinc-100"
          onClick={copy} title="Copy log">
          <Copy />
        </Button>
      )}
    </div>
  )
}

function RerunPopover({ step, pending, context, onRun }: {
  step: Step
  pending: boolean
  context: PreviewContext
  onRun: (params?: StepOptions, cascade?: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const [params, setParams] = useState<StepOptions>(step.params as StepOptions)
  const [cascade, setCascade] = useState(true)
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setParams(step.params as StepOptions) }}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost">
          <Play /> Chạy lại…
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-80 gap-4">
        <p className="text-sm font-medium">Chạy lại bước {STEP_LABELS[step.name]}</p>
        <StepParams name={step.name} value={params} onChange={setParams} context={context} />
        {step.name !== 'mux' && (
          <SwitchField label="Chạy lại các bước sau" hint="Tắt nếu chỉ muốn xem kết quả bước này trước"
            checked={cascade} onChange={setCascade} />
        )}
        <Button disabled={pending} onClick={() => { onRun(params, cascade); setOpen(false) }}>
          {pending ? <Loader2 className="animate-spin" /> : <Play />} Chạy
        </Button>
      </PopoverContent>
    </Popover>
  )
}
