import type { MindmapNode } from '@/api'

/** The nodes playing at the second t, from its part down: at each level the last child started by t. Empty before the
 * first part starts. */
export function playingPath(root: MindmapNode, t: number): MindmapNode[] {
  const path: MindmapNode[] = []
  for (let node = root.children?.findLast((n) => n.start <= t); node; node = node.children?.findLast((n) => n.start <= t)) {
    path.push(node)
  }
  return path
}

/** The parts (level 1) as a WebVTT chapters track: the marks "Phần" on the player's time bar, their names on hover.
 * undefined when there are no parts. */
export function partsVtt(root: MindmapNode): string | undefined {
  const parts = root.children ?? []
  if (!parts.length) return undefined
  const cues = parts.map((part, i) => {
    const end = partEnd(root, i)
    // a cue is one line, no arrow in it
    const name = `${i + 1}. ${part.label}`.replace(/\s+/g, ' ').replaceAll('-->', '→')
    return `${vttTime(part.start)} --> ${vttTime(Math.max(end, part.start))}\n${name}`
  })
  return `WEBVTT\n\n${cues.join('\n\n')}\n`
}

/** The second the part i ends at: its end, else where the next starts, the last at the end of the video. */
export function partEnd(root: MindmapNode, i: number): number {
  const parts = root.children ?? []
  return parts[i].end ?? parts[i + 1]?.start ?? root.end ?? parts[i].start
}

/** "m:ss", "h:mm:ss" past an hour. */
export const clock = (seconds: number) => {
  const s = Math.floor(seconds)
  const [h, m, sec] = [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60]
  return `${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${String(sec).padStart(2, '0')}`
}

/** "hh:mm:ss.mmm" */
const vttTime = (seconds: number) => {
  const ms = Math.round(seconds * 1000)
  const pad = (n: number, width = 2) => String(n).padStart(width, '0')
  return `${pad(Math.floor(ms / 3_600_000))}:${pad(Math.floor(ms / 60_000) % 60)}:${pad(Math.floor(ms / 1000) % 60)}.${pad(ms % 1000, 3)}`
}
