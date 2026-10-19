import { useEffect, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Clock, Cpu, Download, Globe, Loader2, Minus, Package, Plus, Trash2, X, Zap } from 'lucide-react'
import {
  deleteBulkDownload, getQueue, setWorkers,
  type BulkDownload, type Lane, type QueueItem, type QueueView as Queue, type Worker,
} from '@/api'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { bulkDownloadUrl, errorMessage, sizeLabel, stageLabel } from '@/lib/api'
import {
  BULK_KEY, BULK_LANE, bulkPercent, isMaking, skippedLabel, startDownload, useBulkDownloads,
} from '@/lib/bulkDownloads'

const LANES: Record<string, { label: string; hint: string; icon: typeof Cpu }> = {
  cpu: { label: 'CPU', hint: 'Phụ đề, tách nền, lồng tiếng, ghép video trên máy', icon: Cpu },
  net: { label: 'Mạng / API', hint: 'Bước gọi dịch vụ ngoài (OpenAI…)', icon: Globe },
  gpu: { label: 'GPU', hint: 'Bước cần card đồ hoạ', icon: Zap },
  [BULK_LANE]: { label: 'Gói tải về', hint: 'Đóng gói zip kết quả của nhiều việc để tải về', icon: Package },
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

/** `pids`: the processes busy on the lane (by default those running its tasks). */
function WorkerDots({ lane, pids = lane.running.map((i) => i.pid) }: { lane: Lane; pids?: (number | null | undefined)[] }) {
  const busy = new Set(pids)
  if (!lane.workers) {
    return <span className="text-xs text-muted-foreground">{pids.length} đang chạy · {lane.configured} worker theo cài đặt</span>
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

/** A lane's card: name, counts, its workers (and +/− when this server runs them), a warning when nothing would do its
 * work, then the body. */
function LaneShell({ lane, counts, pids, stuck, children }: {
  lane: Lane; counts: string; pids?: (number | null | undefined)[]; stuck?: ReactNode; children: ReactNode
}) {
  const meta = LANES[lane.name] ?? { label: lane.name, hint: '', icon: Cpu }
  const Icon = meta.icon
  return (
    <section className="rounded-xl border bg-card">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3">
        <Icon className="size-4 text-muted-foreground" />
        <h3 className="font-medium" title={meta.hint}>{meta.label}</h3>
        <span className="text-xs text-muted-foreground tabular-nums">{counts}</span>
        <span className="ml-auto flex items-center gap-3">
          <WorkerDots lane={lane} pids={pids} />
          {lane.scalable && <WorkerStepper lane={lane} />}
        </span>
      </header>
      {stuck && lane.configured === 0 && (
        <p className="flex items-start gap-2 border-b bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <AlertTriangle className="mt-px size-3.5 shrink-0" />
          <span>
            {stuck} {lane.scalable
              ? <>Bấm <Plus className="inline size-3" /> để thêm.</>
              : <>Đặt DICH_WORKERS_{lane.name.toUpperCase()}=1 rồi chạy lại server, hoặc chạy thêm <code>dich worker --lanes {lane.name}</code>.</>}
          </span>
        </p>
      )}
      <div className="grid gap-1 p-2">{children}</div>
    </section>
  )
}

function LaneCard({ lane, now, onOpen }: { lane: Lane; now: number; onOpen: (jobId: string) => void }) {
  return (
    <LaneShell lane={lane} counts={`${lane.running.length} chạy · ${lane.queued.length} chờ`}
      stuck={lane.queued.length > 0 && 'Không có worker nào cho lane này nên các bước dưới sẽ chờ mãi.'}>
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
    </LaneShell>
  )
}

const BULK_STATUS: Record<BulkDownload['status'], string> = {
  queued: 'Đang chờ', running: 'Đang tạo', ready: 'Sẵn sàng', failed: 'Lỗi', cancelled: 'Đã huỷ',
}

/** Cancels a bulk download waiting or being made, deletes any other (with its zip). */
function useDeleteBulkDownload() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteBulkDownload({ path: { bulk_id: id } }),
    onError: (error) => toast.error(errorMessage(error)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: BULK_KEY }),
  })
}

function BulkRow({ bulk, now }: { bulk: BulkDownload; now: number }) {
  const remove = useDeleteBulkDownload()
  const making = isMaking(bulk)
  const percent = bulkPercent(bulk)
  return (
    <li className="grid gap-1.5 rounded-lg px-3 py-2">
      <div className="flex items-center gap-3">
        {making
          ? <Loader2 className="size-3.5 shrink-0 animate-spin text-sky-600 dark:text-sky-400" />
          : <Package className="size-3.5 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{bulk.name}</span>
        {bulk.status === 'ready' && (
          <Button type="button" variant="outline" size="xs" onClick={() => startDownload(bulkDownloadUrl(bulk.id))}>
            <Download /> Tải về
          </Button>
        )}
        <Button type="button" variant="ghost" size="xs" disabled={remove.isPending || bulk.cancel_requested}
          onClick={() => remove.mutate(bulk.id)}>
          {making ? <><X /> Huỷ</> : <><Trash2 /> Xoá</>}
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pl-6.5 text-xs text-muted-foreground">
        <span className={cn('font-medium text-foreground/80', bulk.status === 'failed' && 'text-destructive')}>
          {BULK_STATUS[bulk.status]}
        </span>
        <span>· {bulk.files} file</span>
        {bulk.size != null && <span className="tabular-nums">· {sizeLabel(bulk.size)}</span>}
        {bulk.status === 'ready' && bulk.expires_at && (
          <span className="tabular-nums" title="Gói bị xoá sau thời gian này; tạo lại gói cùng các file sẽ gia hạn">
            · còn {duration(bulk.expires_at - now)}
          </span>
        )}
        {bulk.cancel_requested && <span className="text-amber-700 dark:text-amber-300">· đang huỷ…</span>}
        {bulk.status === 'running' && !!bulk.total && (
          <span className="ml-auto tabular-nums">{sizeLabel(bulk.done ?? 0)}/{sizeLabel(bulk.total)} · {percent}%</span>
        )}
      </div>
      {bulk.status === 'failed' && bulk.error && <p className="pl-6.5 text-xs text-destructive">{bulk.error}</p>}
      {bulk.skipped.length > 0 && (
        <p className="pl-6.5 text-xs text-amber-700 dark:text-amber-300">{skippedLabel(bulk)}: {bulk.skipped.join(', ')}</p>
      )}
      {bulk.status === 'running' && <div className="pl-6.5"><Progress value={percent} /></div>}
    </li>
  )
}

/** The bulk_download lane: its workers and the bulk downloads ("Gói tải về"), newest first. */
function BulkDownloadsCard({ lane, now }: { lane: Lane; now: number }) {
  const bulks = useBulkDownloads()
  const list = bulks.data ?? []
  const pending = list.filter(isMaking)
  return (
    <LaneShell lane={lane} counts={`${list.length} gói · ${pending.length} đang tạo`}
      pids={pending.filter((b) => b.status === 'running').map((b) => b.pid)}
      stuck={pending.length > 0 && 'Không có worker nào tạo gói nên các gói dưới sẽ chờ mãi.'}>
      {bulks.error && <p className="px-3 py-2 text-sm text-destructive">{errorMessage(bulks.error)}</p>}
      {!bulks.error && list.length === 0 && (
        <p className="px-3 py-2 text-sm text-muted-foreground">Chưa có gói nào. Tick các việc rồi bấm Tải về để tạo.</p>
      )}
      {list.length > 0 && <ul className="grid gap-0.5">{list.map((b) => <BulkRow key={b.id} bulk={b} now={now} />)}</ul>}
    </LaneShell>
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
  const { blocked, waiting_review } = queue.data
  // the bulk_download lane has no tasks: its card lists the bulk downloads instead
  const bulkLane = queue.data.lanes.find((l) => l.name === BULK_LANE)
  const lanes = queue.data.lanes.filter((l) => l !== bulkLane)
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
      {bulkLane && <BulkDownloadsCard lane={bulkLane} now={now} />}
      {off.map((lane) => <OffLane key={lane.name} lane={lane} />)}
    </div>
  )
}
