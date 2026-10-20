// Where each job was left in the player, per language: kept in this browser only (localStorage), never on the server.
import { useSyncExternalStore } from 'react'
import type { Job } from '@/api'
import { loadSaved, save } from '@/lib/api'
import { playback } from '@/lib/playback'

/** t: the second it was left at, d: the media's length, at: when (ms). */
export type Watched = { t: number; d: number; at: number }
/** By job and language. */
type Store = Record<string, Watched>

const KEY = 'watched'
/** Watched past this share of it: played from the start again. */
const DONE = 0.95
/** Saved again once this many seconds further on. */
const EVERY = 5
/** The jobs remembered, the last watched kept. */
const KEEP = 300

const keyOf = (job: string, lang: string | undefined) => `${job}:${lang ?? ''}`
const jobOf = (key: string) => key.slice(0, key.lastIndexOf(':'))
let store: Store | undefined
const listeners = new Set<() => void>()
const read = () => (store ??= loadSaved<Store>(KEY, {}))

/** Every job remembered, re-rendered as one is saved. */
export const useWatched = () => useSyncExternalStore((listener) => {
  listeners.add(listener)
  return () => void listeners.delete(listener)
}, read)

/** Where `job` was left in `lang`; from what is saved now without `saved`. */
export const watchedIn = (job: string, lang: string | undefined, saved = read()): Watched | undefined => saved[keyOf(job, lang)]

/** Remembers `job` left at `time` of `duration` in `lang`: every few seconds, or right away (`now`: paused, ended). */
export function saveWatched(job: string, lang: string | undefined, time: number, duration: number, now = false) {
  if (!duration || !Number.isFinite(duration)) return
  const key = keyOf(job, lang)
  const all = read()
  const last = all[key]
  if (!now && last && Math.abs(time - last.t) < EVERY) return
  const kept = Object.entries(all).filter(([k]) => k !== key).sort(([, a], [, b]) => b.at - a.at).slice(0, KEEP - 1)
  store = { ...Object.fromEntries(kept), [key]: { t: time, d: duration, at: Date.now() } }
  save(KEY, store)
  listeners.forEach((listener) => listener())
}

/** The share of it watched, 0 to 1. */
export const progress = (w: Watched) => Math.min(1, w.d ? w.t / w.d : 0)
/** The second to play it from: where it was left, unless watched (nearly) to the end. */
export const resumeAt = (w: Watched | undefined) => (w && progress(w) < DONE ? w.t : undefined)

/** The job of `jobs` to watch in `lang`: the one left part-way through most lately (left), else the first with
 * something to watch. */
export function toWatch(jobs: Job[], lang: string | undefined, saved = read()): { job: Job; left: boolean } | undefined {
  const ids = new Map(jobs.map((j) => [j.id, j]))
  let last: { job: Job; at: number } | undefined
  for (const [key, w] of Object.entries(saved)) {
    const job = ids.get(jobOf(key))
    if (job && key === keyOf(job.id, lang) && resumeAt(w) !== undefined && (!last || w.at > last.at)) last = { job, at: w.at }
  }
  if (last) return { job: last.job, left: true }
  const first = jobs.find((j) => playback(j, lang))
  return first && { job: first, left: false }
}
