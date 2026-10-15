import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { client } from '@/api/client.gen'
import { listJobs, getConfig, getStepLog, type Job, type Step, type StepLog } from '@/api'

client.setConfig({ baseUrl: '', throwOnError: true })

export type StepName = Step['name']
export type Status = Step['status']
export type StepOptions = Record<string, string | number | boolean | null>
export type Options = Record<StepName, StepOptions>

export const STEP_LABELS: Record<StepName, string> = {
  transcribe: 'Phụ đề',
  translate: 'Dịch',
  dub: 'Lồng tiếng',
  mux: 'Ghép video',
}

export const STATUS_LABELS: Record<Status, string> = {
  queued: 'Đang chờ',
  running: 'Đang chạy',
  done: 'Xong',
  failed: 'Lỗi',
  cancelled: 'Đã dừng',
  stale: 'Cần chạy lại',
}

/** FastAPI errors come back as {detail}; turn anything thrown into a readable message. */
export function errorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    return JSON.stringify(detail)
  }
  if (error instanceof Error) return error.message
  return String(error)
}

export const fileName = (path: string) => path.split('/').pop() ?? path
export const mediaUrl = (path: string) => `/api/media?path=${encodeURIComponent(path)}`
export const vttUrl = (path: string) => `/api/subtitles/vtt?path=${encodeURIComponent(path)}`

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: async () => (await getConfig()).data, staleTime: Infinity })
}

export function useJobs() {
  return useQuery({ queryKey: ['jobs'], queryFn: async () => (await listJobs()).data })
}

function putJob(queryClient: QueryClient, job: Job) {
  queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) => {
    const index = jobs.findIndex((j) => j.id === job.id)
    if (index < 0) return [...jobs, job]
    const next = jobs.slice()
    next[index] = job
    return next
  })
}

type Event =
  | { type: 'job'; job: Job }
  | { type: 'progress'; job_id: number; step_id: number; done: number; total: number }
  | { type: 'log'; job_id: number; step_id: number; offset: number; text: string }
  | { type: 'deleted'; id: number }

const byteLength = (text: string) => new TextEncoder().encode(text).length

/** Keeps the ['jobs'] cache in sync with the server's event stream; onJobFinished fires when a job ends. */
export function useLiveJobs(onJobFinished?: (job: Job) => void) {
  const queryClient = useQueryClient()
  const finished = useRef(onJobFinished)
  finished.current = onJobFinished
  useEffect(() => {
    const source = new EventSource('/api/events')
    // after a reconnect we may have missed events: reload the list and open logs
    source.onopen = () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      void queryClient.invalidateQueries({ queryKey: ['log'] })
    }
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as Event
      if (event.type === 'job') {
        const before = queryClient.getQueryData<Job[]>(['jobs'])?.find((j) => j.id === event.job.id)
        putJob(queryClient, event.job)
        const wasActive = before && (before.status === 'running' || before.status === 'queued')
        const isOver = event.job.status !== 'running' && event.job.status !== 'queued'
        if (wasActive && isOver) finished.current?.(event.job)
      } else if (event.type === 'progress') {
        queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) =>
          jobs.map((job) =>
            job.id !== event.job_id
              ? job
              : {
                  ...job,
                  steps: job.steps.map((s) => (s.id === event.step_id ? { ...s, done: event.done, total: event.total } : s)),
                },
          ),
        )
      } else if (event.type === 'log') {
        // only logs someone has open are cached; a piece that does not follow on (missed one) refetches
        let gap = false
        queryClient.setQueriesData<StepLog>({ queryKey: ['log', event.step_id] }, (log) => {
          if (!log || event.offset < log.size) return log
          if (event.offset > log.size) {
            gap = true
            return log
          }
          return { ...log, text: log.text + event.text, size: log.size + byteLength(event.text) }
        })
        if (gap) void queryClient.invalidateQueries({ queryKey: ['log', event.step_id] })
      } else if (event.type === 'deleted') {
        queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) => jobs.filter((j) => j.id !== event.id))
      }
    }
    return () => source.close()
  }, [queryClient])
}

/** A step's log while open, kept current by useLiveJobs; refetched for each new attempt (an old log may be trimmed then). */
export function useStepLog(job: Job, step: Step, open: boolean) {
  return useQuery({
    queryKey: ['log', step.id, step.attempt],
    queryFn: async () => (await getStepLog({ path: { job_id: job.id, name: step.name } })).data,
    enabled: open,
    staleTime: Infinity,
    gcTime: 0,
  })
}

/** localStorage that never throws (private windows, blocked storage). */
export function loadSaved<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback
  } catch {
    return fallback
  }
}

export function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable: settings just are not remembered */
  }
}
