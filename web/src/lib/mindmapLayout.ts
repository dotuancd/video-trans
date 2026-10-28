import { hierarchy, tree } from 'd3-hierarchy'
import type { MindmapNode } from '@/api'

/** How the map fans out: A one side, left → right; B both sides, clockwise (the first parts down the right, the rest
 * up the left). */
export type View = 'A' | 'B'
export type Side = 'right' | 'left'

/** [width, height] of a node by its depth: the root, a part, a point, a detail. */
export const SIZES = [[260, 84], [250, 72], [270, 40], [240, 36]] as const
/** Between the columns of two depths. */
const COLUMN_GAP = 72
/** Between two parts on a side, between two nodes of one parent, of two parents. */
const PART_GAP = 30
const SIBLING_GAP = 14
const COUSIN_GAP = 30

const sizeOf = (depth: number) => SIZES[Math.min(depth, SIZES.length - 1)]

/** The view shown: the one the user picked, else A when there are few parts (≤ 3, nothing to balance) or the screen is
 * taller than wide, else B. */
export function pickView(saved: View | undefined, parts: number, screen: { width: number; height: number }): View {
  return saved ?? (parts <= 3 || screen.height > screen.width ? 'A' : 'B')
}

/** Where B cuts the parts, in time order: the parts before the cut on the right, the rest on the left, the two sides as
 * near the same height as can be. heights: the parts', a side's being theirs and the gaps between them. At least one part
 * on each side when there are two. */
export function splitAt(heights: number[]): number {
  const side = (from: number, to: number) => heights.slice(from, to).reduce((sum, h) => sum + h + PART_GAP, -PART_GAP)
  let best = heights.length
  let gap = Infinity
  for (let cut = 1; cut < heights.length; cut++) {
    const off = Math.abs(side(0, cut) - side(cut, heights.length))
    if (off < gap) [best, gap] = [cut, off]
  }
  return best
}

export type PlacedNode = {
  node: MindmapNode
  depth: number
  /** The part it is in (0-based), undefined for the root. */
  part?: number
  side: Side
  /** Top left, the root centred on 0, 0. */
  x: number
  y: number
  width: number
  height: number
}
export type Link = { source: string; target: string; part: number; depth: number; side: Side }

/** The map laid out: the nodes shown (those under a folded node left out) and the links from parent to child.
 * folded: the ids of the nodes whose children are hidden. */
export function layoutMindmap(root: MindmapNode, folded: ReadonlySet<string>, view: View): { nodes: PlacedNode[]; links: Link[] } {
  const parts = (root.children ?? []).map((node, part) => layoutPart(node, part, folded))
  const cut = view === 'B' ? splitAt(parts.map((p) => p.height)) : parts.length
  const [width, height] = sizeOf(0)
  const nodes: PlacedNode[] = [{ node: root, depth: 0, side: 'right', x: -width / 2, y: -height / 2, width, height }]
  const links: Link[] = []
  // a side read top → bottom: the right's in time order, the left's from its last part, the clock going up it
  const sides: [typeof parts, Side][] = [[parts.slice(0, cut), 'right'], [parts.slice(cut).reverse(), 'left']]
  for (const [list, side] of sides) {
    let top = -(list.reduce((sum, p) => sum + p.height + PART_GAP, -PART_GAP)) / 2
    for (const part of list) {
      for (const n of part.nodes) {
        const x = side === 'right' ? n.left : -n.left - n.width
        nodes.push({ node: n.node, depth: n.depth, part: part.part, side, x, y: top + n.top, width: n.width, height: n.height })
        links.push({ source: n.parent ?? root.id, target: n.node.id, part: part.part, depth: n.depth, side })
      }
      top += part.height + PART_GAP
    }
  }
  return { nodes, links }
}

/** A part and the nodes shown under it as a tidy tree, its top at 0; left: the distance from the root's centre. */
function layoutPart(node: MindmapNode, part: number, folded: ReadonlySet<string>) {
  const laid = tree<MindmapNode>().nodeSize([1, 1])
    .separation((a, b) => (sizeOf(a.depth + 1)[1] + sizeOf(b.depth + 1)[1]) / 2
      + (a.parent === b.parent ? SIBLING_GAP : COUSIN_GAP))(
      hierarchy(node, (n) => (folded.has(n.id) ? undefined : n.children ?? undefined)))
  const all = laid.descendants()
  const tops = all.map((n) => n.x - sizeOf(n.depth + 1)[1] / 2)
  const min = Math.min(...tops)
  const max = Math.max(...all.map((n) => n.x + sizeOf(n.depth + 1)[1] / 2))
  const nodes = all.map((n, i) => {
    const depth = n.depth + 1
    let left = sizeOf(0)[0] / 2 + COLUMN_GAP
    for (let d = 1; d < depth; d++) left += sizeOf(d)[0] + COLUMN_GAP
    const [width, height] = sizeOf(depth)
    return { node: n.data, parent: n.parent?.data.id, depth, left, top: tops[i] - min, width, height }
  })
  return { part, nodes, height: max - min }
}
