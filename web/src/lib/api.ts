import { useQuery } from '@tanstack/react-query'
import { client } from '@/api/client.gen'
import {
  attemptEvents, attemptRequests, getConfig, getTranscript, listDrivers, listProjects, listTemplates,
  type ArtifactInfo, type DriverInfo, type Job, type Task, type Uploaded,
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
  brief: 'Bối cảnh',
  translate: 'Dịch',
  dub: 'Lồng tiếng',
  mux: 'Ghép video',
  mindmap: 'Mindmap',
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
export const bulkDownloadUrl = (id: string) => `/api/bulk-downloads/${id}/download`

export const sizeLabel = (bytes: number) =>
  bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(0)} MB` : `${Math.ceil(bytes / 1e3)} KB`

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

/** A job's subtitles: lang a language code, "src" the original's; nothing loaded without one. */
export function useTranscript(jobId: string, lang: string | undefined) {
  return useQuery({
    queryKey: ['transcript', jobId, lang],
    queryFn: async () => (await getTranscript({ path: { job_id: jobId }, query: { lang } })).data,
    enabled: !!lang,
    retry: false,
  })
}

/** The pipeline log of an attempt, kept current by useLiveJobs (lib/jobs). */
export function useAttemptEvents(attemptId: string | null | undefined) {
  return useQuery({
    queryKey: ['events', attemptId],
    queryFn: async () => (await attemptEvents({ path: { attempt_id: attemptId! } })).data,
    enabled: !!attemptId,
    staleTime: Infinity,
  })
}

/** What an LLM translation sent and got back, request by request; polled while the attempt runs. */
export function useAttemptRequests(attemptId: string, running: boolean) {
  return useQuery({
    queryKey: ['requests', attemptId],
    queryFn: async () => (await attemptRequests({ path: { attempt_id: attemptId } })).data,
    refetchInterval: running ? 3000 : false,
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

/** The "skip the cache" switch of a rerun. */
export const FRESH_HINT = 'Tính lại từ đầu dù đầu vào và thông số không đổi (kể cả câu lồng tiếng đã đọc, phần đã dịch dở)'
