// PROTOTYPE — throwaway (vt mindmap design). Question: in the "Phóng to" overlay, should the mindmap fan out to one side
// (A: left → right, sections top → bottom in time order) or to both sides (B: classic, clockwise)? Fake data, no API.
// Open http://localhost:5173/?prototype=mindmap&variant=A (or B) with `npm run dev`.
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import {
  Background, Controls, Handle, NodeToolbar, Position, ReactFlow, type Edge, type Node, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { hierarchy, tree, type HierarchyPointNode } from 'd3-hierarchy'
import { ChevronRight, Download, Pause, Play, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { PrototypeSwitcher, useVariant, type Variant } from '@/components/PrototypeSwitcher'
import { cn } from '@/lib/utils'

type MNode = { id: string; label: string; note?: string; start: number; end?: number; children?: MNode[] }

const DURATION = 2780
const MINDMAP: MNode = {
  id: 'root', label: 'Rust: an toàn bộ nhớ không cần GC', start: 0, end: DURATION,
  note: 'Vì sao ownership và borrow checker thay được garbage collector, và cái giá phải trả khi viết code.',
  children: [
    { id: 's1', label: 'Vấn đề của quản lý bộ nhớ', start: 0, end: 312, children: [
      { id: 's1a', label: 'Lỗi use-after-free và double free', start: 48, note: '70% lỗ hổng bảo mật của Chrome đến từ lỗi bộ nhớ.' },
      { id: 's1b', label: 'GC: an toàn nhưng có độ trễ', start: 161 },
      { id: 's1c', label: 'Rust chọn con đường thứ ba', start: 268 },
    ] },
    { id: 's2', label: 'Ownership: mỗi giá trị một chủ', start: 312, end: 905, children: [
      { id: 's2a', label: 'Ba quy tắc ownership', start: 330, note: 'Một chủ duy nhất; ra khỏi scope thì giải phóng; gán là chuyển quyền.', children: [
        { id: 's2a1', label: 'Move khi gán biến', start: 402 },
        { id: 's2a2', label: 'Drop khi ra khỏi scope', start: 471 },
      ] },
      { id: 's2b', label: 'Clone và Copy khác nhau', start: 560 },
      { id: 's2c', label: 'Ví dụ: String và &str', start: 744, note: 'Demo code: truyền String vào hàm làm mất quyền sở hữu.' },
    ] },
    { id: 's3', label: 'Borrowing và tham chiếu', start: 905, end: 1490, children: [
      { id: 's3a', label: 'Nhiều &T hoặc một &mut T', start: 940, note: 'Quy tắc cốt lõi chống data race ngay lúc biên dịch.' },
      { id: 's3b', label: 'Borrow checker báo lỗi thế nào', start: 1105, children: [
        { id: 's3b1', label: 'Đọc thông báo lỗi E0502', start: 1160 },
        { id: 's3b2', label: 'Sửa bằng cách thu hẹp scope', start: 1262 },
      ] },
      { id: 's3c', label: 'Non-lexical lifetimes (NLL)', start: 1380 },
    ] },
    { id: 's4', label: 'Lifetimes', start: 1490, end: 2010, children: [
      { id: 's4a', label: "Chú thích 'a nghĩa là gì", start: 1522 },
      { id: 's4b', label: 'Quy tắc lifetime elision', start: 1690, note: 'Ba quy tắc giúp phần lớn hàm không phải viết lifetime.' },
      { id: 's4c', label: "'static và khi nào nên dùng", start: 1860 },
    ] },
    { id: 's5', label: 'Smart pointer khi cần chia sẻ', start: 2010, end: 2475, children: [
      { id: 's5a', label: 'Box, Rc, Arc', start: 2040 },
      { id: 's5b', label: 'RefCell: kiểm tra lúc chạy', start: 2210, note: 'Đổi lỗi biên dịch lấy panic lúc chạy nếu vi phạm.' },
      { id: 's5c', label: 'Tránh vòng tham chiếu bằng Weak', start: 2362 },
    ] },
    { id: 's6', label: 'Tổng kết và lời khuyên', start: 2475, end: DURATION, children: [
      { id: 's6a', label: 'Đánh đổi: học khó, chạy nhanh', start: 2490 },
      { id: 's6b', label: 'Tài liệu nên đọc tiếp', start: 2650 },
    ] },
  ],
}

const SIZE = [[260, 84], [250, 72], [270, 40], [240, 36]] as const   // [width, height] by depth
const GAP = 72
const HUES = [255, 160, 40, 330, 200, 95, 10, 285]
const color = (i: number) => `oklch(0.66 0.16 ${HUES[i % HUES.length]})`

const clock = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = Math.floor(s % 60)
  const mm = String(m).padStart(h ? 2 : 1, '0'), ss = String(sec).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** Ids on the path playing at `t`: the section, then at each level the last child that started. */
function activePath(t: number) {
  const ids = new Set<string>()
  let level = MINDMAP.children ?? []
  while (level.length) {
    const hit = [...level].reverse().find((n) => n.start <= t)
    if (!hit) break
    ids.add(hit.id)
    level = hit.children ?? []
  }
  return ids
}

type Side = 'right' | 'left'
type Data = {
  node: MNode; depth: number; side: Side; color: string; index?: number; active: boolean; collapsed: boolean
  onToggle: (id: string) => void; onSeek: (n: MNode) => void
}

/** One side of the map: the sections given, top → bottom, positioned as columns off the root. */
function layoutSide(sections: MNode[], side: Side, collapsed: Set<string>) {
  const root = hierarchy<MNode>({ id: '__', label: '', start: 0, children: sections },
    (n) => (collapsed.has(n.id) ? undefined : n.children))
  const height = (n: HierarchyPointNode<MNode>) => SIZE[Math.min(n.depth, 3)][1]
  const laid = tree<MNode>().nodeSize([1, 1])
    .separation((a, b) => (height(a as never) + height(b as never)) / 2 + (a.parent === b.parent ? 14 : 30))(root)
  const all = laid.descendants().filter((n) => n.depth > 0)
  const mid = (Math.min(...all.map((n) => n.x)) + Math.max(...all.map((n) => n.x))) / 2
  return all.map((n) => {
    let left = SIZE[0][0] / 2 + GAP
    for (let d = 1; d < n.depth; d++) left += SIZE[d][0] + GAP
    const [w, h] = SIZE[Math.min(n.depth, 3)]
    const x = side === 'right' ? left : -left - w
    return { n, x, y: n.x - mid - h / 2 }
  })
}

function build(variant: string, collapsed: Set<string>, active: Set<string>, onToggle: Data['onToggle'], onSeek: Data['onSeek']) {
  const sections = MINDMAP.children ?? []
  const sectionOf = new Map<string, number>()
  sections.forEach((s, i) => {
    const walk = (n: MNode) => { sectionOf.set(n.id, i); n.children?.forEach(walk) }
    walk(s)
  })
  // B: clockwise, the first half down the right, the rest up the left
  const half = Math.ceil(sections.length / 2)
  const sides: [MNode[], Side][] = variant === 'B'
    ? [[sections.slice(0, half), 'right'], [sections.slice(half).reverse(), 'left']]
    : [[sections, 'right']]
  const nodes: Node<Data>[] = [{
    id: 'root', type: 'mind', position: { x: -SIZE[0][0] / 2, y: -SIZE[0][1] / 2 },
    data: { node: MINDMAP, depth: 0, side: 'right', color: 'var(--primary)', active: false, collapsed: false, onToggle, onSeek },
  }]
  const edges: Edge[] = []
  for (const [list, side] of sides) {
    for (const { n, x, y } of layoutSide(list, side, collapsed)) {
      const i = sectionOf.get(n.data.id)!
      nodes.push({
        id: n.data.id, type: 'mind', position: { x, y },
        data: {
          node: n.data, depth: n.depth, side, color: color(i), index: n.depth === 1 ? i + 1 : undefined,
          active: active.has(n.data.id), collapsed: collapsed.has(n.data.id), onToggle, onSeek,
        },
      })
      const parent = n.depth === 1 ? 'root' : n.parent!.data.id
      edges.push({
        id: `${parent}-${n.data.id}`, source: parent, target: n.data.id,
        sourceHandle: side === 'right' ? 'r' : 'l', targetHandle: side === 'right' ? 'tl' : 'tr',
        style: { stroke: color(i), strokeWidth: n.depth === 1 ? 3 : n.depth === 2 ? 2 : 1.5, opacity: active.has(n.data.id) ? 1 : 0.55 },
      })
    }
  }
  return { nodes, edges }
}

const hidden = '!size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent'

function MindNode({ data, selected }: NodeProps<Node<Data>>) {
  const { node, depth, side, active, collapsed } = data
  const [hover, setHover] = useState(false)
  const [w, h] = SIZE[Math.min(depth, 3)]
  const kids = node.children?.length ?? 0
  const toggle = kids > 0 && depth > 0 && (
    <button
      onClick={(e) => { e.stopPropagation(); data.onToggle(node.id) }}
      className={cn('nodrag absolute top-1/2 flex h-5 min-w-5 -translate-y-1/2 items-center justify-center rounded-full border bg-background px-1 text-[10px] font-semibold text-muted-foreground shadow-sm hover:text-foreground',
        side === 'right' ? '-right-2.5' : '-left-2.5')}>
      {collapsed ? `+${kids}` : <ChevronRight className={cn('size-3', side === 'left' && 'rotate-180')} />}
    </button>
  )
  const handles = (
    <>
      <Handle type="target" id="tl" position={Position.Left} className={hidden} />
      <Handle type="target" id="tr" position={Position.Right} className={hidden} />
      <Handle type="source" id="r" position={Position.Right} className={hidden} />
      <Handle type="source" id="l" position={Position.Left} className={hidden} />
    </>
  )
  const note = node.note && (
    <NodeToolbar isVisible={hover || selected} position={Position.Bottom}
      className="max-w-72 rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      {node.note}
    </NodeToolbar>
  )
  const events = { onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false), onClick: () => data.onSeek(node) }

  if (depth === 0) return (
    <div {...events} style={{ width: w, height: h }}
      className="flex cursor-pointer flex-col justify-center rounded-xl bg-primary px-4 text-primary-foreground shadow-lg">
      {handles}{note}
      <div className="text-[15px] font-semibold leading-tight">{node.label}</div>
      <div className="mt-1 text-xs opacity-70">{clock(DURATION)} · {MINDMAP.children?.length} phần</div>
    </div>
  )

  if (depth === 1) return (
    <div {...events} style={{ width: w, height: h, '--c': data.color } as CSSProperties}
      className={cn('relative flex cursor-pointer flex-col justify-center gap-1 rounded-lg border bg-card px-3 shadow-sm transition-shadow hover:shadow-md',
        side === 'right' ? 'border-l-4 border-l-(--c)' : 'border-r-4 border-r-(--c)',
        active && 'ring-2 ring-(--c) ring-offset-2 ring-offset-background')}>
      {handles}{note}{toggle}
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="flex size-4 items-center justify-center rounded-full bg-(--c) text-[10px] font-bold text-white">{data.index}</span>
        <span className="tabular-nums">{clock(node.start)}–{clock(node.end ?? DURATION)}</span>
        {active && <span className="ml-auto flex items-center gap-1 font-medium text-(--c)"><span className="size-1.5 animate-pulse rounded-full bg-(--c)" />Đang phát</span>}
      </div>
      <div className="truncate text-sm font-semibold">{node.label}</div>
      {/* where the section sits in the whole video */}
      <div className="relative h-1 rounded-full bg-muted">
        <div className="absolute inset-y-0 rounded-full bg-(--c)"
          style={{ left: `${(node.start / DURATION) * 100}%`, width: `${(((node.end ?? DURATION) - node.start) / DURATION) * 100}%` }} />
      </div>
    </div>
  )

  return (
    <div {...events} style={{ width: w, height: h, '--c': data.color } as CSSProperties}
      className={cn('relative flex cursor-pointer items-center gap-2 rounded-full border bg-card px-3 shadow-xs transition-colors hover:bg-accent',
        active && 'border-(--c) bg-(--c)/10', depth === 3 && 'bg-muted/50')}>
      {handles}{note}{toggle}
      <span className={cn('truncate', depth === 2 ? 'text-[13px]' : 'text-xs text-muted-foreground')}>{node.label}</span>
      <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">{clock(node.start)}</span>
      {node.note && <span className="size-1.5 shrink-0 rounded-full bg-(--c)/60" title="Có ghi chú" />}
    </div>
  )
}

const nodeTypes = { mind: MindNode }

function markdown(n: MNode = MINDMAP, depth = 0): string {
  const line = depth === 0 ? `# ${n.label}\n\n${n.note ?? ''}\n` : `${'  '.repeat(depth - 1)}- [${clock(n.start)}] ${n.label}${n.note ? ` — ${n.note}` : ''}`
  return [line, ...(n.children ?? []).map((c) => markdown(c, depth + 1))].join('\n')
}

const VARIANTS: Variant[] = [
  { key: 'A', name: 'Một phía, trái → phải theo thời gian' },
  { key: 'B', name: 'Hai phía, chiều kim đồng hồ' },
]

export default function MindmapPrototype() {
  const [variant, setVariant] = useState(useVariant(VARIANTS))
  const [t, setT] = useState(1180)
  const [playing, setPlaying] = useState(false)
  const [collapsed, setCollapsed] = useState(() => new Set<string>())
  const [lastSeek, setLastSeek] = useState<string>()
  const dark = document.documentElement.classList.contains('dark')

  useEffect(() => {   // playback simulated at 20×
    if (!playing) return
    const id = setInterval(() => setT((v) => Math.min(DURATION, v + 1)), 50)
    return () => clearInterval(id)
  }, [playing])

  const active = useMemo(() => activePath(t), [t])
  const { nodes, edges } = useMemo(() => build(variant.key, collapsed, active,
    (id) => setCollapsed((s) => { const n = new Set(s); if (!n.delete(id)) n.add(id); return n }),
    (n) => { setT(n.start); setLastSeek(`${n.label} → ${clock(n.start)}`) }), [variant.key, collapsed, active])

  return (
    <div className="fixed inset-0 flex flex-col bg-background text-foreground">
      <header className="flex items-center gap-3 border-b px-4 py-2">
        <span className="text-sm font-semibold">Mindmap</span>
        <span className="truncate text-sm text-muted-foreground">{MINDMAP.label}</span>
        <div className="ml-auto flex gap-2">
          <Button variant="outline" size="sm" onClick={() => {
            navigator.clipboard.writeText(markdown())
            toast.success('Đã chép Markdown (prototype: thật sẽ tải <tên>.mindmap.md)')
          }}><Download />Xuất Markdown</Button>
          <Button variant="ghost" size="icon-sm" onClick={() => toast('Đóng → về trình phát')}><X /></Button>
        </div>
      </header>
      <div className="relative flex-1">
        <ReactFlow key={variant.key} nodes={nodes} edges={edges} nodeTypes={nodeTypes} colorMode={dark ? 'dark' : 'light'}
          fitView fitViewOptions={{ padding: 0.15 }} minZoom={0.2} nodesDraggable={false} nodesConnectable={false}
          proOptions={{ hideAttribution: true }} style={{ '--xy-background-color': 'transparent' } as CSSProperties}>
          <Background gap={24} size={1} />
          <Controls showInteractive={false} position="top-right" />
        </ReactFlow>

        {/* fake player state: what the overlay would get from the video */}
        <div className="absolute bottom-16 left-4 w-80 rounded-lg border bg-card/95 p-3 text-xs shadow-lg backdrop-blur">
          <div className="mb-2 flex items-center gap-2">
            <Button size="icon-sm" variant="secondary" onClick={() => setPlaying((p) => !p)}>{playing ? <Pause /> : <Play />}</Button>
            <span className="tabular-nums">{clock(t)} / {clock(DURATION)}</span>
            <span className="ml-auto text-muted-foreground">giả lập video (×20)</span>
          </div>
          <input type="range" min={0} max={DURATION} value={t} onChange={(e) => setT(+e.target.value)} className="w-full" />
          <div className="mt-2 space-y-0.5 text-muted-foreground">
            <div>Đang phát: <span className="text-foreground">{[...active].map((id) => find(id)?.label).join(' › ') || '—'}</span></div>
            <div>Bấm gần nhất: <span className="text-foreground">{lastSeek ?? '—'}</span></div>
            <div>Thu gọn: {collapsed.size ? [...collapsed].join(', ') : '—'}</div>
          </div>
        </div>
      </div>
      <PrototypeSwitcher variants={VARIANTS} current={variant} onChange={setVariant} />
    </div>
  )
}

function find(id: string, n: MNode = MINDMAP): MNode | undefined {
  if (n.id === id) return n
  for (const c of n.children ?? []) { const hit = find(id, c); if (hit) return hit }
}
