import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, FileDown, Loader2, Maximize2, Network } from 'lucide-react'
import { usePlayer } from '@videojs/react/video'
import { toast } from 'sonner'
import { getMindmap, makeMindmap, type Job, type MindmapNode } from '@/api'
import { Button } from '@/components/ui/button'
import { downloadUrl, errorMessage, slotArtifact, STATUS_LABELS } from '@/lib/api'
import { startDownload } from '@/lib/bulkDownloads'
import { putJob } from '@/lib/jobs'
import { clock, playingPath } from '@/lib/mindmap'
import { cn } from '@/lib/utils'

const ACTIVE = ['blocked', 'queued', 'running']
// React Flow: loaded at the first "Phóng to"
const MindmapMap = lazy(() => import('@/components/MindmapMap'))

/** The job's mindmap, once made: the column's outline and the marks "Phần" on the time bar share it. */
export function useMindmap(job?: Job) {
  const artifact = job && slotArtifact(job, 'mindmap')
  const mindmap = useQuery({
    queryKey: ['mindmap', job?.id, artifact?.id],
    queryFn: async () => (await getMindmap({ path: { job_id: job!.id } })).data,
    enabled: !!artifact,
    // the job's mindmap made before stays shown while its update is read
    placeholderData: (previous, query) => (query?.queryKey[1] === job?.id ? previous : undefined),
    staleTime: Infinity,
    retry: false,
  })
  return { artifact, mindmap }
}

/** The job's mindmap in the player's column: made with one click ("Tạo mindmap", no dialog), its progress while the
 * task runs, then the outline, shown as soon as it is made (the job is kept current by the event stream). A line
 * clicked: onSeek to the second it starts at. Made before the subtitles were edited (stale), it is still shown, with
 * a light line to make it again ("Cập nhật"): "Cập nhật phía sau" leaves it alone. */
export function MindmapPanel({ job, onSeek }: { job: Job; onSeek: (seconds: number) => void }) {
  const queryClient = useQueryClient()
  const task = job.tasks.find((t) => t.stage === 'mindmap')
  const { artifact, mindmap } = useMindmap(job)
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

  if (mindmap.data) {
    const updating = task && ACTIVE.includes(task.status)
    // stale, or its update failed or was stopped: the mindmap shown is the one made before
    const again = task && ['stale', 'failed', 'cancelled'].includes(task.status)
    return (
      <>
        {(updating || again) && (
          <p className="flex items-center gap-1.5 border-b border-white/10 px-3 py-1.5 text-xs text-white/60">
            {updating ? <><Loader2 className="size-3 animate-spin" /> Đang cập nhật mindmap…</> : <>
              <span className={cn(task.status !== 'stale' && 'text-red-300')}>
                {task.status === 'stale' ? 'Tạo từ phụ đề trước khi sửa' : 'Chưa cập nhật được'}
              </span> ·
              <button type="button" disabled={making} onClick={make} title={task.error || 'Tạo lại mindmap từ phụ đề đã sửa'}
                className="text-white/90 underline-offset-2 hover:underline disabled:opacity-50">
                {task.status === 'stale' ? 'Cập nhật' : 'Thử lại'}
              </button>
            </>}
          </p>
        )}
        <Outline root={mindmap.data.root} onSeek={onSeek} onExport={artifact && (() => startDownload(downloadUrl(artifact.id)))} />
      </>
    )
  }
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
      {failed && <p className={`max-w-56 text-xs ${task.status === 'skipped' ? 'text-amber-200/80' : 'text-red-300'}`}>{task.error || task.message || STATUS_LABELS[task.status]}</p>}
      <Button size="sm" disabled={making || (task?.status === 'skipped' && !task.current_attempt)} onClick={make}
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
 * folds and unfolds by its chevron, the note shown on hover. The line playing is marked and scrolled to: the deepest
 * node playing that is shown (its part when the part is folded). onExport: downloads it as <name>.mindmap.md. */
function Outline({ root, onSeek, onExport }: { root: MindmapNode; onSeek: (seconds: number) => void; onExport?: () => void }) {
  const [folded, setFolded] = useState<Set<string>>(new Set())
  // re-rendered when the line playing changes, not at every second
  const playing = usePlayer(useCallback((state) => {
    const path = playingPath(root, state.currentTime)
    const shown = path.findIndex((node) => folded.has(node.id)) + 1 || path.length
    return path[shown - 1]?.id
  }, [root, folded]))
  const line = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    line.current?.scrollIntoView({ block: 'nearest' })
  }, [playing])
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
            ref={node.id === playing ? line : undefined} aria-current={node.id === playing || undefined}
            className={cn('grid min-w-0 flex-1 gap-0.5 rounded px-1.5 py-1 text-left text-sm hover:bg-white/10',
              node.id === playing && 'bg-white/15')}>
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
  const [zoomed, setZoomed] = useState(false)
  const close = useCallback(() => setZoomed(false), [])
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-2 pe-2 ps-1">
      <div className="flex items-start gap-1">
        <button type="button" onClick={() => onSeek(root.start)}
          className="grid min-w-0 flex-1 gap-1 rounded px-2.5 py-1.5 text-left hover:bg-white/10">
          <span className="font-medium">{root.label}</span>
          {root.note && <span className="text-xs text-white/60">{root.note}</span>}
        </button>
        {onExport && (
          <Button size="icon-sm" variant="ghost" className="shrink-0 text-white/70 hover:bg-white/10 hover:text-white"
            title="Xuất Markdown: tải về dàn ý có mốc thời gian (.mindmap.md)" aria-label="Xuất Markdown" onClick={onExport}>
            <FileDown />
          </Button>
        )}
        <Button size="icon-sm" variant="ghost" className="shrink-0 text-white/70 hover:bg-white/10 hover:text-white"
          title="Phóng to: sơ đồ tư duy toàn màn hình" aria-label="Phóng to" onClick={() => setZoomed(true)}>
          <Maximize2 />
        </Button>
      </div>
      {zoomed && (
        <Suspense fallback={<div className="fixed inset-0 z-50 grid place-items-center bg-black/60"><Loader2 className="animate-spin" /></div>}>
          <MindmapMap root={root} onSeek={onSeek} onClose={close} onExport={onExport} />
        </Suspense>
      )}
      <ul className="mt-1">{rows(root.children ?? [], 0)}</ul>
    </div>
  )
}
