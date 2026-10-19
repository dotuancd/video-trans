import { useEffect, useRef, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FolderInput, Inbox, Loader2, Play, SlidersHorizontal, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import {
  createBulkDownload, deleteJobs, moveJobs, previewBulkDownload, rerunJobs,
  type DriverInfo, type Job, type JobCounts, type JobsDone, type Project,
} from '@/api'
import { BulkActions, pausable, paused } from '@/components/JobList'
import { DriverParams, DriverSelect, Field, SwitchField } from '@/components/Params'
import { COLORS, Dot, NONE } from '@/components/Projects'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { same } from '@/components/JobConfig'
import {
  downloadUrl, driversOf, errorMessage, FRESH_HINT, sizeLabel, stageLabel, STATUS_LABELS, useConfig, type Params, type Status,
} from '@/lib/api'
import { startDownload, watchBulkDownload } from '@/lib/bulkDownloads'
import { cn } from '@/lib/utils'
import { dropJobs, matchingIds, putJob, type JobFilter } from '@/lib/jobs'

/** The list's header: tick every job (those loaded, then every one matching the filter, as in Gmail), and
 * what can be done with the jobs ticked (or with all of them). */
export function BulkBar({ jobs, total, counts, filter, checked, everything, onChecked, projects, drivers, view, onOpenProject, onOpenJob }: {
  /** the jobs loaded */
  jobs: Job[]
  /** how many jobs match the filter, loaded or not */
  total: number
  counts?: Pick<JobCounts, 'pausable' | 'resumable'>
  filter: JobFilter
  /** may hold jobs not loaded */
  checked: Set<string>
  /** checked is every job matching the filter */
  everything: boolean
  onChecked: (ids: Set<string>, everything?: boolean) => void
  projects: Project[]
  drivers: DriverInfo[]
  /** the project shown (ALL, NONE or its id) */
  view: string
  onOpenProject: (id: string) => void
  onOpenJob: (id: string) => void
}) {
  const picked = jobs.filter((j) => checked.has(j.id))
  const ids = [...checked]
  const all = jobs.length > 0 && picked.length === jobs.length
  const master = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (master.current) master.current.indeterminate = picked.length > 0 && !all
  })
  const [fetching, setFetching] = useState(false)
  const pickEverything = async () => {
    setFetching(true)
    try {
      onChecked(new Set(await matchingIds(filter)), true)
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setFetching(false)
    }
  }

  return (
    <div className="flex min-h-8 flex-wrap items-center gap-2">
      {jobs.length > 0 && (
        <input ref={master} type="checkbox" className="ml-4 size-4 cursor-pointer accent-sky-600" checked={all}
          aria-label="Chọn tất cả" title={all ? 'Bỏ chọn tất cả' : 'Chọn tất cả việc đã tải'}
          onChange={() => onChecked(new Set(all ? [] : jobs.map((j) => j.id)))} />
      )}
      {checked.size === 0 ? (
        <>
          <h3 className="text-sm font-medium text-muted-foreground">Việc ({total})</h3>
          <BulkActions toPause={counts?.pausable ?? 0} toResume={counts?.resumable ?? 0} ids={() => matchingIds(filter)}
            className="ml-auto" />
        </>
      ) : (
        <>
          <span className="text-sm font-medium">{everything ? `Đã chọn tất cả ${checked.size} việc khớp bộ lọc` : `Đã chọn ${checked.size}`}</span>
          {all && !everything && total > checked.size && (
            <Button size="sm" variant="link" className="px-1" disabled={fetching} onClick={() => void pickEverything()}>
              {fetching && <Loader2 className="animate-spin" />} Chọn tất cả {total} việc khớp bộ lọc
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => onChecked(new Set())} title="Bỏ chọn (Esc)">
            <X /> Bỏ chọn
          </Button>
          <div className="ml-auto flex flex-wrap gap-2">
            {everything ? <BulkActions toPause={counts?.pausable ?? 0} toResume={counts?.resumable ?? 0} ids={ids} />
              : <BulkActions toPause={picked.filter(pausable).length} toResume={picked.filter(paused).length} ids={ids} />}
            <DownloadDialog ids={ids} onOpenJob={onOpenJob} />
            <MoveMenu ids={ids} projects={projects} view={view} onOpenProject={onOpenProject} />
            <RerunDialog jobs={picked} ids={ids} drivers={drivers} />
            <DeleteDialog jobs={picked} ids={ids} onDeleted={(gone) => onChecked(new Set(ids.filter((id) => !gone.includes(id))), everything)} />
          </div>
        </>
      )}
    </div>
  )
}

function MoveMenu({ ids, projects, view, onOpenProject }: {
  ids: string[]
  projects: Project[]
  view: string
  onOpenProject: (id: string) => void
}) {
  const queryClient = useQueryClient()
  const move = useMutation({
    mutationFn: async (project: string | null) => (await moveJobs({ body: { ids, project_id: project } })).data,
    onSuccess: (moved, project) => {
      for (const job of moved) putJob(queryClient, job)
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: ['job-counts'] })
      const name = projects.find((p) => p.id === project)?.name ?? 'Chưa phân loại'
      toast.success(`Đã chuyển ${moved.length} việc vào ${name}`, {
        action: { label: 'Xem', onClick: () => onOpenProject(project ?? NONE) },
      })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline" disabled={move.isPending}><FolderInput /> Chuyển vào…</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        <DropdownMenuItem disabled={view === NONE} onSelect={() => move.mutate(null)}>
          <Inbox /> Chưa phân loại
        </DropdownMenuItem>
        {projects.length > 0 && <DropdownMenuSeparator />}
        {projects.map((p) => (
          <DropdownMenuItem key={p.id} disabled={view === p.id} onSelect={() => move.mutate(p.id)}>
            <Dot color={p.color ?? COLORS[0]} /> {p.name}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Download the results of the jobs ticked: their dubbed videos and/or subtitles, in some languages. What it would
 * hold is previewed first, with the jobs not done yet (left out). One file is downloaded as is; more are zipped by
 * the server ("Gói tải về"), followed by a toast. */
function DownloadDialog({ ids, onOpenJob }: { ids: string[]; onOpenJob: (id: string) => void }) {
  const queryClient = useQueryClient()
  const languages = useConfig().data?.languages
  const [open, setOpen] = useState(false)
  const [video, setVideo] = useState(true)
  const [srt, setSrt] = useState(false)
  /** null: every language */
  const [langs, setLangs] = useState<string[] | null>(null)
  const [showUnfinished, setShowUnfinished] = useState(false)
  const body = { job_ids: ids, video, srt, langs }
  const preview = useQuery({
    queryKey: ['bulk-preview', body],
    queryFn: async () => (await previewBulkDownload({ body })).data,
    enabled: open && (video || srt),
    placeholderData: keepPreviousData,
  })
  const create = useMutation({
    mutationFn: async () => (await createBulkDownload({ body })).data,
    onSuccess: (bulk) => {
      watchBulkDownload(queryClient, bulk)
      setOpen(false)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const data = video || srt ? preview.data : undefined
  const unfinished = data?.unfinished ?? []
  const count = data?.files.length ?? 0
  const langName = (code: string) => languages?.find((l) => l.code === code)?.name ?? code
  const toggle = (code: string) => {
    const on = langs ?? data?.langs ?? []
    const next = on.includes(code) ? on.filter((l) => l !== code) : [...on, code]
    if (next.length) setLangs(data && data.langs.every((l) => next.includes(l)) ? null : next)
  }
  const go = () => {
    if (data?.single) {
      startDownload(downloadUrl(data.single.artifact_id))
      setOpen(false)
    } else {
      create.mutate()
    }
  }
  const checkbox = 'size-4 cursor-pointer accent-sky-600'

  return (
    <>
      <Button size="sm" variant="outline"
        onClick={() => { setLangs(null); setShowUnfinished(false); setOpen(true) }}>
        <Download /> Tải về
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Tải về kết quả {ids.length} việc</DialogTitle>
            <DialogDescription>Chỉ việc đã xong mới có file để tải. Nhiều file được gói thành một file zip.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" className={checkbox} checked={video} onChange={(e) => { setVideo(e.target.checked); setLangs(null) }} />
                Video lồng tiếng
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" className={checkbox} checked={srt} onChange={(e) => { setSrt(e.target.checked); setLangs(null) }} />
                Phụ đề .srt <span className="text-xs text-muted-foreground">(kèm phụ đề gốc)</span>
              </label>
            </div>
            {data && data.langs.length > 1 && (
              <Field label="Ngôn ngữ">
                <div className="flex flex-wrap gap-1.5">
                  {data.langs.map((code) => {
                    const on = !langs || langs.includes(code)
                    return (
                      <button key={code} type="button" aria-pressed={on} onClick={() => toggle(code)}
                        className={cn('rounded-full border px-3 py-1 text-xs transition-colors',
                          on ? 'border-primary bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>
                        {langName(code)}
                      </button>
                    )
                  })}
                </div>
              </Field>
            )}
            <p className="flex items-center gap-2 text-sm">
              {!video && !srt ? <span className="text-muted-foreground">Chọn ít nhất một loại file</span>
                : !data ? <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  : count ? <span>{count} file · {sizeLabel(data.size)}{data.single && ' — tải thẳng, không cần gói'}</span>
                    : <span className="text-muted-foreground">Không có file nào để tải</span>}
              {preview.isFetching && data && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
            </p>
            {preview.isError && <p className="text-sm text-destructive">{errorMessage(preview.error)}</p>}
            {unfinished.length > 0 && (
              <div className="grid gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
                <p>
                  {unfinished.length} việc chưa xong sẽ không được tải.{' '}
                  <Button size="sm" variant="link" className="h-auto p-0" onClick={() => setShowUnfinished(!showUnfinished)}>
                    {showUnfinished ? 'Ẩn' : `Xem ${unfinished.length} việc chưa xong`}
                  </Button>
                </p>
                {showUnfinished && (
                  <ul className="grid max-h-48 gap-0.5 overflow-y-auto">
                    {unfinished.map((j) => (
                      <li key={j.id}>
                        <button type="button" className="flex w-full gap-2 rounded px-1 py-0.5 text-left hover:bg-accent"
                          onClick={() => { setOpen(false); onOpenJob(j.id) }}>
                          <span className="min-w-0 flex-1 truncate">{j.title}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">{STATUS_LABELS[j.status as Status] ?? j.status}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Huỷ</Button>
            {(!data || count > 0) && (
              <Button className="h-auto min-h-9 whitespace-normal" onClick={go}
                disabled={!count || preview.isFetching || preview.isError || create.isPending}>
                {create.isPending ? <Loader2 className="animate-spin" /> : <Download />}
                {unfinished.length ? 'Tiếp tục tải mà không có các việc chưa xong' : 'Tải'}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** jobs: those of ids that are loaded (to tell how many run). */
function DeleteDialog({ jobs, ids, onDeleted }: { jobs: Job[]; ids: string[]; onDeleted: (ids: string[]) => void }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const running = jobs.filter((j) => j.tasks.some((t) => t.status === 'running')).length
  const remove = useMutation({
    mutationFn: async () => (await deleteJobs({ body: { ids } })).data,
    onSuccess: ({ deleted, skipped }) => {
      dropJobs(queryClient, deleted)
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: ['job-counts'] })
      onDeleted(deleted)
      setOpen(false)
      toast.success(`Đã xoá ${deleted.length} việc`, skipped.length
        ? { description: `${skipped.length} việc đang chạy chưa xoá — tạm dừng trước rồi xoá lại.` }
        : undefined)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  return (
    <>
      <Button size="sm" variant="outline" className="text-destructive" onClick={() => setOpen(true)}>
        <Trash2 /> Xoá
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Xoá {ids.length} việc?</DialogTitle>
            <DialogDescription>
              Xoá việc và các file trung gian; file đã xuất cạnh video được giữ.
              {running > 0 ? ` ${running} việc đang chạy sẽ được bỏ qua.`
                : ids.length > jobs.length && ' Việc đang chạy sẽ được bỏ qua.'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Huỷ</Button>
            <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {remove.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />} Xoá {ids.length - running} việc
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** Run one stage again in every job ticked, with another driver or params. Only the params changed are sent:
 * each job keeps the others it has. jobs: those of ids that are loaded, which the stages are offered from; a job
 * without the stage is skipped by the server. */
function RerunDialog({ jobs, ids, drivers }: { jobs: Job[]; ids: string[]; drivers: DriverInfo[] }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const tasks = jobs.flatMap((j) => j.tasks).filter((t) => t.stage !== 'source' && t.status !== 'skipped')
  const stages = [...new Set([...tasks].sort((a, b) => a.position - b.position).map((t) => t.stage))]
  const [stage, setStage] = useState('')
  const [driverName, setDriverName] = useState('')
  const [params, setParams] = useState<Params>({})
  const [cascade, setCascade] = useState(true)
  const [fresh, setFresh] = useState(false)
  /** jobs that would lose edits by hand: asked about before running them */
  const [confirm, setConfirm] = useState<string[]>([])

  const ofStage = tasks.filter((t) => t.stage === stage)
  const first = ofStage[0]
  // jobs not loaded: not known whether they have the stage
  const count = new Set(ofStage.map((t) => t.job_id)).size + ids.length - jobs.length
  const driver = drivers.find((d) => d.name === driverName)
  const choices = driversOf(drivers, stage)
  const pick = (name: string) => {
    const task = tasks.find((t) => t.stage === name)
    setStage(name)
    setDriverName(task?.driver ?? '')
    setParams(task?.params ?? {})
  }
  const changedDriver = !!first && driverName !== first.driver
  // the same driver: only what was changed, so each job keeps its own other params
  const changes = changedDriver ? params
    : Object.fromEntries(Object.entries(params).filter(([k, v]) => !same(v, first?.params[k])))

  const run = useMutation({
    mutationFn: async ({ ids, confirm }: { ids: string[]; confirm?: boolean }) => (await rerunJobs({
      body: {
        ids, stage, mode: cascade ? 'cascade' : 'only', params: changes,
        driver: changedDriver ? driverName : undefined, confirm: !!confirm, fresh,
      },
    })).data,
    onSuccess: (result: JobsDone) => {
      for (const job of result.jobs) putJob(queryClient, job)
      void queryClient.invalidateQueries({ queryKey: ['job-counts'] })
      if (result.jobs.length) toast.success(`Chạy lại ${stageLabel(stage)} cho ${result.jobs.length} việc`)
      if (result.skipped.length) {
        toast.warning(`${result.skipped.length} việc bỏ qua`, {
          description: result.skipped.map((s) => `${s.title}: ${s.reason}`).join('\n'),
        })
      }
      setConfirm(result.needs_confirm)
      if (!result.needs_confirm.length) setOpen(false)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  return (
    <>
      <Button size="sm" variant="outline" disabled={!stages.length}
        onClick={() => { pick(stages.includes('translate') ? 'translate' : stages[0]); setConfirm([]); setFresh(false); setOpen(true) }}>
        <SlidersHorizontal /> Chạy lại…
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          {confirm.length > 0 ? (
            <>
              <DialogHeader>
                <DialogTitle>Thay bản đã sửa tay?</DialogTitle>
                <DialogDescription>
                  {confirm.length} việc có phụ đề bạn đã sửa tay ở bước này hoặc sau nó. Chạy lại sẽ tạo kết quả mới thay
                  cho bản sửa (bản sửa vẫn nằm trong lịch sử các lần chạy).
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>Bỏ qua các việc này</Button>
                <Button disabled={run.isPending} onClick={() => run.mutate({ ids: confirm, confirm: true })}>
                  {run.isPending ? <Loader2 className="animate-spin" /> : <Play />} Vẫn chạy lại {confirm.length} việc
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Chạy lại {ids.length} việc</DialogTitle>
                <DialogDescription>
                  Chỉ thông số bạn đổi được áp dụng; mỗi việc giữ các thông số khác của nó.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4">
                <Field label="Bước" hint={count < ids.length ? `${count}/${ids.length} việc có bước này; việc còn lại bỏ qua` : undefined}>
                  <Select value={stage} onValueChange={pick}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {stages.map((s) => <SelectItem key={s} value={s}>{stageLabel(s)}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </Field>
                {choices.length > 1 && (
                  <Field label="Engine" hint={changedDriver ? 'Đổi engine: mọi việc dùng các thông số bên dưới' : undefined}>
                    <DriverSelect drivers={choices} value={driverName}
                      onChange={(name) => { setDriverName(name); setParams(name === first?.driver ? first.params : {}) }} />
                  </Field>
                )}
                {driver && (
                  <DriverParams driver={driver} value={params} onChange={setParams}
                    context={first ? { job_id: first.job_id, lang: first.lang ?? 'vi' } : undefined} />
                )}
                {stage !== 'mux' && (
                  <SwitchField label="Chạy lại các bước sau" hint="Tắt để xem kết quả bước này trước; các bước sau thành 'Cần chạy lại'"
                    checked={cascade} onChange={setCascade} />
                )}
                <SwitchField label="Bỏ qua cache" hint={FRESH_HINT} checked={fresh} onChange={setFresh} />
              </div>
              <DialogFooter>
                <span className="mr-auto self-center text-xs text-muted-foreground">
                  {changedDriver ? `Engine → ${driver?.title ?? driverName}`
                    : Object.keys(changes).length ? `Đổi: ${Object.keys(changes).join(', ')}` : 'Giữ nguyên thông số'}
                </span>
                <Button variant="ghost" onClick={() => setOpen(false)}>Huỷ</Button>
                <Button disabled={run.isPending || !count || !!driver?.available}
                  onClick={() => run.mutate({ ids })}>
                  {run.isPending ? <Loader2 className="animate-spin" /> : <Play />} Chạy lại {count} việc
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
