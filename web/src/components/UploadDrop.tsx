import { useRef, useState } from 'react'
import { Captions, FileVideo, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { guessStart, type Start } from '@/lib/api'

const ORDER: Start[] = ['media', 'srt', 'vi_srt']

/** The files of one video (a.mp4, a.srt, a.vi.srt): uploaded into one folder, one job. */
export type UploadGroup = {
  key: string
  files: File[]
  /** the file the job starts from: the most advanced one */
  source: File
  start: Start
}

const stemOf = (name: string) => name.toLowerCase().replace(/(\.vi)?\.srt$/, '').replace(/\.[^.]+$/, '')

export function groupUploads(files: File[]): UploadGroup[] {
  const groups = new Map<string, File[]>()
  for (const file of files) groups.set(stemOf(file.name), [...(groups.get(stemOf(file.name)) ?? []), file])
  return [...groups].map(([key, members]) => {
    const source = members.reduce((best, f) =>
      ORDER.indexOf(guessStart(f.name)!) > ORDER.indexOf(guessStart(best.name)!) ? f : best)
    return { key, files: members, source, start: guessStart(source.name)! }
  })
}

const sizeLabel = (bytes: number) =>
  bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(0)} MB` : `${Math.ceil(bytes / 1e3)} KB`

/** Drop or pick videos / .srt / .vi.srt from this computer; files of the same video are shown together. */
export function UploadDrop({ files, onChange, progress, disabled }: {
  files: File[]
  onChange: (files: File[]) => void
  progress: Map<File, number>
  disabled?: boolean
}) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const add = (list: FileList | null) => {
    const picked = [...(list ?? [])].filter((f) => guessStart(f.name))
    // the same name again replaces the earlier pick
    onChange([...files.filter((f) => !picked.some((p) => p.name === f.name)), ...picked])
  }

  return (
    <div className="grid gap-2">
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled) add(e.dataTransfer.files) }}
        className={cn(
          'flex flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground transition-colors hover:bg-accent/50',
          over && 'border-primary bg-primary/5 text-foreground',
        )}
      >
        <Upload className="size-5" />
        <span><span className="font-medium text-foreground">Kéo thả</span> hoặc bấm để chọn nhiều file</span>
        <span className="text-xs">Video/audio, .srt, .vi.srt — phụ đề cùng tên đi chung với video</span>
      </button>
      <input ref={input} type="file" multiple hidden accept="video/*,audio/*,.srt"
        onChange={(e) => { add(e.target.files); e.target.value = '' }} />

      {groupUploads(files).map((group) => (
        <ul key={group.key} className="grid gap-1 rounded-md border p-1.5">
          {group.files.map((file) => {
            const Icon = guessStart(file.name) === 'media' ? FileVideo : Captions
            const done = progress.get(file)
            return (
              <li key={file.name} className="grid gap-1 px-1">
                <div className="flex items-center gap-2 text-sm">
                  <Icon className="size-4 shrink-0 text-muted-foreground" />
                  <span className={cn('truncate', file === group.source && 'font-medium')}>{file.name}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">
                    {done === undefined ? sizeLabel(file.size) : `${Math.round(done * 100)}%`}
                  </span>
                  <Button type="button" variant="ghost" size="icon" className="size-6" disabled={disabled}
                    aria-label={`Bỏ ${file.name}`} onClick={() => onChange(files.filter((f) => f !== file))}>
                    <X />
                  </Button>
                </div>
                {done !== undefined && <Progress value={done * 100} className="h-1" />}
              </li>
            )
          })}
        </ul>
      ))}
    </div>
  )
}
