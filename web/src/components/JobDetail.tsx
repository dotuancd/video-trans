import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Download, ExternalLink, Eye, FolderSearch, Languages, Loader2, Play, Plus, RotateCcw, ScrollText, Square, StepForward, Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  addLangs, cancelJob, deleteJob, listAttempts, pickAttempt, refreshJob, rerunTask, resumeJob, reveal, updateJob,
  type Config, type DriverInfo, type Job, type Project, type Task, type TranscriptSaved,
} from '@/api'
import { STATUS_STYLE, taskLabel } from '@/components/JobList'
import { DriverParams, DriverSelect, SwitchField } from '@/components/Params'
import { SubtitleEditor, useSubtitleDraft, type SubtitleDraft } from '@/components/SubtitleEditor'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import {
  artifactUrl, downloadUrl, driversOf, errorMessage, inlineUrl, isActive, needsConfirm, putJob, slotArtifact,
  STATUS_LABELS, useAttemptEvents, type Params,
} from '@/lib/api'

/** Select value of "no project" */
const NO_PROJECT = '-'
const VIDEO = /\.(mp4|mov|m4v|webm|mkv)$/i
const SYSTEM_ERROR = /mã ([0-9a-f]{8})/

type RerunArgs = { task: Task; mode: 'only' | 'cascade'; params?: Params; driver?: string; confirm?: boolean }

export function JobDetail({ job, drivers, config, projects, onClose }: {
  job?: Job
  drivers: DriverInfo[]
  config: Config
  projects: Project[]
  onClose: () => void
}) {
  return (
    <Sheet open={!!job} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        className="gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-3xl"
        // toasts (e.g. "Cập nhật phía sau" after saving subtitles) live outside the sheet: clicking them must not close it
        onInteractOutside={(e) => (e.target as Element | null)?.closest('[data-sonner-toaster]') && e.preventDefault()}
      >
        {job && <JobBody key={job.id} job={job} drivers={drivers} config={config} projects={projects} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  )
}

function JobBody({ job, drivers, config, projects, onClose }: {
  job: Job
  drivers: DriverInfo[]
  config: Config
  projects: Project[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [media, setMedia] = useState<HTMLMediaElement | null>(null)
  const [playing, setPlaying] = useState(-1) // cue under the playhead
  const [confirming, setConfirming] = useState<RerunArgs>()
  const active = isActive(job.status)
  const statuses = new Set(job.tasks.map((t) => t.status))
  const updated = (data: Job) => putJob(queryClient, data)
  const onError = (error: unknown) => toast.error(errorMessage(error))

  // which language the player and the editor show: a translation, or the original
  const views = [...job.langs.filter((l) => slotArtifact(job, `transcript.${l}`)), 'src']
  const [chosen, setLang] = useState<string>()
  const lang = chosen && views.includes(chosen) ? chosen : views.find((v) => slotArtifact(job, v === 'src' ? 'transcript.src' : `transcript.${v}`))
  const target = lang && lang !== 'src' ? lang : job.langs[0]

  const rerun = useMutation({
    mutationFn: async ({ task, mode, params, driver, confirm }: RerunArgs) =>
      (await rerunTask({ path: { task_id: task.id }, body: { mode, params, driver, confirm: !!confirm } })).data,
    onSuccess: updated,
    onError: (error, args) => (needsConfirm(error) ? setConfirming(args) : onError(error)),
  })
  const cancel = useJobAction(job.id, cancelJob)
  const resume = useJobAction(job.id, resumeJob)
  const refresh = useJobAction(job.id, refreshJob)
  const move = useMutation({
    mutationFn: async (project: string | null) => (await updateJob({ path: { job_id: job.id }, body: { project_id: project } })).data,
    onSuccess: updated,
    onError,
  })
  const remove = useMutation({
    mutationFn: async () => deleteJob({ path: { job_id: job.id } }),
    onSuccess: () => {
      queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) => jobs.filter((j) => j.id !== job.id))
      onClose()
    },
    onError,
  })

  const task = (key: string) => job.tasks.find((t) => t.key === key)
  const onSaved = ({ texts, sources, source_text_changed }: TranscriptSaved) => {
    const saved = `Đã lưu ${texts + sources} câu`
    const translate = lang && lang !== 'src' ? task(`translate@${lang}`) : undefined
    // the translation edited too: it is the result now, only the steps after it run again
    if (source_text_changed && texts === 0 && translate && translate.status !== 'skipped') {
      return toast.success(saved, {
        description: 'Phụ đề gốc đã đổi: dịch lại để bản dịch theo bản gốc mới.',
        action: { label: 'Dịch lại', onClick: () => rerun.mutate({ task: translate, mode: 'cascade' }) },
        duration: 10000,
      })
    }
    if (texts + sources === 0) return toast.success(saved)
    toast.success(saved, {
      description: 'Các bước phía sau (lồng tiếng, ghép video) cần chạy lại theo bản sửa — câu không đổi lấy từ cache.',
      action: { label: 'Cập nhật phía sau', onClick: () => refresh.mutate() },
      duration: 10000,
    })
  }
  const draft = useSubtitleDraft(job.id, lang, onSaved)

  // what can be played for the language shown, best first; the original stays reachable
  const out = target ? slotArtifact(job, `media.out.${target}`) : undefined
  const voice = target ? slotArtifact(job, `audio.dub.${target}`) : undefined
  const original = slotArtifact(job, 'media.video')
  const sources = [
    out && { label: 'Bản lồng tiếng', id: out.id, path: out.path },
    original && { label: 'Bản gốc', id: original.id, path: original.path },
    !out && voice && { label: 'Giọng đọc', id: voice.id, path: voice.path },
  ].filter((x) => !!x)
  const exported = job.tasks.flatMap((t) => t.exports)
  const skipped = job.tasks.filter((t) => t.status === 'skipped' && t.message && !t.message.includes('nguồn'))

  const seek = (seconds: number) => {
    if (!media) return
    media.currentTime = seconds
    void media.play()
  }

  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="pr-8 break-all">{job.title}</SheetTitle>
        <SheetDescription className="font-mono text-xs break-all">{job.source}</SheetDescription>
        <div className="flex flex-wrap gap-2 pt-2">
          <Select value={job.project_id ?? NO_PROJECT} onValueChange={(v) => move.mutate(v === NO_PROJECT ? null : v)}>
            <SelectTrigger size="sm" className="max-w-48" aria-label="Project"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_PROJECT}>Chưa phân loại</SelectItem>
              {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
          {(exported[0] || !job.source.startsWith('http')) && (
            <Button size="sm" variant="outline" onClick={() => reveal({ body: { path: exported.at(-1) ?? job.source } })}>
              <FolderSearch /> Mở thư mục
            </Button>
          )}
          {active && (
            <Button size="sm" variant="outline" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              <Square /> Dừng
            </Button>
          )}
          {(statuses.has('waiting_review') || statuses.has('cancelled') || statuses.has('failed')) && (
            <Button size="sm" disabled={resume.isPending} onClick={() => resume.mutate()}>
              <StepForward /> Tiếp tục
            </Button>
          )}
          {statuses.has('stale') && (
            <Button size="sm" variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}
              title="Chạy lại các bước có đầu vào đã đổi">
              <RotateCcw /> Cập nhật phía sau
            </Button>
          )}
          {job.langs.length > 0 && <AddLanguage job={job} config={config} onAdded={updated} />}
          <Button size="sm" variant="ghost" className="text-muted-foreground" disabled={job.status === 'running'}
            onClick={() => remove.mutate()} title="Xoá việc và các file trung gian; file đã xuất cạnh video được giữ">
            <Trash2 /> Xoá
          </Button>
        </div>
      </SheetHeader>

      <div className="grid gap-6 p-4">
        {statuses.has('waiting_review') && (
          <p className="rounded-md border border-violet-300 bg-violet-50 p-3 text-sm text-violet-900 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-200">
            Việc đang chờ bạn: xem lại bản dịch bên dưới, nghe thử giọng ở bước Lồng tiếng (Chạy lại…), rồi bấm <b>Tiếp tục</b>.
          </p>
        )}
        <ol className="grid gap-3">
          {job.tasks.filter((t) => t.status !== 'skipped').map((t, i, list) => (
            <TaskRow key={t.id} task={t} last={i === list.length - 1} drivers={driversOf(drivers, t.stage)}
              pending={rerun.isPending} onRerun={(args) => rerun.mutate({ task: t, ...args })} />
          ))}
        </ol>
        {skipped.length > 0 && (
          <ul className="grid gap-0.5 text-xs text-muted-foreground">
            {skipped.map((t) => <li key={t.id}>Bỏ qua {taskLabel(t)}: {t.message}</li>)}
          </ul>
        )}

        {views.length > 1 && (
          <div className="flex items-center gap-1">
            <Languages className="mr-1 size-4 text-muted-foreground" />
            {views.map((v) => (
              <Button key={v} size="sm" variant={v === lang ? 'secondary' : 'ghost'} onClick={() => setLang(v)}>
                {v === 'src' ? 'Bản gốc' : config.languages.find((l) => l.code === v)?.name ?? v}
              </Button>
            ))}
          </div>
        )}
        {sources.length ? (
          <Preview key={lang} sources={sources} draft={draft} media={media} onMedia={setMedia} onActive={setPlaying} />
        ) : (
          <p className="text-sm text-muted-foreground">Chưa có video hoặc audio để xem.</p>
        )}
        {lang && <SubtitleEditor draft={draft} active={playing} onSeek={seek} />}
      </div>

      <Dialog open={!!confirming} onOpenChange={(open) => !open && setConfirming(undefined)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Thay bản đã sửa tay?</DialogTitle>
            <DialogDescription>
              Chạy lại {confirming && taskLabel(confirming.task)} sẽ tạo kết quả mới thay cho phụ đề bạn đã sửa.
              Bản sửa vẫn nằm trong lịch sử các lần chạy.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(undefined)}>Huỷ</Button>
            <Button onClick={() => { rerun.mutate({ ...confirming!, confirm: true }); setConfirming(undefined) }}>
              Vẫn chạy lại
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** POST /jobs/{id}/<action>: the job comes back and replaces the cached one. */
function useJobAction(jobId: string, action: typeof cancelJob) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => (await action({ path: { job_id: jobId } })).data,
    onSuccess: (job: Job) => putJob(queryClient, job),
    onError: (error) => toast.error(errorMessage(error)),
  })
}

function AddLanguage({ job, config, onAdded }: { job: Job; config: Config; onAdded: (job: Job) => void }) {
  const [open, setOpen] = useState(false)
  const add = useMutation({
    mutationFn: async (lang: string) => (await addLangs({ path: { job_id: job.id }, body: { langs: [lang] } })).data,
    onSuccess: (data) => { onAdded(data); setOpen(false) },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const left = config.languages.filter((l) => !job.langs.includes(l.code))
  if (!left.length) return null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="outline"><Plus /> Ngôn ngữ</Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="grid w-64 gap-2">
        <p className="text-sm font-medium">Dịch thêm sang</p>
        <p className="text-xs text-muted-foreground">Dùng lại phụ đề gốc đã có, chỉ chạy các bước của ngôn ngữ mới.</p>
        <div className="flex flex-wrap gap-1.5">
          {left.map((l) => (
            <Button key={l.code} size="sm" variant="outline" disabled={add.isPending} onClick={() => add.mutate(l.code)}>
              {l.name}
            </Button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

const escapeVtt = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** Player showing the subtitle draft as it is typed (unsaved edits included), and reporting the cue under the playhead. */
function Preview({ sources, draft, media, onMedia, onActive }: {
  sources: { label: string; id: string; path: string }[]
  draft: SubtitleDraft
  media: HTMLMediaElement | null
  onMedia: (el: HTMLMediaElement | null) => void
  onActive: (index: number) => void
}) {
  const [choice, setChoice] = useState(sources[0].id)
  const source = sources.find((s) => s.id === choice) ?? sources[0]
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
    const track = media.addTextTrack('subtitles', 'Phụ đề', 'vi')
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
    ref: onMedia, src: artifactUrl(source.id), controls: true, onTimeUpdate,
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => { e.currentTarget.currentTime = resume.current },
  }

  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-1">
        {sources.length > 1 && sources.map((s) => (
          <Button key={s.id} size="sm" variant={s.id === source.id ? 'secondary' : 'ghost'} onClick={() => setChoice(s.id)}>
            {s.label}
          </Button>
        ))}
        <Button asChild size="sm" variant="ghost" className="ml-auto text-muted-foreground">
          <a href={downloadUrl(source.id)} download><Download /> Tải về</a>
        </Button>
      </div>
      {VIDEO.test(source.path)
        ? <video key={source.id} {...props} className="aspect-video w-full rounded-lg bg-black" />
        : <audio key={source.id} {...props} className="w-full" />}
      {/\.mkv$/i.test(source.path) && (
        <p className="text-xs text-muted-foreground">Trình duyệt có thể không phát được .mkv — mở bằng trình phát video.</p>
      )}
    </div>
  )
}

function TaskRow({ task, last, drivers, pending, onRerun }: {
  task: Task
  last: boolean
  drivers: DriverInfo[]
  pending: boolean
  onRerun: (args: Omit<RerunArgs, 'task'>) => void
}) {
  const { icon: Icon, className } = STATUS_STYLE[task.status]
  const percent = task.total ? Math.round((100 * (task.done ?? 0)) / task.total) : null
  const busy = task.status === 'running' || task.status === 'queued'
  const driver = drivers.find((d) => d.name === task.driver)
  const code = task.message?.match(SYSTEM_ERROR)?.[1]
  // a failure opens the log: what went wrong is the first thing to look at
  const [showLog, setShowLog] = useState(task.status === 'failed')
  const [prevStatus, setPrevStatus] = useState(task.status)
  if (prevStatus !== task.status) {
    setPrevStatus(task.status)
    if (task.status === 'failed') setShowLog(true)
  }

  return (
    <li className="grid grid-cols-[2rem_1fr] gap-3">
      <div className="flex flex-col items-center">
        <span className={cn('flex size-8 items-center justify-center rounded-full border', className)}>
          <Icon className={cn('size-4', task.status === 'running' && 'animate-spin')} />
        </span>
        {!last && <span className="mt-1 w-px flex-1 bg-border" />}
      </div>
      <div className="grid min-w-0 gap-1 pb-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium">{taskLabel(task)}</span>
          <span className="text-xs text-muted-foreground">
            {driver?.title ?? task.driver} · {STATUS_LABELS[task.status]}
            {task.status === 'running' && percent !== null && ` · ${task.done}/${task.total}`}
            {task.retries > 0 && ` · thử lại ${task.retries}`}
          </span>
          <div className="ml-auto flex gap-1">
            {(task.current_attempt || task.status !== 'blocked') && task.stage !== 'source' && (
              <Button size="sm" variant={showLog ? 'secondary' : 'ghost'} onClick={() => setShowLog(!showLog)}
                title="Nhật ký của bước này">
                <ScrollText /> Log
              </Button>
            )}
            {!busy && (task.status === 'failed' || task.status === 'stale' || task.status === 'cancelled') && (
              <Button size="sm" variant="outline" disabled={pending} onClick={() => onRerun({ mode: 'cascade' })}>
                <RotateCcw /> {task.status === 'failed' ? 'Thử lại' : 'Chạy'}
              </Button>
            )}
            {!busy && task.stage !== 'source' && (
              <RerunPopover task={task} drivers={drivers} pending={pending} onRun={onRerun} />
            )}
          </div>
        </div>
        {task.status === 'running' && percent !== null && (
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-sky-500 transition-[width]" style={{ width: `${percent}%` }} />
          </div>
        )}
        {task.message && (
          <p className={cn('text-xs', task.status === 'failed' ? 'text-red-700 dark:text-red-300' : 'text-muted-foreground')}>
            {task.message}
            {code && (
              <a className="ml-2 inline-flex items-center gap-0.5 underline" href={`/api/admin/traces/${code}`} target="_blank" rel="noreferrer">
                log hệ thống <ExternalLink className="size-3" />
              </a>
            )}
          </p>
        )}
        {showLog && <LogPanel task={task} />}
        {task.stage !== 'source' && !!task.artifacts?.length && (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            {(task.artifacts ?? []).map((a) => (
              <a key={a.id} href={downloadUrl(a.id)} download title={task.exports.length ? `Đã xuất: ${task.exports.join(', ')}` : 'Tải về'}
                className="inline-flex items-center gap-1 font-mono text-xs text-muted-foreground/80 hover:text-foreground">
                <Download className="size-3" /> {a.download_name}
              </a>
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString()
const LEVEL_STYLE: Record<string, string> = { warn: 'text-amber-300', error: 'text-red-300' }

/** The pipeline log of the task, one attempt at a time (newest first), following new lines; an earlier
 * attempt also shows its files, and can become the result again. */
function LogPanel({ task }: { task: Task }) {
  const queryClient = useQueryClient()
  const pick = useMutation({
    mutationFn: async (attemptId: string) =>
      (await pickAttempt({ path: { task_id: task.id, attempt_id: attemptId } })).data,
    onSuccess: (job: Job) => {
      putJob(queryClient, job)
      toast.success('Đã dùng lại lần chạy này — các bước sau cần cập nhật')
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const attempts = useQuery({
    queryKey: ['attempts', task.id, task.current_attempt, task.status],
    queryFn: async () => (await listAttempts({ path: { task_id: task.id } })).data,
  })
  const [chosen, setChosen] = useState<string>()
  const attempt = attempts.data?.find((a) => a.id === chosen) ?? attempts.data?.[0]
  const events = useAttemptEvents(attempt?.id)
  const ref = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)
  useEffect(() => {
    const el = ref.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [events.data])

  if (attempts.isLoading) return <Loader2 className="size-4 animate-spin text-muted-foreground" />
  if (!attempt) return <p className="text-xs text-muted-foreground">Chưa chạy lần nào.</p>
  const ORIGIN: Record<string, string> = { driver: '', cache: ' · dùng lại', human: ' · sửa tay' }
  return (
    <div className="grid gap-1">
      <div className="flex flex-wrap items-center gap-1">
        {attempts.data!.slice(0, 6).map((a) => (
          <Button key={a.id} size="sm" variant={a.id === attempt.id ? 'secondary' : 'ghost'} className="h-6 px-2 text-xs"
            onClick={() => setChosen(a.id)}>
            Lần {a.n}{ORIGIN[a.origin]}{a.id === task.current_attempt && ' · đang dùng'}
          </Button>
        ))}
        <a className="ml-auto inline-flex items-center gap-0.5 text-xs text-muted-foreground underline hover:text-foreground"
          href={`/api/attempts/${attempt.id}/system`} target="_blank" rel="noreferrer">
          Log hệ thống <ExternalLink className="size-3" />
        </a>
      </div>
      {attempt.id !== task.current_attempt && attempt.status === 'done' && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-dashed px-2 py-1">
          {attempt.artifacts?.length ? attempt.artifacts.map((a) => (
            <span key={a.id} className="inline-flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
              {a.download_name}
              <a href={inlineUrl(a.id)} target="_blank" rel="noreferrer" title="Xem" className="hover:text-foreground">
                <Eye className="size-3" />
              </a>
              <a href={downloadUrl(a.id)} download title="Tải về" className="hover:text-foreground">
                <Download className="size-3" />
              </a>
            </span>
          )) : <span className="text-xs text-muted-foreground">File của lần này đã được dọn.</span>}
          {!!attempt.artifacts?.length && task.status !== 'running' && (
            <Button size="sm" variant="outline" className="ml-auto h-6 px-2 text-xs" disabled={pick.isPending}
              onClick={() => pick.mutate(attempt.id)}>
              <RotateCcw /> Dùng lần này
            </Button>
          )}
        </div>
      )}
      <div ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
        }}
        className="max-h-64 overflow-auto rounded-md bg-zinc-950 p-2 font-mono text-[11px] leading-relaxed text-zinc-200">
        {events.isLoading ? 'Đang tải…' : events.data?.length ? events.data.map((e) => (
          <div key={e.id} className={cn('break-words', LEVEL_STYLE[e.level])}>
            <span className="text-zinc-500">{time(e.ts)} </span>{e.message}
          </div>
        )) : <span className="text-zinc-500">{attempt.origin === 'human' ? 'Bản sửa tay.' : 'Chưa có dòng nào.'}</span>}
      </div>
    </div>
  )
}

function RerunPopover({ task, drivers, pending, onRun }: {
  task: Task
  drivers: DriverInfo[]
  pending: boolean
  onRun: (args: Omit<RerunArgs, 'task'>) => void
}) {
  const [open, setOpen] = useState(false)
  const [driverName, setDriverName] = useState(task.driver)
  const [params, setParams] = useState<Params>(task.params)
  const [cascade, setCascade] = useState(true)
  const driver = drivers.find((d) => d.name === driverName)
  const reset = () => { setDriverName(task.driver); setParams(task.params) }
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) reset() }}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost"><Play /> Chạy lại…</Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid max-h-[80vh] w-80 gap-4 overflow-y-auto">
        <p className="text-sm font-medium">Chạy lại {taskLabel(task)}</p>
        {drivers.length > 1 && (
          <DriverSelect drivers={drivers} value={driverName}
            onChange={(name) => { setDriverName(name); setParams(name === task.driver ? task.params : {}) }} />
        )}
        {driver && (
          <DriverParams driver={driver} value={params} onChange={setParams}
            context={{ job_id: task.job_id, lang: task.lang ?? 'vi' }} />
        )}
        {task.stage !== 'mux' && (
          <SwitchField label="Chạy lại các bước sau" hint="Tắt để xem kết quả bước này trước; các bước sau thành 'Cần chạy lại'"
            checked={cascade} onChange={setCascade} />
        )}
        <Button disabled={pending || !!driver?.available} onClick={() => {
          onRun({ mode: cascade ? 'cascade' : 'only', params, driver: driverName !== task.driver ? driverName : undefined })
          setOpen(false)
        }}>
          {pending ? <Loader2 className="animate-spin" /> : <Play />} Chạy
        </Button>
      </PopoverContent>
    </Popover>
  )
}
