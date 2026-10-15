import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Captions, FileAudio, FileVideo, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { errorMessage, save, uploadFile } from '@/lib/api'

const MEDIA = /\.(mp4|mkv|mov|m4v|webm|avi|wav|mp3|m4a|aac|flac|ogg)$/i
const AUDIO = /\.(wav|mp3|m4a|aac|flac|ogg)$/i
const SUBTITLE = /\.srt$/i
export const isUploadable = (name: string) => MEDIA.test(name) || SUBTITLE.test(name)

/** The files of one video (a.mp4, a.srt, a.vi.srt): uploaded into one folder, one job. */
export type UploadGroup = {
  key: string
  files: File[]
  /** the file the job starts from: the video, else the original .srt, else a translated one */
  source: File
}

/** a for a.mp4, a.srt and a.vi.srt */
const stemOf = (name: string) => name.toLowerCase().replace(/(\.[a-z]{2,3})?\.srt$/, '').replace(/\.[^.]+$/, '')
/** Which file a job starts from: lowest first */
const rank = (name: string) => (MEDIA.test(name) ? 0 : /\.[a-z]{2,3}\.srt$/i.test(name) ? 2 : 1)

export function groupUploads(files: File[]): UploadGroup[] {
  const groups = new Map<string, File[]>()
  for (const file of files) groups.set(stemOf(file.name), [...(groups.get(stemOf(file.name)) ?? []), file])
  return [...groups].map(([key, members]) => ({
    key, files: members, source: members.reduce((best, f) => (rank(f.name) < rank(best.name) ? f : best)),
  }))
}

export const sizeLabel = (bytes: number) =>
  bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(0)} MB` : `${Math.ceil(bytes / 1e3)} KB`

/** Drop or pick videos / .srt from this computer. */
export function UploadDrop({ onFiles, compact, hint }: { onFiles: (files: File[]) => void; compact?: boolean; hint?: string }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const add = (list: FileList | null) => {
    const picked = [...(list ?? [])].filter((f) => isUploadable(f.name))
    if (picked.length) onFiles(picked)
  }
  return (
    <>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files) }}
        className={cn(
          'flex items-center justify-center gap-1 rounded-lg border border-dashed px-4 text-center text-sm text-muted-foreground transition-colors hover:bg-accent/50',
          compact ? 'gap-2 py-3' : 'flex-col py-6',
          over && 'border-primary bg-primary/5 text-foreground',
        )}
      >
        <Upload className="size-5" />
        <span><span className="font-medium text-foreground">Kéo thả</span> hoặc bấm để chọn nhiều file</span>
        {!compact && <span className="text-xs">{hint ?? 'Video/audio, .srt — phụ đề cùng tên (a.srt, a.vi.srt) được dùng luôn thay vì tạo lại'}</span>}
      </button>
      <input ref={input} type="file" multiple hidden accept="video/*,audio/*,.srt"
        onChange={(e) => { add(e.target.files); e.target.value = '' }} />
    </>
  )
}

/** One video in the upload tray: uploaded as soon as it is dropped, started later. */
export type TrayRow = {
  /** `${scope}:${stem}` */
  key: string
  /** the project it was dropped into, or 'none' */
  scope: string
  /** the file the job starts from, and every file of the group */
  name: string
  files: string[]
  size: number
  bytes: number
  sent: number
  progress: number
  uploadId?: string
  /** where the uploaded `name` is, once it is */
  path?: string
  checked: boolean
  error?: string
}

const TRAY_KEY = 'upload-tray'
export const isReady = (r: TrayRow) => r.progress >= 1 && !!r.path && !r.error

function readTray(): TrayRow[] {
  try {
    const rows = JSON.parse(localStorage.getItem(TRAY_KEY) ?? '[]')
    return Array.isArray(rows) ? rows.filter(isReady) : []
  } catch {
    return []
  }
}

/** Files dropped are uploaded right away (two videos at a time, a video before its subtitles, into one
 * folder); a video is checked once uploaded. Uploaded rows are remembered across reloads. */
export function useUploadTray() {
  const [rows, setRows] = useState<TrayRow[]>(readTray)
  useEffect(() => save(TRAY_KEY, rows.filter(isReady)), [rows])
  const queue = useRef<{ key: string; files: File[] }[]>([])
  const busy = useRef(new Set<string>())
  const ids = useRef<Map<string, string> | null>(null)
  ids.current ??= new Map(rows.flatMap((r) => (r.uploadId ? [[r.key, r.uploadId] as const] : [])))

  const setRow = (key: string, patch: (r: TrayRow) => Partial<TrayRow>) =>
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch(r) } : r)))

  async function upload({ key, files }: { key: string; files: File[] }) {
    for (const file of files) {
      try {
        const up = await uploadFile(file, ids.current!.get(key), (done) =>
          setRow(key, (r) => ({ progress: Math.min(0.99, (r.sent + done * file.size) / r.bytes) })))
        ids.current!.set(key, up.id)
        setRow(key, (r) => ({ uploadId: up.id, sent: r.sent + file.size, ...(file.name === r.name && { path: up.path }) }))
      } catch (error) {
        setRow(key, () => ({ error: errorMessage(error) }))
        return
      }
    }
    setRow(key, (r) => (r.sent >= r.bytes ? { progress: 1, checked: true } : { progress: r.sent / r.bytes }))
  }

  function pump() {
    while (busy.current.size < 2) {
      const index = queue.current.findIndex((j) => !busy.current.has(j.key))
      if (index < 0) return
      const [job] = queue.current.splice(index, 1)
      busy.current.add(job.key)
      void upload(job).finally(() => {
        busy.current.delete(job.key)
        pump()
      })
    }
  }

  const add = (scope: string, files: File[]) => {
    const groups = groupUploads(files).map((g) => ({ ...g, key: `${scope}:${g.key}` }))
    setRows((rs) => {
      let next = rs
      for (const g of groups) {
        const bytes = g.files.reduce((n, f) => n + f.size, 0)
        const names = g.files.map((f) => f.name)
        const old = next.find((r) => r.key === g.key)
        if (!old) {
          next = [...next, { key: g.key, scope, name: g.source.name, files: names, size: g.source.size,
            bytes: Math.max(1, bytes), sent: 0, progress: 0, checked: false }]
          continue
        }
        // more files of a video already in the tray: a better source (the video after its .srt) replaces it
        const better = rank(g.source.name) < rank(old.name)
        next = next.map((r) => r !== old ? r : {
          ...r, files: [...new Set([...r.files, ...names])], bytes: r.bytes + bytes, progress: r.sent / (r.bytes + bytes),
          checked: false, error: undefined,
          ...(better && { name: g.source.name, size: g.source.size, path: undefined }),
        })
      }
      return next
    })
    // the source first: its folder is where the subtitles go
    for (const g of groups) queue.current.push({ key: g.key, files: [g.source, ...g.files.filter((f) => f !== g.source)] })
    pump()
  }

  const remove = (keys: string[]) => {
    queue.current = queue.current.filter((j) => !keys.includes(j.key))
    setRows((rs) => rs.filter((r) => !keys.includes(r.key)))
  }
  const check = (keys: string[], checked: boolean) =>
    setRows((rs) => rs.map((r) => (keys.includes(r.key) && isReady(r) ? { ...r, checked } : r)))

  return { rows, add, remove, check }
}

const iconOf = (name: string) => (SUBTITLE.test(name) ? Captions : AUDIO.test(name) ? FileAudio : FileVideo)
/** "vi" for a.vi.srt, "gốc" for a.srt */
const subLabel = (name: string) => name.match(/\.([a-z]{2,3})\.srt$/i)?.[1] ?? 'gốc'

/** The videos in the tray with their upload progress; uploaded ones can be checked to start. */
export function UploadTray({ rows, onCheck, onRemove }: {
  rows: TrayRow[]
  onCheck: (keys: string[], checked: boolean) => void
  onRemove: (key: string) => void
}) {
  const ready = rows.filter(isReady)
  const all = ready.length > 0 && ready.every((r) => r.checked)
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            <th className="w-8 px-2 py-1.5 text-left">
              <input type="checkbox" aria-label="Chọn tất cả video đã tải lên" disabled={!ready.length} checked={all}
                onChange={(e) => onCheck(ready.map((r) => r.key), e.target.checked)} />
            </th>
            <th className="px-2 py-1.5 text-left font-medium">Video</th>
            <th className="px-2 py-1.5 text-left font-medium">Tải lên</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const Icon = iconOf(r.name)
            return (
              <tr key={r.key} className="border-t">
                <td className="px-2 py-1.5">
                  <input type="checkbox" aria-label={`Chọn ${r.name}`} disabled={!isReady(r)} checked={r.checked}
                    onChange={(e) => onCheck([r.key], e.target.checked)} />
                </td>
                <td className="max-w-0 px-2 py-1.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <Icon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate font-medium">{r.name}</span>
                  </div>
                  {r.files.length > 1 && (
                    <div className="mt-0.5 flex flex-wrap gap-1 pl-6">
                      {r.files.filter((n) => n !== r.name).map((n) => (
                        <span key={n} title={n} className="inline-flex items-center gap-1 rounded border px-1.5 font-mono text-[11px] text-muted-foreground">
                          <Captions className="size-3" />{subLabel(n)}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="w-36 px-2 py-1.5">
                  {r.error ? (
                    <span className="flex items-center gap-1 text-xs text-destructive" title={r.error}>
                      <AlertCircle className="size-3.5 shrink-0" /><span className="truncate">{r.error}</span>
                    </span>
                  ) : isReady(r) ? (
                    <span className="text-xs text-muted-foreground">Xong · {sizeLabel(r.size)}</span>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Progress value={r.progress * 100} className="h-1 flex-1" />
                      <span className="w-8 text-right text-xs tabular-nums text-muted-foreground">{Math.round(r.progress * 100)}%</span>
                    </div>
                  )}
                </td>
                <td className="px-1">
                  <Button type="button" variant="ghost" size="icon" className="size-6" aria-label={`Bỏ ${r.name}`}
                    onClick={() => onRemove(r.key)}>
                    <X />
                  </Button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
