import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Loader2, Network } from 'lucide-react'
import { toast } from 'sonner'
import { getMindmap, makeMindmap, type Job, type MindmapNode } from '@/api'
import { Button } from '@/components/ui/button'
import { errorMessage, slotArtifact, STATUS_LABELS } from '@/lib/api'
import { putJob } from '@/lib/jobs'
import { cn } from '@/lib/utils'

const ACTIVE = ['blocked', 'queued', 'running']

/** "m:ss", "h:mm:ss" past an hour. */
const clock = (seconds: number) => {
  const s = Math.floor(seconds)
  const [h, m, sec] = [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60]
  return `${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${String(sec).padStart(2, '0')}`
}

/** The job's mindmap in the player's column: made with one click ("Tạo mindmap", no dialog), its progress while the
 * task runs, then the outline, shown as soon as it is made (the job is kept current by the event stream). A line
 * clicked: onSeek to the second it starts at. */
export function MindmapPanel({ job, onSeek }: { job: Job; onSeek: (seconds: number) => void }) {
  const queryClient = useQueryClient()
  const task = job.tasks.find((t) => t.stage === 'mindmap')
  const artifact = slotArtifact(job, 'mindmap')
  const mindmap = useQuery({
    queryKey: ['mindmap', job.id, artifact?.id],
    queryFn: async () => (await getMindmap({ path: { job_id: job.id } })).data,
    enabled: !!artifact,
    staleTime: Infinity,
    retry: false,
  })
  const [making, setMaking] = useState(false)
  const make = async () => {
    setMaking(true)
    try {
      putJob(queryClient, (await makeMindmap({ path: { job_id: job.id } })).data)
    } catch (e) {
      toast.error(errorMessage(e))
    } finally {
      setMaking(false)
    }
  }

  if (mindmap.data) return <Outline root={mindmap.data.root} onSeek={onSeek} />
  if (artifact && !mindmap.error) return <Centered><Loader2 className="animate-spin text-white/60" /></Centered>
  if (task && ACTIVE.includes(task.status)) {
    const share = task.total ? (task.done ?? 0) / task.total : undefined
    return (
      <Centered>
        <Loader2 className="animate-spin text-white/60" />
        <p className="text-sm text-white/80">Đang tạo mindmap… <span className="text-white/50">{STATUS_LABELS[task.status]}</span></p>
        {share !== undefined && (
          <span className="h-1 w-40 overflow-hidden rounded-full bg-white/15">
            <span className="block h-full bg-white/70" style={{ width: `${share * 100}%` }} />
          </span>
        )}
      </Centered>
    )
  }
  const failed = task && ['failed', 'cancelled', 'skipped'].includes(task.status)
  return (
    <Centered>
      <Network className="size-8 text-white/40" />
      <p className="max-w-56 text-sm text-white/70">Tóm tắt video theo từng phần, bấm một dòng để nhảy tới đoạn đó.</p>
      {failed && <p className="max-w-56 text-xs text-red-300">{task.error || task.message || STATUS_LABELS[task.status]}</p>}
      <Button size="sm" disabled={making || task?.status === 'skipped'} onClick={make}
        title="Sơ đồ tư duy: 1 request AI đọc phụ đề gốc, viết bằng ngôn ngữ đích đầu tiên">
        {making && <Loader2 className="animate-spin" />} {failed ? 'Thử lại' : 'Tạo mindmap'}
      </Button>
    </Centered>
  )
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">{children}</div>
}

/** The title and summary, then the parts (numbered, from–to) and their points as an indented list; a node with points
 * folds and unfolds by its chevron, the note shown on hover. */
function Outline({ root, onSeek }: { root: MindmapNode; onSeek: (seconds: number) => void }) {
  const [folded, setFolded] = useState<Set<string>>(new Set())
  const toggle = (id: string) => setFolded((ids) => {
    const next = new Set(ids)
    if (!next.delete(id)) next.add(id)
    return next
  })
  const rows = (nodes: MindmapNode[], depth: number): ReactNode => nodes.map((node, n) => {
    const children = node.children ?? []
    const open = !folded.has(node.id)
    return (
      <li key={node.id}>
        <div className="flex items-start" style={{ paddingInlineStart: `${depth * 0.75}rem` }}>
          <button type="button" aria-label={open ? 'Thu gọn' : 'Mở'} aria-expanded={children.length ? open : undefined}
            disabled={!children.length} onClick={() => toggle(node.id)}
            className="mt-1.5 grid size-5 shrink-0 place-items-center rounded text-white/50 hover:bg-white/10 disabled:invisible">
            <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
          </button>
          <button type="button" title={node.note ?? undefined} onClick={() => onSeek(node.start)}
            className="grid min-w-0 flex-1 gap-0.5 rounded px-1.5 py-1 text-left text-sm hover:bg-white/10">
            <span className={cn(depth === 0 && 'font-medium')}>
              {depth === 0 && <span className="me-1.5 text-white/50">{n + 1}.</span>}
              {node.label}
            </span>
            <span className="text-xs text-white/50 tabular-nums">
              {clock(node.start)}{node.end != null && ` – ${clock(node.end)}`}
            </span>
          </button>
        </div>
        {open && children.length > 0 && <ul>{rows(children, depth + 1)}</ul>}
      </li>
    )
  })
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-2 pe-2 ps-1">
      <button type="button" onClick={() => onSeek(root.start)}
        className="grid w-full gap-1 rounded px-2.5 py-1.5 text-left hover:bg-white/10">
        <span className="font-medium">{root.label}</span>
        {root.note && <span className="text-xs text-white/60">{root.note}</span>}
      </button>
      <ul className="mt-1">{rows(root.children ?? [], 0)}</ul>
    </div>
  )
}
