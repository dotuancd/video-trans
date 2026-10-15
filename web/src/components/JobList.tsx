import { AlertCircle, Check, Circle, CircleDashed, Loader2, RotateCcw, Square } from 'lucide-react'
import type { Job, Step } from '@/api'
import { cn } from '@/lib/utils'
import { fileName, STATUS_LABELS, STEP_LABELS, type Status } from '@/lib/api'

export const STATUS_STYLE: Record<Status, { icon: typeof Check; className: string }> = {
  queued: { icon: Circle, className: 'text-muted-foreground border-border' },
  running: { icon: Loader2, className: 'text-sky-700 border-sky-300 bg-sky-50 dark:text-sky-300 dark:border-sky-800 dark:bg-sky-950/50' },
  done: { icon: Check, className: 'text-emerald-700 border-emerald-300 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-800 dark:bg-emerald-950/50' },
  failed: { icon: AlertCircle, className: 'text-red-700 border-red-300 bg-red-50 dark:text-red-300 dark:border-red-800 dark:bg-red-950/50' },
  cancelled: { icon: Square, className: 'text-muted-foreground border-border bg-muted/50' },
  stale: { icon: RotateCcw, className: 'text-amber-700 border-amber-300 bg-amber-50 dark:text-amber-300 dark:border-amber-800 dark:bg-amber-950/50' },
}

export function StepChip({ step }: { step: Step }) {
  const { icon: Icon, className } = STATUS_STYLE[step.status]
  const percent = step.status === 'running' && step.total ? Math.round((100 * (step.done ?? 0)) / step.total) : null
  return (
    <span
      title={`${STEP_LABELS[step.name]}: ${STATUS_LABELS[step.status]}${step.error ? ` — ${step.error}` : ''}`}
      className={cn('relative inline-flex items-center gap-1.5 overflow-hidden rounded-full border px-2.5 py-0.5 text-xs font-medium', className)}
    >
      {percent !== null && <span className="absolute inset-y-0 left-0 bg-sky-200/60 dark:bg-sky-800/40" style={{ width: `${percent}%` }} />}
      <Icon className={cn('relative size-3.5', step.status === 'running' && 'animate-spin')} />
      <span className="relative">{STEP_LABELS[step.name]}</span>
      {percent !== null && <span className="relative tabular-nums">{percent}%</span>}
    </span>
  )
}

export function JobList({ jobs, selected, onSelect }: {
  jobs: Job[]
  selected?: number
  onSelect: (id: number) => void
}) {
  if (!jobs.length) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-12 text-center text-muted-foreground">
        <CircleDashed className="size-8" />
        <p className="font-medium text-foreground">Chưa có việc nào</p>
        <p className="text-sm">Chọn một video hoặc thư mục bên trái để bắt đầu.</p>
      </div>
    )
  }
  return (
    <ul className="grid gap-2">
      {[...jobs].reverse().map((job) => (
        <li key={job.id}>
          <button
            type="button"
            onClick={() => onSelect(job.id)}
            className={cn(
              'grid w-full gap-2 rounded-xl border bg-card p-4 text-left transition-colors hover:border-foreground/20 hover:bg-accent/40',
              selected === job.id && 'border-foreground/30 bg-accent/40',
            )}
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate font-medium">{fileName(job.source)}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{STATUS_LABELS[job.status]}</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {job.steps.map((step) => (
                <StepChip key={step.id} step={step} />
              ))}
            </div>
            {job.steps.find((s) => s.status === 'failed')?.error && (
              <p className="truncate text-xs text-red-700 dark:text-red-300">
                {job.steps.find((s) => s.status === 'failed')!.error}
              </p>
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}
