import { useEffect, useRef } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query'
import { countJobs, getJob, jobIds, listJobs, type BulkDownload, type Event, type Job, type JobPage, type ListJobsData } from '@/api'
import { isActive, type JobStatus } from '@/lib/api'
import { BULK_KEY, putBulkDownload } from '@/lib/bulkDownloads'

type ListQuery = NonNullable<ListJobsData['query']>
export type StatusGroup = NonNullable<ListQuery['status']>
export type JobSort = NonNullable<ListQuery['sort']>
/** What the job list shows: a project's jobs ("none": of no project; undefined: every job), in a status group,
 * matching q, in sort order. */
export type JobFilter = { project?: string; status?: StatusGroup; q?: string; sort: JobSort }

export const STATUS_GROUPS: { id: StatusGroup; label: string; hint: string }[] = [
  { id: 'attention', label: 'Cần xử lý', hint: 'Lỗi, chờ duyệt, cần chạy lại' },
  { id: 'active', label: 'Đang chạy', hint: 'Đang chạy, đang chờ, chờ bước trước' },
  { id: 'stopped', label: 'Đã dừng', hint: 'Đã dừng' },
  { id: 'done', label: 'Xong', hint: 'Xong' },
]
export const GROUP_OF: Record<JobStatus, StatusGroup> = {
  failed: 'attention', waiting_review: 'attention', stale: 'attention',
  running: 'active', queued: 'active', blocked: 'active',
  cancelled: 'stopped', done: 'done',
}
export const SORTS: { id: JobSort; label: string }[] = [
  { id: 'created_desc', label: 'Mới tạo' },
  { id: 'created_asc', label: 'Cũ nhất' },
  { id: 'title', label: 'Tên A→Z' },
  { id: 'updated_desc', label: 'Cập nhật gần nhất' },
  { id: 'cost_desc', label: 'Chi phí cao nhất' },
]
export const PAGE = 50

/** Like the server's: no Vietnamese marks (đ → d), lower case. */
export const fold = (text: string) =>
  text.replace(/đ/g, 'd').replace(/Đ/g, 'D').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
/** Like the server's: digits by value, so plain string order puts "Tập 2" before "Tập 10". */
const naturalKey = (text: string) =>
  fold(text).replace(/\d+/g, (digits) => {
    const n = digits.replace(/^0+/, '') || '0'
    return `${String(n.length).padStart(3, '0')}${n}`
  })
const order = (a: string | number, b: string | number) => (a < b ? -1 : a > b ? 1 : 0)

/** < 0 when a comes before b in the list sorted by `sort` (as the server sorts it). */
export function compareJobs(a: Job, b: Job, sort: JobSort): number {
  const desc = sort !== 'created_asc' && sort !== 'title'
  const key = (j: Job) => sort === 'title' ? naturalKey(j.title) : sort === 'updated_desc' ? j.updated_at
    : sort === 'cost_desc' ? j.cost_usd ?? -1 : j.created_at
  const found = order(key(a), key(b)) || order(a.id, b.id)
  return desc ? -found : found
}

/** Whether the job belongs in the list (whatever its place). */
export function matches(job: Job, { project, status, q }: JobFilter) {
  if (project === 'none' ? job.project_id : project && job.project_id !== project) return false
  if (status && GROUP_OF[job.status] !== status) return false
  const needle = fold((q ?? '').split(/\s+/).filter(Boolean).join(' '))
  return !needle || fold(job.title).includes(needle) || fold(job.source).includes(needle)
}

export const listKey = (filter: JobFilter) => ['jobs', filter] as const
const query = ({ project, status, q, sort }: JobFilter) => ({ project, status, q: q || undefined, sort })

/** The list a page at a time; fetchNextPage loads PAGE more. Kept current by useLiveJobs. */
export function useJobList(filter: JobFilter, enabled = true) {
  return useInfiniteQuery({
    queryKey: listKey(filter),
    enabled,
    queryFn: async ({ pageParam }) => (await listJobs({ query: { ...query(filter), cursor: pageParam, limit: PAGE } })).data,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

/** Every job id matching the filter (to act on all of them, loaded or not). */
export async function matchingIds(filter: JobFilter) {
  return (await jobIds({ query: { project: filter.project, status: filter.status, q: filter.q || undefined } })).data
}

/** The numbers about jobs: of the filter's project and q (status: only for pausable / resumable), and of
 * every job. Kept current by useLiveJobs. */
export function useJobCounts({ project, q, status }: Partial<JobFilter>) {
  return useQuery({
    queryKey: ['job-counts', { project, q: q || undefined, status }],
    queryFn: async () => (await countJobs({ query: { project, q: q || undefined, status } })).data,
    placeholderData: (previous) => previous,
  })
}

/** A job: from the loaded lists when it is there, else fetched (opened by URL, not loaded yet). */
export function useJob(id: string | undefined) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: ['job', id],
    queryFn: async () => (await getJob({ path: { job_id: id! } })).data,
    enabled: !!id,
    initialData: () => (id ? loadedJob(queryClient, id) : undefined),
    retry: false,
  })
}

type Pages = InfiniteData<JobPage, string | undefined>
const lists = (queryClient: QueryClient) => queryClient.getQueriesData<Pages>({ queryKey: ['jobs'] })
const loadedJob = (queryClient: QueryClient, id: string) => {
  for (const [, data] of lists(queryClient)) {
    for (const page of data?.pages ?? []) {
      const job = page.jobs.find((j) => j.id === id)
      if (job) return job
    }
  }
  return undefined
}

/** The job as last known here, if it is. */
export const findJob = (queryClient: QueryClient, id: string) =>
  queryClient.getQueryData<Job>(['job', id]) ?? loadedJob(queryClient, id)

function mapJobs(queryClient: QueryClient, change: (jobs: Job[]) => Job[]) {
  queryClient.setQueriesData<Pages>({ queryKey: ['jobs'] }, (data) =>
    data && { ...data, pages: data.pages.map((page) => ({ ...page, jobs: change(page.jobs) })) })
}

/** The job as it is now replaces the one cached, in place: lists never move or drop a job by themselves. */
export function putJob(queryClient: QueryClient, job: Job) {
  mapJobs(queryClient, (jobs) => (jobs.some((j) => j.id === job.id) ? jobs.map((j) => (j.id === job.id ? job : j)) : jobs))
  if (queryClient.getQueryData(['job', job.id])) queryClient.setQueryData(['job', job.id], job)
}

/** Jobs deleted: gone from every list. */
export function dropJobs(queryClient: QueryClient, ids: string[]) {
  mapJobs(queryClient, (jobs) => jobs.filter((j) => !ids.includes(j.id)))
  for (const id of ids) queryClient.removeQueries({ queryKey: ['job', id], exact: true })
}

/** Jobs created in this window: they go on top of the list at once, instead of asking for a refresh. */
const ours = new Set<string>()

function insert(queryClient: QueryClient, filter: JobFilter, job: Job): boolean {
  // on top is their place when the newest come first
  if (!matches(job, filter) || (filter.sort !== 'created_desc' && filter.sort !== 'updated_desc')) return false
  queryClient.setQueryData<Pages>(listKey(filter), (data) => data && !data.pages.some((p) => p.jobs.some((j) => j.id === job.id))
    ? { ...data, pages: data.pages.map((page, i) => (i ? page : { ...page, jobs: [job, ...page.jobs] })) }
    : data)
  return true
}

/** Puts the jobs just created on top of the list shown. */
export async function addCreated(queryClient: QueryClient, filter: JobFilter, ids: string[]) {
  for (const id of ids) ours.add(id)
  void queryClient.invalidateQueries({ queryKey: ['job-counts'] })
  for (const id of ids.slice(0, PAGE)) {
    if (loadedJob(queryClient, id)) continue
    try {
      insert(queryClient, filter, (await getJob({ path: { job_id: id } })).data)
    } catch {
      /* deleted meanwhile */
    }
  }
}

/** Whether the list, as loaded, no longer shows `job` as the server would: it left the filter, its place
 * changed, or it is new to the loaded part. */
function outdated(data: Pages | undefined, filter: JobFilter, job: Job): boolean {
  if (!data) return false
  const shown = data.pages.flatMap((p) => p.jobs)
  const at = shown.findIndex((j) => j.id === job.id)
  if (at < 0) {
    const more = !!data.pages.at(-1)?.next_cursor
    return matches(job, filter) && (!more || (shown.length > 0 && compareJobs(job, shown.at(-1)!, filter.sort) < 0))
  }
  if (!matches(job, filter)) return true
  const before = shown[at - 1]
  const after = shown[at + 1]
  return (!!before && compareJobs(before, job, filter.sort) > 0) || (!!after && compareJobs(job, after, filter.sort) > 0)
}

type LiveEvent =
  | { type: 'job'; job: Job }
  | { type: 'progress'; job_id: string; task_id: string; done: number; total: number }
  | { type: 'log'; job_id: string; task_id: string; event: Event }
  | { type: 'deleted'; id: string }
  | { type: 'project'; id: string; deleted: boolean }
  | { type: 'voice'; id: string; deleted: boolean }
  | { type: 'bulk_download'; id: string; bulk_download: BulkDownload | null }

/** Keeps the cached jobs, the counts, open pipeline logs and bulk downloads in sync with the server's event stream. Jobs
 * change in place; when the list shown (filter) is no longer right, onListChanged fires instead of moving
 * jobs under the cursor. onJobFinished fires when a job stops running. */
export function useLiveJobs({ filter, onJobFinished, onListChanged }: {
  filter: JobFilter
  onJobFinished?: (job: Job) => void
  onListChanged?: () => void
}) {
  const queryClient = useQueryClient()
  const current = useRef({ filter, onJobFinished, onListChanged })
  useEffect(() => {
    current.current = { filter, onJobFinished, onListChanged }
  })
  useEffect(() => {
    const statuses = new Map<string, JobStatus>() // as last seen, to tell when a job finished
    let counting: ReturnType<typeof setTimeout> | undefined
    const recount = () => {
      counting ??= setTimeout(() => {
        counting = undefined
        void queryClient.invalidateQueries({ queryKey: ['job-counts'] })
      }, 1000)
    }
    const source = new EventSource('/api/events')
    // after a reconnect we may have missed events: reload the lists and open logs
    source.onopen = () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      void queryClient.invalidateQueries({ queryKey: ['job'] })
      void queryClient.invalidateQueries({ queryKey: ['job-counts'] })
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: ['voice-library'] })
      void queryClient.invalidateQueries({ queryKey: ['events'] })
      void queryClient.invalidateQueries({ queryKey: BULK_KEY })
    }
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as LiveEvent
      const { filter, onJobFinished, onListChanged } = current.current
      if (event.type === 'job') {
        const job = event.job
        const before = statuses.get(job.id) ?? findJob(queryClient, job.id)?.status
        statuses.set(job.id, job.status)
        putJob(queryClient, job)
        if (before && isActive(before) && !isActive(job.status)) onJobFinished?.(job)
        const data = queryClient.getQueryData<Pages>(listKey(filter))
        if (outdated(data, filter, job)) {
          const loaded = data!.pages.some((p) => p.jobs.some((j) => j.id === job.id))
          if (loaded || !ours.has(job.id) || !insert(queryClient, filter, job)) onListChanged?.()
        }
        recount()
        void queryClient.invalidateQueries({ queryKey: ['transcript', job.id] })
      } else if (event.type === 'progress') {
        const progress = (job: Job) => job.id !== event.job_id ? job : {
          ...job,
          tasks: job.tasks.map((t) => (t.id === event.task_id ? { ...t, done: event.done, total: event.total } : t)),
        }
        mapJobs(queryClient, (jobs) => jobs.map(progress))
        queryClient.setQueryData<Job>(['job', event.job_id], (job) => job && progress(job))
      } else if (event.type === 'log') {
        const attempt = event.event.attempt_id
        if (attempt) {
          queryClient.setQueryData<Event[]>(['events', attempt], (events) =>
            events && !events.some((e) => e.id === event.event.id) ? [...events, event.event] : events)
        }
      } else if (event.type === 'deleted') {
        dropJobs(queryClient, [event.id])
        recount()
      } else if (event.type === 'project') {
        void queryClient.invalidateQueries({ queryKey: ['projects'] })
        if (event.deleted) {
          // its jobs are left without a project, and its voices become shared
          void queryClient.invalidateQueries({ queryKey: ['jobs'] })
          void queryClient.invalidateQueries({ queryKey: ['job'] })
          void queryClient.invalidateQueries({ queryKey: ['voice-library'] })
          recount()
        }
      } else if (event.type === 'voice') {
        void queryClient.invalidateQueries({ queryKey: ['voice-library'] })
      } else if (event.type === 'bulk_download') {
        putBulkDownload(queryClient, event.id, event.bulk_download)
      }
    }
    return () => {
      clearTimeout(counting)
      source.close()
    }
  }, [queryClient])
}
