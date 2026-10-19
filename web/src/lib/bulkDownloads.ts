import { useEffect, useSyncExternalStore } from 'react'
import { useQuery, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { listBulkDownloads, type BulkDownload } from '@/api'
import { bulkDownloadUrl, sizeLabel } from '@/lib/api'

export const BULK_KEY = ['bulk-downloads']
/** The queue lane whose workers make the zips. */
export const BULK_LANE = 'bulk_download'

/** Waiting or being made: it can still be cancelled. */
export const isMaking = (bulk: BulkDownload) => bulk.status === 'queued' || bulk.status === 'running'
export const bulkPercent = (bulk: BulkDownload) => (bulk.total ? Math.floor(((bulk.done ?? 0) / bulk.total) * 100) : 0)
export const skippedLabel = (bulk: BulkDownload) =>
  bulk.skipped.length ? `${bulk.skipped.length} file đã mất, bỏ qua` : undefined

export const useBulkDownloads = () =>
  useQuery({ queryKey: BULK_KEY, queryFn: async () => (await listBulkDownloads()).data })

/** A bulk download changed (from the event stream; null: deleted). */
export function putBulkDownload(queryClient: QueryClient, id: string, bulk: BulkDownload | null) {
  queryClient.setQueryData<BulkDownload[]>(BULK_KEY, (bulks) => {
    if (!bulks) return bulks
    if (!bulk) return bulks.filter((b) => b.id !== id)
    return bulks.some((b) => b.id === id) ? bulks.map((b) => (b.id === id ? bulk : b)) : [bulk, ...bulks]
  })
}

/** Starts the browser's download of a URL, staying on the page. */
export function startDownload(url: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = ''
  a.click()
}

// the bulk downloads asked for from this tab: a toast follows each until it is ready
let watched: string[] = []
const listeners = new Set<() => void>()
const setWatched = (ids: string[]) => {
  watched = ids
  for (const listener of listeners) listener()
}
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** Follow a bulk download just asked for with a toast. Events of it may have come first: those are newer. */
export function watchBulkDownload(queryClient: QueryClient, bulk: BulkDownload) {
  if (!queryClient.getQueryData<BulkDownload[]>(BULK_KEY)?.some((b) => b.id === bulk.id)) {
    putBulkDownload(queryClient, bulk.id, bulk)
  }
  if (!watched.includes(bulk.id)) setWatched([...watched, bulk.id])
}

/** The toasts of the bulk downloads watched: progress while being made, then [Tải về] (no download by itself). */
export function BulkDownloadToasts() {
  const ids = useSyncExternalStore(subscribe, () => watched)
  const bulks = useBulkDownloads()
  useEffect(() => {
    for (const id of ids) {
      const bulk = bulks.data?.find((b) => b.id === id)
      if (!bulk) {
        if (bulks.isFetched && !bulks.isFetching) {
          toast.dismiss(id)
          setWatched(watched.filter((w) => w !== id))
        }
        continue
      }
      const done = () => setWatched(watched.filter((w) => w !== id))
      const skipped = skippedLabel(bulk)
      if (bulk.status === 'ready') {
        toast.success('Gói tải về đã sẵn sàng', {
          id, duration: Infinity, closeButton: true, onDismiss: done,
          description: [`${bulk.name} · ${sizeLabel(bulk.size ?? 0)}`, skipped].filter(Boolean).join(' · '),
          action: { label: 'Tải về', onClick: () => { startDownload(bulkDownloadUrl(id)); done() } },
        })
      } else if (bulk.status === 'failed') {
        toast.error('Tạo gói tải về lỗi', { id, duration: Infinity, closeButton: true, description: bulk.error, onDismiss: done })
      } else if (bulk.status === 'cancelled') {
        toast.dismiss(id)
        done()
      } else {
        toast.loading(bulk.status === 'queued' ? 'Gói tải về đang chờ…' : `Đang tạo gói tải về… ${bulkPercent(bulk)}%`, {
          id, duration: Infinity, description: `${bulk.files} file · ${bulk.name}`,
        })
      }
    }
  }, [ids, bulks.data, bulks.isFetched, bulks.isFetching])
  return null
}
