import { describe, expect, it } from 'vitest'
import type { MindmapNode } from '@/api'
import { partsVtt, playingPath } from '@/lib/mindmap'

// a 10-minute video: an intro from 0:05 (nothing before it), then two parts; a part's first point starts after it
const root: MindmapNode = {
  id: 'root', label: 'Video', start: 0, end: 600, children: [
    { id: 'p1', label: 'Mở đầu', start: 5, end: 120, children: [
      { id: 'p1.1', label: 'Chào', start: 5 },
      { id: 'p1.2', label: 'Mục tiêu', start: 60, children: [{ id: 'p1.2.1', label: 'Chi tiết', start: 90 }] },
    ] },
    { id: 'p2', label: 'Nội dung', start: 120, end: 400, children: [
      { id: 'p2.1', label: 'Ý một', start: 150 },
      { id: 'p2.2', label: 'Ý hai', start: 300 },
    ] },
    { id: 'p3', label: 'Kết', start: 400, end: 600 },
  ],
}
const ids = (t: number) => playingPath(root, t).map((n) => n.id)

describe('playingPath', () => {
  it('is empty before the first part', () => {
    expect(ids(0)).toEqual([])
    expect(ids(4.9)).toEqual([])
  })

  it('takes a node from the second it starts at', () => {
    expect(ids(5)).toEqual(['p1', 'p1.1'])
    expect(ids(60)).toEqual(['p1', 'p1.2'])
    expect(ids(90)).toEqual(['p1', 'p1.2', 'p1.2.1'])
    expect(ids(120)).toEqual(['p2'])
  })

  it('keeps the point before until the next one starts', () => {
    expect(ids(59.9)).toEqual(['p1', 'p1.1'])
    expect(ids(200)).toEqual(['p2', 'p2.1'])
  })

  it('stops at the part when its first point has not started yet', () => {
    expect(ids(130)).toEqual(['p2'])
  })

  it('is the last part at the end of the video and past it', () => {
    expect(ids(600)).toEqual(['p3'])
    expect(ids(10_000)).toEqual(['p3'])
  })

  it('is empty for a mindmap with no parts', () => {
    expect(playingPath({ id: 'root', label: 'Video', start: 0 }, 30)).toEqual([])
  })
})

describe('partsVtt', () => {
  it('has a numbered chapter per part, from its start to its end', () => {
    expect(partsVtt(root)).toBe([
      'WEBVTT',
      '',
      '00:00:05.000 --> 00:02:00.000\n1. Mở đầu',
      '',
      '00:02:00.000 --> 00:06:40.000\n2. Nội dung',
      '',
      '00:06:40.000 --> 00:10:00.000\n3. Kết',
      '',
    ].join('\n'))
  })

  it('ends a part without an end where the next starts, the last at the end of the video', () => {
    const vtt = partsVtt({ id: 'root', label: 'V', start: 0, end: 3725.5, children: [
      { id: 'a', label: 'A', start: 0 },
      { id: 'b', label: 'B', start: 61.25 },
    ] })
    expect(vtt).toContain('00:00:00.000 --> 00:01:01.250\n1. A')
    expect(vtt).toContain('00:01:01.250 --> 01:02:05.500\n2. B')
  })

  it('keeps a name on one line, with no cue arrow in it', () => {
    const vtt = partsVtt({ id: 'root', label: 'V', start: 0, end: 10, children: [
      { id: 'a', label: 'A --> B\n\nC', start: 0, end: 10 },
    ] })
    expect(vtt).toContain('\n1. A → B C\n')
  })

  it('is nothing without parts', () => {
    expect(partsVtt({ id: 'root', label: 'V', start: 0, children: [] })).toBeUndefined()
  })
})
