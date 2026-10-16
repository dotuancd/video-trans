import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, Circle, CircleDashed, CirclePause, Loader2, Minus, Pause, Play, RotateCcw, Square } from 'lucide-react'
import { toast } from 'sonner'
import { pauseJobs, unpauseJobs, type Job, type Task } from '@/api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { STATUS_LABELS, errorMessage, formatCost, putJob, stageLabel, type Status } from '@/lib/api'

export const STATUS_STYLE: Record<Status, { icon: typeof Check; className: string }> = {
  blocked: { icon: CircleDashed, className: 'text-muted-foreground border-border' },
  queued: { icon: Circle, className: 'text-muted-foreground border-border' },
  running: { icon: Loader2, className: 'text-sky-700 border-sky-300 bg-sky-50 dark:text-sky-300 dark:border-sky-800 dark:bg-sky-950/50' },
  done: { icon: Check, className: 'text-emerald-700 border-emerald-300 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/50' },
  failed: { icon: AlertCircle, className: 'text-red-700 border-red-300 bg-red-50 dark:text-red-300 dark:border-red-800 dark:bg-red-950/50' },
  cancelled: { icon: Square, className: 'text-muted-foreground border-border bg-muted/50' },
  skipped: { icon: Minus, className: 'text-muted-foreground/60 border-dashed border-border' },
  waiting_review: { icon: CirclePause, className: 'text-violet-700 border-violet-300 bg-violet-50 dark:text-violet-300 dark:border-violet-800 dark:bg-violet-950/50' },
  stale: { icon: RotateCcw, className: 'text-amber-700 border-amber-300 bg-amber-50 dark:text-amber-300 dark:border-amber-800 dark:bg-amber-950/50' },
}

export const taskLabel = (task: Task) => `${stageLabel(task.stage)}${task.lang ? ` · ${task.lang}` : ''}`

export function TaskChip({ task }: { task: Task }) {
  const { icon: Icon, className } = STATUS_STYLE[task.status]
  const percent = task.status === 'running' && task.total ? Math.round((100 * (task.done ?? 0)) / task.total) : null
  return (
    <span
      title={`${taskLabel(task)}: ${STATUS_LABELS[task.status]}${task.message ? ` — ${task.message}` : ''}`}
      className={cn('relative inline-flex items-center gap-1.5 overflow-hidden rounded-full border px-2.5 py-0.5 text-xs font-medium', className)}
    >
      {percent !== null && <span className="absolute inset-y-0 left-0 bg-sky-200/60 dark:bg-sky-800/40" style={{ width: `${percent}%` }} />}
      <Icon className={cn('relative size-3.5', task.status === 'running' && 'animate-spin')} />
      <span className="relative">{taskLabel(task)}</span>
      {percent !== null && <span className="relative tabular-nums">{percent}%</span>}
    </span>
  )
}

export function JobList({ jobs, selected, onSelect, checked, onCheck }: {
  jobs: Job[]
  selected?: string
  onSelect: (id: string) => void
  /** jobs ticked for the bulk actions */
  checked: Set<string>
  /** shift: tick every job from the last one ticked */
  onCheck: (id: string, range: boolean) => void
}) {
  if (!jobs.length) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-12 text-center text-muted-foreground">
        <CircleDashed className="size-8" />
        <p className="font-medium text-foreground">Chưa có việc nào</p>
        <p className="text-sm">Tải lên hoặc chọn một video bên trái để bắt đầu.</p>
      </div>
    )
  }
  return (
    <ul className="grid gap-2">
      {jobs.map((job) => {
        const failed = job.tasks.find((t) => t.status === 'failed')
        const ticked = checked.has(job.id)
        return (
          <li key={job.id} className={cn(
            'flex rounded-xl border bg-card transition-colors hover:border-foreground/20 hover:bg-accent/40',
            selected === job.id && 'border-foreground/30 bg-accent/40',
            ticked && 'border-sky-400/70 bg-sky-50/60 dark:border-sky-700 dark:bg-sky-950/30',
          )}>
            <label className="flex cursor-pointer items-start py-4 pr-1 pl-4" title="Chọn để thao tác nhiều việc (Shift: chọn cả đoạn)">
              <input type="checkbox" className="mt-0.5 size-4 cursor-pointer accent-sky-600" checked={ticked}
                aria-label={`Chọn ${job.title}`} onChange={() => {}} onClick={(e) => onCheck(job.id, e.shiftKey)} />
            </label>
            <button
              type="button"
              onClick={() => onSelect(job.id)}
              className="grid min-w-0 flex-1 gap-2 py-4 pr-4 pl-2 text-left"
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate font-medium">{job.title}</span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {job.langs.length > 0 && `${job.langs.join(', ')} · `}{STATUS_LABELS[job.status]}
                  {formatCost(job.cost_usd) && <span title="Chi phí API (OpenAI…), tính cả các lần chạy lại"> · {formatCost(job.cost_usd)}</span>}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {job.tasks.filter((t) => t.status !== 'skipped' && t.stage !== 'source').map((task) => (
                  <TaskChip key={task.id} task={task} />
                ))}
              </div>
              {failed?.message && <p className="truncate text-xs text-red-700 dark:text-red-300">{failed.message}</p>}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

const pausable = (job: Job) =>
  job.tasks.some((t) => t.status === 'queued' || t.status === 'blocked' || (t.status === 'running' && !t.cancel_requested))
const paused = (job: Job) =>
  job.tasks.some((t) => t.status === 'cancelled' || (t.status === 'running' && t.cancel_requested))

/** Pause / continue the jobs given (those ticked, or every job shown). Jobs waiting for review and failed
 * steps are left to each job. */
export function BulkActions({ jobs, className }: { jobs: Job[]; className?: string }) {
  const queryClient = useQueryClient()
  const toPause = jobs.filter(pausable).map((j) => j.id)
  const toResume = jobs.filter(paused).map((j) => j.id)
  const act = useMutation({
    mutationFn: async (pause: boolean) =>
      (await (pause ? pauseJobs : unpauseJobs)({ body: { ids: pause ? toPause : toResume } })).data,
    onSuccess: (changed, pause) => {
      for (const job of changed) putJob(queryClient, job)
      toast.success(`${pause ? 'Đã tạm dừng' : 'Tiếp tục'} ${changed.length} việc`)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  if (!toPause.length && !toResume.length) return null
  return (
    <div className={cn('flex gap-2', className)}>
      {toPause.length > 0 && (
        <Button size="sm" variant="outline" disabled={act.isPending} onClick={() => act.mutate(true)}
          title="Dừng các việc đang chạy và đang chờ; bước đang chạy dở sẽ chạy lại từ đầu khi tiếp tục">
          <Pause /> Tạm dừng {toPause.length}
        </Button>
      )}
      {toResume.length > 0 && (
        <Button size="sm" variant="outline" disabled={act.isPending} onClick={() => act.mutate(false)}
          title="Chạy tiếp các việc đã dừng; bước chờ duyệt và bước lỗi vẫn để bạn xử lý từng việc">
          <Play /> Tiếp tục {toResume.length}
        </Button>
      )}
    </div>
  )
}
