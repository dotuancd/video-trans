// Playing a dub that is still being made: the original video (quiet, like mux's ducking) with the dub
// pieces done so far laid over it in sync through Web Audio. Each piece is placed against the video's
// clock when it starts, so drift never builds up past one piece (~30s).
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getLive, type LiveAudio, type Task } from '@/api'

const ORIGINAL_VOLUME = 0.15 // mux's default original_volume
const LOOKAHEAD = 10 // seconds of dub fetched and scheduled ahead of the playhead
const KEEP = 40 // decoded pieces further than this (seconds) from the playhead are dropped
const EDGE = 0.3 // pause this close (seconds) to the end of what is ready

/** The live index of a dub task, refetched as its progress moves (progress arrives over /api/events). */
export function useLiveIndex(task: Task | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['live', task?.id, task?.status, task?.done],
    queryFn: async () => (await getLive({ path: { task_id: task!.id } })).data,
    enabled: enabled && !!task,
    retry: false,
    placeholderData: (previous) => previous,
  })
}

const chunkUrl = (taskId: string, live: LiveAudio, n: number) =>
  `/api/tasks/${taskId}/live/${n}?attempt=${live.attempt_id}`

/** Plays live over media; waiting = paused at the end of what is ready, resumes on its own when more is. */
export function useLiveDub(media: HTMLMediaElement | null, taskId: string | undefined, live: LiveAudio | undefined) {
  const [waiting, setWaiting] = useState(false)
  const latest = useRef(live)
  const kick = useRef<() => void>(() => {})
  useEffect(() => {
    latest.current = live
    kick.current()
  }, [live])

  useEffect(() => {
    if (!media || !taskId) return
    const ctx = new AudioContext()
    const buffers = new Map<string, Promise<AudioBuffer | null>>() // by url
    const scheduled = new Map<number, AudioBufferSourceNode>() // since the last play/seek
    let autoPaused = false
    media.volume = ORIGINAL_VOLUME

    const load = (url: string) => {
      if (!buffers.has(url)) {
        buffers.set(url, fetch(url)
          .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.statusText))))
          .then((data) => ctx.decodeAudioData(data))
          .catch(() => { buffers.delete(url); return null }))
      }
      return buffers.get(url)!
    }
    const stopAll = () => {
      scheduled.forEach((source) => { try { source.stop() } catch { /* not started yet */ } })
      scheduled.clear()
    }
    const schedule = () => {
      const index = latest.current
      if (!index || media.paused) return
      const t = media.currentTime
      for (const chunk of index.chunks) {
        if (chunk.end <= t || chunk.start > t + LOOKAHEAD || scheduled.has(chunk.n)) continue
        const url = chunkUrl(taskId, index, chunk.n)
        void load(url).then((buffer) => {
          if (!buffer || media.paused || media.seeking || scheduled.has(chunk.n)) return
          const now = media.currentTime
          const offset = Math.max(0, now - chunk.start)
          if (offset >= buffer.duration) return
          const source = ctx.createBufferSource()
          source.buffer = buffer
          source.connect(ctx.destination)
          source.start(ctx.currentTime + Math.max(0, chunk.start - now), offset)
          scheduled.set(chunk.n, source)
        })
      }
      for (const [url, _buffer] of buffers) { // keep memory flat on long videos
        const n = Number(url.split('/').pop()!.split('?')[0])
        const chunk = index.chunks[n]
        if (!chunk || chunk.end < t - KEEP || chunk.start > t + KEEP) buffers.delete(url)
      }
    }
    const atEdge = () => {
      const index = latest.current
      return !!index && !index.finished && media.currentTime >= index.ready_until - EDGE
    }
    const check = () => {
      if (!media.paused && atEdge()) {
        autoPaused = true
        media.pause()
        setWaiting(true)
      }
    }
    kick.current = () => {
      if (autoPaused && !atEdge()) {
        autoPaused = false
        setWaiting(false)
        void media.play()
      }
      schedule()
    }

    const onPlay = () => {
      autoPaused = false
      setWaiting(atEdge())
      void ctx.resume()
      stopAll()
      check()
      schedule()
    }
    const onPause = () => { stopAll(); if (!autoPaused) setWaiting(false) }
    const onSeeking = () => stopAll()
    const onSeeked = () => { check(); schedule() }
    const onTime = () => { check(); schedule() }
    const onRate = () => { if (media.playbackRate !== 1) media.playbackRate = 1 } // pieces play at 1x only
    media.addEventListener('play', onPlay)
    media.addEventListener('pause', onPause)
    media.addEventListener('seeking', onSeeking)
    media.addEventListener('seeked', onSeeked)
    media.addEventListener('timeupdate', onTime)
    media.addEventListener('ratechange', onRate)
    return () => {
      media.removeEventListener('play', onPlay)
      media.removeEventListener('pause', onPause)
      media.removeEventListener('seeking', onSeeking)
      media.removeEventListener('seeked', onSeeked)
      media.removeEventListener('timeupdate', onTime)
      media.removeEventListener('ratechange', onRate)
      kick.current = () => {}
      stopAll()
      void ctx.close()
      media.volume = 1
    }
  }, [media, taskId])

  return waiting
}

export const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
