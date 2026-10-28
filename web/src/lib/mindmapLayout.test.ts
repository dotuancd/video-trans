import { describe, expect, it } from 'vitest'
import type { MindmapNode } from '@/api'
import { layoutMindmap, pickView, splitAt } from '@/lib/mindmapLayout'

// a part with n points
const part = (id: string, start: number, n = 0): MindmapNode => ({
  id, label: id, start, children: Array.from({ length: n }, (_, i) => ({ id: `${id}.${i}`, label: `${id}.${i}`, start: start + i })),
})
const map = (...parts: MindmapNode[]): MindmapNode => ({ id: 'root', label: 'Video', start: 0, children: parts })
const placed = (root: MindmapNode, view: 'A' | 'B', folded: string[] = []) =>
  layoutMindmap(root, new Set(folded), view).nodes.filter((n) => n.depth === 1)

describe('layoutMindmap', () => {
  it('B goes clockwise: down the right in time order, then up the left', () => {
    const parts = placed(map(part('a', 0, 2), part('b', 10, 2), part('c', 20, 2), part('d', 30, 2)), 'B')
    const right = parts.filter((p) => p.side === 'right').sort((p, q) => p.y - q.y).map((p) => p.node.id)
    const left = parts.filter((p) => p.side === 'left').sort((p, q) => q.y - p.y).map((p) => p.node.id)
    expect([...right, ...left]).toEqual(['a', 'b', 'c', 'd'])
    expect(right).toEqual(['a', 'b'])
  })

  it('A puts every part on the right, top → bottom in time order', () => {
    const parts = placed(map(part('a', 0, 1), part('b', 10, 1), part('c', 20, 1)), 'A')
    expect(parts.every((p) => p.side === 'right' && p.x > 0)).toBe(true)
    expect(parts.sort((p, q) => p.y - q.y).map((p) => p.node.id)).toEqual(['a', 'b', 'c'])
  })

  it('splits the sides by height, not by the number of parts', () => {
    // one long part first: alone on the right, the four short ones on the left
    const parts = placed(map(part('a', 0, 12), part('b', 10, 1), part('c', 20, 1), part('d', 30, 1), part('e', 40, 1)), 'B')
    expect(parts.filter((p) => p.side === 'right').map((p) => p.node.id)).toEqual(['a'])
  })

  it('leaves out what is under a folded node, its height with it', () => {
    const root = map(part('a', 0, 12), part('b', 10, 1), part('c', 20, 1), part('d', 30, 1), part('e', 40, 1))
    const { nodes } = layoutMindmap(root, new Set(['a']), 'B')
    expect(nodes.some((n) => n.node.id.startsWith('a.'))).toBe(false)
    expect(placed(root, 'B', ['a']).filter((p) => p.side === 'right').map((p) => p.node.id)).toEqual(['a', 'b'])
  })

  it('links each node to its parent, the parts to the root, in the colour of their part', () => {
    const { links } = layoutMindmap(map(part('a', 0, 1), part('b', 10, 1)), new Set(), 'B')
    expect(links).toContainEqual({ source: 'root', target: 'b', part: 1, depth: 1, side: 'left' })
    expect(links).toContainEqual({ source: 'b', target: 'b.0', part: 1, depth: 2, side: 'left' })
  })

  it('overlaps no two nodes', () => {
    const { nodes } = layoutMindmap(map(part('a', 0, 3), part('b', 10, 5), part('c', 20, 2), part('d', 30, 4)), new Set(), 'B')
    for (const [i, n] of nodes.entries()) {
      for (const m of nodes.slice(i + 1)) {
        const apart = n.x + n.width <= m.x || m.x + m.width <= n.x || n.y + n.height <= m.y || m.y + m.height <= n.y
        expect(apart, `${n.node.id} / ${m.node.id}`).toBe(true)
      }
    }
  })
})

describe('splitAt', () => {
  it('cuts where the two sides differ least', () => {
    expect(splitAt([100, 100, 100, 100])).toBe(2)
    expect(splitAt([500, 100, 100, 100, 100])).toBe(1)
    expect(splitAt([100, 100, 100, 100, 500])).toBe(4)
  })

  it('keeps a part on each side', () => {
    expect(splitAt([1000, 10])).toBe(1)
    expect(splitAt([10, 1000])).toBe(1)
  })
})

describe('pickView', () => {
  const wide = { width: 1600, height: 900 }
  it('is B on a wide screen with more than 3 parts', () => {
    expect(pickView(undefined, 4, wide)).toBe('B')
  })

  it('is A with 3 parts or fewer, or on a screen taller than wide', () => {
    expect(pickView(undefined, 3, wide)).toBe('A')
    expect(pickView(undefined, 8, { width: 800, height: 1200 })).toBe('A')
  })

  it('is the one the user picked, whatever the rule says', () => {
    expect(pickView('B', 2, { width: 800, height: 1200 })).toBe('B')
    expect(pickView('A', 8, wide)).toBe('A')
  })
})
