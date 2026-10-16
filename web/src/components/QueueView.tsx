import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Clock, Cpu, Globe, Loader2, Minus, Plus, Zap } from 'lucide-react'
import { getQueue, setWorkers, type Lane, type QueueItem, type QueueView as Queue, type Worker } from '@/api'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { errorMessage, stageLabel } from '@/lib/api'

const LANES: Record<string, { label: string; hint: string; icon: typeof Cpu }> = {
  cpu: { label: 'CPU', hint: 'Phụ đề, tách nền, lồng tiếng, ghép video trên máy', icon: Cpu },
  net: { label: 'Mạng / API', hint: 'Bước gọi dịch vụ ngoài (OpenAI…)', icon: Globe },
  gpu: { label: 'GPU', hint: 'Bước cần card đồ hoạ', icon: Zap },
}

/** Seconds since `since` (epoch seconds) as "45s", "3m 05s", "1h 02m". */
function duration(seconds: number) {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}

function useNow() {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [])
  return now
}

const MAX_WORKERS = 16

/** Sets a lane's number of workers; the queue view shows the new count at once. */
function useSetWorkers() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ lane, count }: { lane: string; count: number }) =>
      (await setWorkers({ path: { lane }, body: { count } })).data,
    onMutate: ({ lane, count }) => queryClient.setQueryData<Queue>(['queue'], (view) => view && {
      ...view, lanes: view.lanes.map((l) => (l.name === lane ? { ...l, configured: count } : l)),
    }),
    onSuccess: (view) => queryClient.setQueryData(['queue'], view),
    onError: (error) => {
      toast.error(errorMessage(error))
      void queryClient.invalidateQueries({ queryKey: ['queue'] })
    },
  })
}

function WorkerStepper({ lane }: { lane: Lane }) {
  const set = useSetWorkers()
  const change = (by: number) => set.mutate({ lane: lane.name, count: lane.configured + by })
  return (
    <span className="flex items-center gap-0.5" title="Số worker của lane này (giữ sau khi khởi động lại). Bớt worker: worker đang chạy làm xong bước hiện tại rồi mới tắt.">
      <Button type="button" variant="ghost" size="icon-xs" aria-label="Bớt một worker"
        disabled={lane.configured <= 0} onClick={() => change(-1)}><Minus /></Button>
      <span className="w-4 text-center text-xs font-medium tabular-nums">{lane.configured}</span>
      <Button type="button" variant="ghost" size="icon-xs" aria-label="Thêm một worker"
        disabled={lane.configured >= MAX_WORKERS} onClick={() => change(1)}><Plus /></Button>
    </span>
  )
}

const itemLabel = (item: QueueItem) => `${stageLabel(item.stage)}${item.lang ? ` · ${item.lang}` : ''}`

function WorkerDots({ lane }: { lane: Lane }) {
  const busy = new Set(lane.running.map((i) => i.pid))
  if (!lane.workers) {
    return <span className="text-xs text-muted-foreground">{lane.running.length} đang chạy · {lane.configured} worker theo cài đặt</span>
  }
  const state = (w: Worker) => (w.retiring ? 'retiring' : !w.alive ? 'dead' : busy.has(w.pid) ? 'busy' : 'idle')
  const busyCount = lane.workers.filter((w) => state(w) === 'busy').length
  const working = lane.workers.filter((w) => !w.retiring).length
  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground">
      <span className="flex gap-1">
        {lane.workers.map((w, i) => (
          <span key={w.pid ?? i} title={`Worker ${w.pid ?? '?'}: ${{ busy: 'đang chạy', idle: 'rảnh', dead: 'đang khởi động lại', retiring: 'làm xong bước này rồi tắt' }[state(w)]}`}
            className={cn('size-2.5 rounded-full', {
              busy: 'bg-sky-500 animate-pulse', idle: 'bg-muted-foreground/30', dead: 'bg-red-500',
              retiring: 'bg-amber-400 animate-pulse',
            }[state(w)])} />
        ))}
      </span>
      <span className="tabular-nums">{busyCount}/{working} worker bận</span>
    </span>
  )
}

function Row({ item, index, now, onOpen }: { item: QueueItem; index?: number; now: number; onOpen: (jobId: string) => void }) {
  const running = item.status === 'running'
  const percent = running && item.total ? Math.round((100 * (item.done ?? 0)) / item.total) : null
  return (
    <li>
      <button type="button" onClick={() => onOpen(item.job_id)}
        className="grid w-full gap-1.5 rounded-lg px-3 py-2 text-left transition-colors hover:bg-accent/60">
        <div className="flex items-baseline gap-3">
          {index !== undefined
            ? <span className="w-5 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{index}</span>
            : <Loader2 className="size-3.5 shrink-0 self-center animate-spin text-sky-600 dark:text-sky-400" />}
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.title}</span>
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums"
            title={running ? 'Thời gian đã chạy' : 'Thời gian đã chờ'}>
            {duration(now - (running ? item.started_at ?? item.updated_at : item.updated_at))}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pl-8 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">{itemLabel(item)}</span>
          <span>· {item.driver}</span>
          {running && item.pid && <span title="Process worker">· pid {item.pid}</span>}
          {item.retries > 0 && <span className="text-amber-700 dark:text-amber-300">· thử lại lần {item.retries}</span>}
          {item.cancel_requested && <span className="text-amber-700 dark:text-amber-300">· đang dừng…</span>}
          {percent !== null && <span className="ml-auto tabular-nums">{item.done}/{item.total} · {percent}%</span>}
        </div>
        {running && (
          <div className="pl-8">
            {percent !== null ? <Progress value={percent} /> : <div className="h-1 w-full overflow-hidden rounded-full bg-muted"><div className="h-full w-1/3 animate-pulse rounded-full bg-sky-400/60" /></div>}
          </div>
        )}
      </button>
    </li>
  )
}

function LaneCard({ lane, now, onOpen }: { lane: Lane; now: number; onOpen: (jobId: string) => void }) {
  const meta = LANES[lane.name] ?? { label: lane.name, hint: '', icon: Cpu }
  const Icon = meta.icon
  const noWorker = lane.queued.length > 0 && lane.configured === 0
  return (
    <section className="rounded-xl border bg-card">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3">
        <Icon className="size-4 text-muted-foreground" />
        <h3 className="font-medium" title={meta.hint}>{meta.label}</h3>
        <span className="text-xs text-muted-foreground tabular-nums">{lane.running.length} chạy · {lane.queued.length} chờ</span>
        <span className="ml-auto flex items-center gap-3">
          <WorkerDots lane={lane} />
          {lane.scalable && <WorkerStepper lane={lane} />}
        </span>
      </header>
      {noWorker && (
        <p className="flex items-start gap-2 border-b bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <AlertTriangle className="mt-px size-3.5 shrink-0" />
          {lane.scalable
            ? <>Không có worker nào cho lane này nên các bước dưới sẽ chờ mãi. Bấm <Plus className="inline size-3" /> để thêm.</>
            : <>Không có worker nào cho lane này nên các bước dưới sẽ chờ mãi. Đặt DICH_WORKERS_{lane.name.toUpperCase()}=1 rồi chạy lại server, hoặc chạy thêm <code>dich worker --lanes {lane.name}</code>.</>}
        </p>
      )}
      <div className="grid gap-1 p-2">
        {lane.running.length === 0 && lane.queued.length === 0 && (
          <p className="px-3 py-2 text-sm text-muted-foreground">Trống</p>
        )}
        {lane.running.length > 0 && (
          <ul className="grid gap-0.5">{lane.running.map((item) => <Row key={item.id} item={item} now={now} onOpen={onOpen} />)}</ul>
        )}
        {lane.queued.length > 0 && (
          <>
            {lane.running.length > 0 && <div className="mx-3 my-1 h-px bg-border" />}
            <ul className="grid gap-0.5">
              {lane.queued.map((item, i) => <Row key={item.id} item={item} index={i + 1} now={now} onOpen={onOpen} />)}
            </ul>
          </>
        )}
      </div>
    </section>
  )
}

function OffLane({ lane }: { lane: Lane }) {
  const set = useSetWorkers()
  const meta = LANES[lane.name] ?? { label: lane.name, hint: '', icon: Cpu }
  const Icon = meta.icon
  return (
    <p className="flex items-center gap-3 rounded-xl border border-dashed px-4 py-2 text-sm text-muted-foreground">
      <Icon className="size-4" />
      <span><span className="font-medium text-foreground/80">{meta.label}</span> · tắt — {meta.hint}</span>
      <Button type="button" variant="outline" size="xs" className="ml-auto"
        onClick={() => set.mutate({ lane: lane.name, count: 1 })}><Plus /> Bật 1 worker</Button>
    </p>
  )
}

/** What each lane's workers are doing and what waits for them; polled while shown. */
export function QueueView({ onOpen }: { onOpen: (jobId: string) => void }) {
  const queue = useQuery({
    queryKey: ['queue'],
    queryFn: async () => (await getQueue()).data,
    refetchInterval: 1500,
  })
  const now = useNow()
  if (queue.error) return <p className="text-sm text-destructive">{errorMessage(queue.error)}</p>
  if (!queue.data) return <Loader2 className="animate-spin text-muted-foreground" />
  const { lanes, blocked, waiting_review } = queue.data
  // a lane with no worker and nothing to do (GPU off by default) is noise: one line to turn it on
  const shown = lanes.filter((l) => l.running.length || l.queued.length || l.configured || l.workers?.length)
  const off = lanes.filter((l) => l.scalable && !shown.includes(l))
  const running = lanes.reduce((n, l) => n + l.running.length, 0)
  const queued = lanes.reduce((n, l) => n + l.queued.length, 0)
  return (
    <div className="grid gap-4">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground tabular-nums">
        <span>{running} đang chạy</span>
        <span>· {queued} đang chờ worker</span>
        {blocked > 0 && <span title="Các bước chờ bước trước của cùng việc xong">· {blocked} chờ bước trước</span>}
        {waiting_review > 0 && <span className="text-violet-700 dark:text-violet-300">· {waiting_review} chờ bạn duyệt</span>}
        <span className="ml-auto flex items-center gap-1 text-xs"><Clock className="size-3.5" /> cập nhật mỗi 1,5s · bấm một dòng để mở việc</span>
      </p>
      {shown.map((lane) => <LaneCard key={lane.name} lane={lane} now={now} onOpen={onOpen} />)}
      {off.map((lane) => <OffLane key={lane.name} lane={lane} />)}
    </div>
  )
}
