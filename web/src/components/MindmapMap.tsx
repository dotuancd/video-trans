import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import {
  Background, Controls, Handle, NodeToolbar, Position, ReactFlow, ReactFlowProvider, useReactFlow, type Edge, type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ChevronRight, FileDown, X } from 'lucide-react'
import { usePlayer } from '@videojs/react/video'
import type { MindmapNode } from '@/api'
import { Button } from '@/components/ui/button'
import { loadSaved, save } from '@/lib/api'
import { clock, partEnd, playingPath } from '@/lib/mindmap'
import { layoutMindmap, pickView, type PlacedNode, type View } from '@/lib/mindmapLayout'
import { cn } from '@/lib/utils'

const HUES = [255, 160, 40, 330, 200, 95, 10, 285]
/** A part's own colour. */
const color = (part: number) => `oklch(0.66 0.16 ${HUES[part % HUES.length]})`
const VIEWS: [View, string, string][] = [
  ['B', 'Hai phía', 'Hai phía, theo chiều kim đồng hồ'],
  ['A', 'Một phía', 'Một phía, từ trái sang phải theo thời gian'],
]
const FIT = { padding: 0.15 }

type Data = PlacedNode & {
  playing: boolean
  folded: boolean
  /** The video's length and the part's span: where the part sits in it. */
  duration: number
  end?: number
  onToggle: (id: string) => void
  onSeek: (seconds: number) => void
}

/** The mindmap as a map fanning out from the title, over the whole screen (the fullscreen video's too). Laid out both
 * sides (B) or one side (A), the view picked kept; points fold and unfold, zoom and drag, the note on hover. The nodes
 * playing are marked; a node clicked: onSeek to its second, then closed. onExport: "Xuất Markdown" in the header. */
export default function MindmapMap({ root, onSeek, onClose, onExport }: {
  root: MindmapNode
  onSeek: (seconds: number) => void
  onClose: () => void
  onExport?: () => void
}) {
  const seek = useCallback((seconds: number) => {
    onSeek(seconds)
    onClose()
  }, [onSeek, onClose])
  // Esc closes the map alone, not the player (it leaves a prevented Esc alone)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      e.preventDefault()
      onClose()
    }
    addEventListener('keydown', onKey, true)
    return () => removeEventListener('keydown', onKey, true)
  }, [onClose])
  return createPortal(
    <div role="dialog" aria-modal aria-label="Mindmap"
      className="fixed inset-0 z-50 flex flex-col bg-background text-foreground">
      <ReactFlowProvider>
        <MapView root={root} onSeek={seek} onClose={onClose} onExport={onExport} />
      </ReactFlowProvider>
    </div>,
    document.fullscreenElement ?? document.body,
  )
}

function MapView({ root, onSeek, onClose, onExport }: {
  root: MindmapNode
  onSeek: (seconds: number) => void
  onClose: () => void
  onExport?: () => void
}) {
  const parts = root.children ?? []
  const [saved, setSaved] = useState(() => loadSaved<{ view?: View }>('mindmap', {}).view)
  const [screen] = useState(() => ({ width: innerWidth, height: innerHeight }))
  const view = pickView(saved, parts.length, screen)
  const pick = (view: View) => {
    setSaved(view)
    save('mindmap', { view })
  }
  const [folded, setFolded] = useState<Set<string>>(new Set())
  const onToggle = useCallback((id: string) => setFolded((ids) => {
    const next = new Set(ids)
    if (!next.delete(id)) next.add(id)
    return next
  }), [])
  // re-rendered when the nodes playing change, not at every second
  const playing = usePlayer(useCallback((state) => playingPath(root, state.currentTime).map((n) => n.id).join(' '), [root]))
  const duration = root.end ?? (parts.length ? partEnd(root, parts.length - 1) : root.start)

  const { nodes, edges } = useMemo(() => {
    const on = new Set(playing.split(' '))
    const { nodes, links } = layoutMindmap(root, folded, view)
    return {
      nodes: nodes.map((placed): Node<Data> => ({
        id: placed.node.id, type: 'mind', position: { x: placed.x, y: placed.y },
        data: {
          ...placed, playing: on.has(placed.node.id), folded: folded.has(placed.node.id), duration,
          end: placed.depth === 1 ? partEnd(root, placed.part!) : undefined, onToggle, onSeek,
        },
      })),
      edges: links.map((link): Edge => ({
        id: `${link.source}-${link.target}`, source: link.source, target: link.target,
        sourceHandle: link.side, targetHandle: link.side === 'right' ? 'in-left' : 'in-right',
        style: {
          stroke: color(link.part), strokeWidth: [3, 3, 2, 1.5][Math.min(link.depth, 3)],
          opacity: on.has(link.target) ? 1 : 0.55,
        },
      })),
    }
  }, [root, folded, view, playing, duration, onToggle, onSeek])

  // a view picked: the map fitted to the screen again, moving there
  const flow = useReactFlow()
  const shown = useRef(view)
  useEffect(() => {
    if (shown.current === view) return
    shown.current = view
    void flow.fitView({ ...FIT, duration: 400 })
  }, [flow, view])

  const dark = document.documentElement.classList.contains('dark')
  return (
    <>
      <header className="flex items-center gap-3 border-b px-4 py-2">
        <span className="text-sm font-semibold" title="Sơ đồ tư duy">Mindmap</span>
        <span className="min-w-0 truncate text-sm text-muted-foreground">{root.label}</span>
        {parts.length > 1 && (
          <div role="group" aria-label="Kiểu xem" className="ms-auto flex rounded-md border p-0.5">
            {VIEWS.map(([key, label, hint]) => (
              <button key={key} type="button" title={hint} aria-pressed={view === key} onClick={() => pick(key)}
                className={cn('rounded px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground',
                  view === key && 'bg-accent font-medium text-foreground')}>
                {label}
              </button>
            ))}
          </div>
        )}
        {onExport && (
          <Button variant="ghost" size="sm" className={cn(parts.length <= 1 && 'ms-auto')} onClick={onExport}
            title="Tải về dàn ý có mốc thời gian (.mindmap.md)">
            <FileDown /> Xuất Markdown
          </Button>
        )}
        <Button variant="ghost" size="icon-sm" className={cn(parts.length <= 1 && !onExport && 'ms-auto')} title="Đóng (Esc)"
          aria-label="Đóng" onClick={onClose}>
          <X />
        </Button>
      </header>
      <div className="relative flex-1">
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} colorMode={dark ? 'dark' : 'light'}
          fitView fitViewOptions={FIT} minZoom={0.2} nodesDraggable={false} nodesConnectable={false}
          elementsSelectable={false} proOptions={{ hideAttribution: true }}
          style={{ '--xy-background-color': 'transparent' } as CSSProperties}>
          <Background gap={24} size={1} />
          <Controls showInteractive={false} position="top-right" />
        </ReactFlow>
      </div>
    </>
  )
}

const HIDDEN = '!size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent'

/** A node: the title, a part (its number, span and place in the video) or a point; its note on hover. */
function MindNode({ data }: NodeProps<Node<Data>>) {
  const { node, depth, side, part, playing, width, height } = data
  const [hover, setHover] = useState(false)
  const kids = node.children?.length ?? 0
  const style = { width, height, '--c': part === undefined ? undefined : color(part) } as CSSProperties
  const seek = { onClick: () => data.onSeek(node.start), title: node.label }
  return (
    <div className="relative" style={style} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <Handle type="target" id="in-left" position={Position.Left} className={HIDDEN} />
      <Handle type="target" id="in-right" position={Position.Right} className={HIDDEN} />
      <Handle type="source" id="right" position={Position.Right} className={HIDDEN} />
      <Handle type="source" id="left" position={Position.Left} className={HIDDEN} />
      {node.note && (
        <NodeToolbar isVisible={hover} position={Position.Bottom}
          className="max-w-72 rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
          {node.note}
        </NodeToolbar>
      )}

      {depth === 0 ? (
        <button type="button" {...seek}
          className="flex size-full flex-col justify-center rounded-xl bg-primary px-4 text-left text-primary-foreground shadow-lg">
          <span className="line-clamp-2 text-[15px] font-semibold leading-tight">{node.label}</span>
          <span className="mt-1 text-xs opacity-70">{clock(data.duration)} · {node.children?.length ?? 0} phần</span>
        </button>
      ) : depth === 1 ? (
        <button type="button" {...seek} aria-current={playing || undefined}
          className={cn('flex size-full flex-col justify-center gap-1 rounded-lg border bg-card px-3 text-left shadow-sm transition-shadow hover:shadow-md',
            side === 'right' ? 'border-l-4 border-l-(--c)' : 'border-r-4 border-r-(--c)',
            playing && 'ring-2 ring-(--c) ring-offset-2 ring-offset-background')}>
          <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="grid size-4 place-items-center rounded-full bg-(--c) text-[10px] font-bold text-white">{part! + 1}</span>
            <span className="tabular-nums">{clock(node.start)}–{clock(data.end!)}</span>
            {playing && (
              <span className="ms-auto flex items-center gap-1 font-medium text-(--c)">
                <span className="size-1.5 animate-pulse rounded-full bg-(--c)" />Đang phát
              </span>
            )}
          </span>
          <span className="truncate text-sm font-semibold">{node.label}</span>
          {/* where the part sits in the whole video */}
          <span className="relative h-1 rounded-full bg-muted">
            <span className="absolute inset-y-0 rounded-full bg-(--c)" style={{
              left: `${(node.start / (data.duration || 1)) * 100}%`,
              width: `${((data.end! - node.start) / (data.duration || 1)) * 100}%`,
            }} />
          </span>
        </button>
      ) : (
        <button type="button" {...seek} aria-current={playing || undefined}
          className={cn('flex size-full items-center gap-2 rounded-full border bg-card px-3 text-left shadow-xs transition-colors hover:bg-accent',
            depth > 2 && 'bg-muted/50', playing && 'border-(--c) bg-(--c)/10')}>
          <span className={cn('truncate', depth === 2 ? 'text-[13px]' : 'text-xs text-muted-foreground')}>{node.label}</span>
          <span className="ms-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">{clock(node.start)}</span>
          {node.note && <span className="size-1.5 shrink-0 rounded-full bg-(--c)/60" title="Có ghi chú" />}
        </button>
      )}

      {depth > 0 && kids > 0 && (
        <button type="button" aria-label={data.folded ? 'Mở' : 'Thu gọn'} aria-expanded={!data.folded}
          onClick={() => data.onToggle(node.id)}
          className={cn('nodrag absolute top-1/2 flex h-5 min-w-5 -translate-y-1/2 items-center justify-center rounded-full border bg-background px-1 text-[10px] font-semibold text-muted-foreground shadow-sm hover:text-foreground',
            side === 'right' ? '-right-2.5' : '-left-2.5')}>
          {data.folded ? `+${kids}` : <ChevronRight className={cn('size-3', side === 'left' && 'rotate-180')} />}
        </button>
      )}
    </div>
  )
}

const NODE_TYPES = { mind: MindNode }
