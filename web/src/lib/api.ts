import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { client } from '@/api/client.gen'
import {
  attemptEvents, getConfig, listDrivers, listJobs, listProjects, listTemplates,
  type ArtifactInfo, type DriverInfo, type Event, type Job, type Task, type Uploaded,
} from '@/api'

client.setConfig({ baseUrl: '', throwOnError: true })

export type Status = Task['status']
export type JobStatus = Job['status']
export type Params = Record<string, unknown>

export const STAGE_LABELS: Record<string, string> = {
  source: 'Nguồn',
  separate: 'Tách nền',
  asr: 'Phụ đề',
  refine: 'Hiệu chỉnh',
  translate: 'Dịch',
  dub: 'Lồng tiếng',
  mux: 'Ghép video',
}
export const stageLabel = (stage: string) => STAGE_LABELS[stage] ?? stage

/** What a job paid to paid APIs, e.g. "$0.012"; null when it paid nothing. */
export const formatCost = (usd?: number | null) =>
  usd ? `$${usd < 0.01 ? usd.toFixed(4) : usd < 1 ? usd.toFixed(3) : usd.toFixed(2)}` : null

export const STATUS_LABELS: Record<Status, string> = {
  blocked: 'Chờ bước trước',
  queued: 'Đang chờ',
  running: 'Đang chạy',
  done: 'Xong',
  failed: 'Lỗi',
  cancelled: 'Đã dừng',
  skipped: 'Bỏ qua',
  waiting_review: 'Chờ duyệt',
  stale: 'Cần chạy lại',
}

export const ACTIVE: JobStatus[] = ['running', 'queued', 'blocked']
export const isActive = (status: JobStatus) => ACTIVE.includes(status)

/** FastAPI errors come back as {detail}; turn anything thrown into a readable message. */
export function errorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? JSON.stringify(d)).join('; ')
    return JSON.stringify(detail)
  }
  if (error instanceof Error) return error.message
  return String(error)
}

/** A 409 from a rerun that would replace edits made by hand: ask, then send again with confirm. */
export function needsConfirm(error: unknown): boolean {
  const detail = error && typeof error === 'object' && 'detail' in error ? (error as { detail: unknown }).detail : null
  return !!detail && typeof detail === 'object' && 'confirm' in detail
}

export const fileName = (path: string) => path.split('/').pop() ?? path
export const artifactUrl = (id: string) => `/api/artifacts/${id}`
export const downloadUrl = (id: string) => `/api/artifacts/${id}/download`
/** Shown in the browser (transcripts as SRT text) instead of downloaded. */
export const inlineUrl = (id: string) => `/api/artifacts/${id}/download?inline=true`

/** The current output of a job for a slot: the last task producing it that has one. */
export function slotArtifact(job: Job, slot: string): (ArtifactInfo & { task: Task }) | undefined {
  for (const task of [...job.tasks].reverse()) {
    const artifact = task.artifacts?.find((a) => a.slot === slot)
    if (artifact) return { ...artifact, task }
  }
  return undefined
}

/** PUT a file to /api/uploads (into folder `id` when given) with upload progress, which fetch lacks. */
export function uploadFile(file: File, id: string | undefined, onProgress: (fraction: number) => void) {
  const query = new URLSearchParams({ name: file.name, ...(id ? { id } : {}) })
  return new Promise<Uploaded>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', `/api/uploads?${query}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onload = () => {
      let body: unknown = xhr.responseText
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        /* plain-text error */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Uploaded)
      else reject(typeof body === 'string' ? new Error(body || `${file.name}: lỗi ${xhr.status}`) : body)
    }
    xhr.onerror = () => reject(new Error(`${file.name}: mất kết nối khi tải lên`))
    xhr.send(file)
  })
}

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: async () => (await getConfig()).data, staleTime: Infinity })
}

export function useDrivers() {
  return useQuery({ queryKey: ['drivers'], queryFn: async () => (await listDrivers()).data, staleTime: 60_000 })
}

export function useTemplates() {
  return useQuery({ queryKey: ['templates'], queryFn: async () => (await listTemplates()).data, staleTime: Infinity })
}

export function useProjects() {
  return useQuery({ queryKey: ['projects'], queryFn: async () => (await listProjects()).data })
}

export function driversOf(drivers: DriverInfo[] | undefined, stage: string) {
  return drivers?.filter((d) => d.stage === stage) ?? []
}

export function useJobs() {
  return useQuery({ queryKey: ['jobs'], queryFn: async () => (await listJobs()).data })
}

export function putJob(queryClient: QueryClient, job: Job) {
  queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) => {
    const index = jobs.findIndex((j) => j.id === job.id)
    if (index < 0) return [job, ...jobs]
    const next = jobs.slice()
    next[index] = job
    return next
  })
}

type LiveEvent =
  | { type: 'job'; job: Job }
  | { type: 'progress'; job_id: string; task_id: string; done: number; total: number }
  | { type: 'log'; job_id: string; task_id: string; event: Event }
  | { type: 'deleted'; id: string }
  | { type: 'project'; id: string; deleted: boolean }

/** Keeps the ['jobs'] cache (and open pipeline logs) in sync with the server's event stream;
 * onJobFinished fires when a job stops running. */
export function useLiveJobs(onJobFinished?: (job: Job) => void) {
  const queryClient = useQueryClient()
  const finished = useRef(onJobFinished)
  useEffect(() => {
    finished.current = onJobFinished
  })
  useEffect(() => {
    const source = new EventSource('/api/events')
    // after a reconnect we may have missed events: reload the list and open logs
    source.onopen = () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: ['events'] })
    }
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as LiveEvent
      if (event.type === 'job') {
        const before = queryClient.getQueryData<Job[]>(['jobs'])?.find((j) => j.id === event.job.id)
        putJob(queryClient, event.job)
        if (before && isActive(before.status) && !isActive(event.job.status)) finished.current?.(event.job)
        void queryClient.invalidateQueries({ queryKey: ['transcript', event.job.id] })
      } else if (event.type === 'progress') {
        queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) =>
          jobs.map((job) => job.id !== event.job_id ? job : {
            ...job,
            tasks: job.tasks.map((t) => (t.id === event.task_id ? { ...t, done: event.done, total: event.total } : t)),
          }),
        )
      } else if (event.type === 'log') {
        const attempt = event.event.attempt_id
        if (attempt) {
          queryClient.setQueryData<Event[]>(['events', attempt], (events) =>
            events && !events.some((e) => e.id === event.event.id) ? [...events, event.event] : events)
        }
      } else if (event.type === 'deleted') {
        queryClient.setQueryData<Job[]>(['jobs'], (jobs = []) => jobs.filter((j) => j.id !== event.id))
      } else if (event.type === 'project') {
        void queryClient.invalidateQueries({ queryKey: ['projects'] })
        // its jobs are left without a project
        if (event.deleted) void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      }
    }
    return () => source.close()
  }, [queryClient])
}

/** The pipeline log of an attempt, kept current by useLiveJobs. */
export function useAttemptEvents(attemptId: string | null | undefined) {
  return useQuery({
    queryKey: ['events', attemptId],
    queryFn: async () => (await attemptEvents({ path: { attempt_id: attemptId! } })).data,
    enabled: !!attemptId,
    staleTime: Infinity,
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
