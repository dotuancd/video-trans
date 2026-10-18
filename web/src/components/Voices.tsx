import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AudioLines, Loader2, Minus, Play, Plus, Trash2, Upload, Wand2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  analyzeVoice, createVoice, deleteVoice, listVoices, saveVoice,
  type LibraryVoice, type Project, type VoiceAnalysis, type VoiceCandidate,
} from '@/api'
import { Dot, COLORS } from '@/components/Projects'
import { Field } from '@/components/Params'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { errorMessage, fileName, uploadFile } from '@/lib/api'

/** A voice clip is 3–8 seconds (VieNeu clones from that) */
const MIN_SECONDS = 3
const MAX_SECONDS = 8
const NUDGE = 0.5
/** project select value: shared by every project */
const SHARED = 'shared'

export const useVoiceLibrary = () =>
  useQuery({ queryKey: ['voice-library'], queryFn: async () => (await listVoices()).data })

const clock = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`
const segmentUrl = (source: string, start: number, end: number) =>
  `/api/voices/segment?${new URLSearchParams({ source, start: String(start), end: String(end) })}`

/** Reads a sample sentence in the voice cloned from `body` (a window or a library voice). */
async function previewUrl(body: Record<string, unknown>) {
  const response = await fetch('/api/voices/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'Xin chào, đây là giọng đọc thử cho video của bạn.', ...body }),
  })
  if (!response.ok) throw await response.json().catch(() => new Error(response.statusText))
  return URL.createObjectURL(await response.blob())
}

/** Make a library voice: a file (audio or video) or a job's video → the 5 best 3–8s windows to hear
 * as they are and as the cloned voice, ends nudged, then named and saved. */
export function VoiceCreator({ open, onOpenChange, projects, projectId, jobId, lang = 'vi', onCreated }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  projects: Project[]
  projectId?: string | null
  /** offer this job's video as the recording */
  jobId?: string
  lang?: string
  onCreated?: (voice: LibraryVoice) => void
}) {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [progress, setProgress] = useState<number>()
  const [analysis, setAnalysis] = useState<VoiceAnalysis>()
  const [windows, setWindows] = useState<VoiceCandidate[]>([])
  const [chosen, setChosen] = useState(0)
  const [name, setName] = useState('')
  const [scope, setScope] = useState(projectId ?? SHARED)
  const [playing, setPlaying] = useState<{ url: string; label: string }>()
  const [dragging, setDragging] = useState(false)

  const reset = () => {
    setAnalysis(undefined)
    setWindows([])
    setChosen(0)
    setName('')
    setScope(projectId ?? SHARED)
    setPlaying(undefined)
    setProgress(undefined)
  }
  const found = (result: VoiceAnalysis, title: string) => {
    setAnalysis(result)
    setWindows(result.candidates)
    setChosen(0)
    setName(title)
  }

  const analyze = useMutation({
    mutationFn: async (from: File | 'job') => {
      if (from === 'job') return { result: (await analyzeVoice({ body: { job_id: jobId, lang } })).data, title: '' }
      setProgress(0)
      const uploaded = await uploadFile(from, undefined, setProgress)
      setProgress(undefined)
      return { result: (await analyzeVoice({ body: { path: uploaded.path } })).data, title: from.name.replace(/\.[^.]+$/, '') }
    },
    onSuccess: ({ result, title }) => found(result, title),
    onError: (error) => { setProgress(undefined); toast.error(errorMessage(error)) },
  })
  const hear = useMutation({
    mutationFn: async (w: VoiceCandidate) => previewUrl({ source: analysis!.source, start: w.start, end: w.end }),
    onSuccess: (url) => setPlaying({ url, label: 'Giọng clone đọc thử' }),
    onError: (error) => toast.error(errorMessage(error)),
  })
  const save = useMutation({
    mutationFn: async () => {
      const w = windows[chosen]
      return (await createVoice({ body: {
        name: name.trim(), project_id: scope === SHARED ? null : scope, source: analysis!.source, start: w.start, end: w.end,
      } })).data
    },
    onSuccess: (voice) => {
      queryClient.setQueryData<LibraryVoice[]>(['voice-library'], (list = []) => [...list.filter((v) => v.id !== voice.id), voice])
      toast.success(`Đã lưu giọng ${voice.name}`)
      onCreated?.(voice)
      onOpenChange(false)
      reset()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const nudge = (index: number, edge: 'start' | 'end', by: number) => setWindows((list) => list.map((w, i) => {
    if (i !== index) return w
    const duration = analysis?.duration ?? Infinity
    const next = { ...w, [edge]: Math.round((w[edge] + by) * 100) / 100 }
    const length = next.end - next.start
    return next.start < 0 || next.end > duration || length < MIN_SECONDS || length > MAX_SECONDS ? w : next
  }))

  const busy = analyze.isPending
  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Tạo giọng đọc</DialogTitle>
          <DialogDescription>
            Chọn file audio hoặc video có người nói. Hệ thống gợi ý 5 đoạn 3–8 giây nói rõ nhất, bạn nghe và chọn một.
          </DialogDescription>
        </DialogHeader>

        {!analysis ? (
          <div className="grid gap-3">
            <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                const file = e.dataTransfer.files[0]
                if (file) analyze.mutate(file)
              }}
              className={cn('grid place-items-center gap-2 rounded-lg border-2 border-dashed p-8 text-sm text-muted-foreground transition-colors hover:bg-accent',
                dragging && 'border-primary bg-accent')}>
              {busy ? <Loader2 className="size-6 animate-spin" /> : <Upload className="size-6" />}
              {progress !== undefined ? `Đang tải lên ${Math.round(progress * 100)}%`
                : busy ? 'Đang tìm đoạn nói rõ nhất…' : 'Kéo thả hoặc bấm để chọn file (mp3, wav, mp4…)'}
            </button>
            {jobId && (
              <Button type="button" variant="outline" disabled={busy} onClick={() => analyze.mutate('job')}>
                <Wand2 /> Lấy từ video của việc này
              </Button>
            )}
            <input ref={fileInput} type="file" accept="audio/*,video/*" hidden
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) analyze.mutate(file)
                e.target.value = ''
              }} />
          </div>
        ) : (
          <div className="grid gap-4">
            <p className="truncate text-xs text-muted-foreground">
              {fileName(analysis.source)} · {clock(analysis.duration)}
              <button type="button" className="ml-2 underline hover:text-foreground" onClick={reset}>Chọn file khác</button>
            </p>
            <ul className="grid gap-2">
              {windows.map((w, i) => (
                <li key={i} className={cn('grid gap-2 rounded-md border p-2', chosen === i && 'border-primary ring-1 ring-primary')}>
                  <label className="flex cursor-pointer items-center gap-3 text-sm">
                    <input type="radio" name="voice-window" checked={chosen === i} onChange={() => setChosen(i)} />
                    <span className="tabular-nums">{clock(w.start)} – {clock(w.end)}</span>
                    <span className="text-muted-foreground tabular-nums">{(w.end - w.start).toFixed(1)}s</span>
                    <span className="ml-auto flex items-center gap-2" title={`Độ rõ tiếng nói ${Math.round(w.score * 100)}%`}>
                      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                        <span className="block h-full bg-primary" style={{ width: `${Math.round(w.score * 100)}%` }} />
                      </span>
                      <span className="w-8 text-right text-xs text-muted-foreground tabular-nums">{Math.round(w.score * 100)}%</span>
                    </span>
                  </label>
                  <div className="flex flex-wrap items-center gap-1">
                    <Button type="button" size="sm" variant="outline"
                      onClick={() => { setChosen(i); setPlaying({ url: segmentUrl(analysis.source, w.start, w.end), label: 'Đoạn gốc' }) }}>
                      <Play /> Nghe gốc
                    </Button>
                    <Button type="button" size="sm" variant="outline" disabled={hear.isPending}
                      title="Clone giọng từ đoạn này và đọc một câu tiếng Việt"
                      onClick={() => { setChosen(i); hear.mutate(w) }}>
                      {hear.isPending && hear.variables === w ? <Loader2 className="animate-spin" /> : <AudioLines />} Nghe thử giọng
                    </Button>
                    <span className="ml-auto flex items-center gap-0.5 text-xs text-muted-foreground">
                      Đầu
                      <Button type="button" size="icon" variant="ghost" className="size-7" aria-label="Lùi đầu" onClick={() => nudge(i, 'start', -NUDGE)}><Minus /></Button>
                      <Button type="button" size="icon" variant="ghost" className="size-7" aria-label="Tiến đầu" onClick={() => nudge(i, 'start', NUDGE)}><Plus /></Button>
                      Cuối
                      <Button type="button" size="icon" variant="ghost" className="size-7" aria-label="Lùi cuối" onClick={() => nudge(i, 'end', -NUDGE)}><Minus /></Button>
                      <Button type="button" size="icon" variant="ghost" className="size-7" aria-label="Tiến cuối" onClick={() => nudge(i, 'end', NUDGE)}><Plus /></Button>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {playing && (
              <div className="grid gap-1">
                <span className="text-xs text-muted-foreground">{playing.label}</span>
                <audio key={playing.url} src={playing.url} controls autoPlay className="h-9 w-full" />
              </div>
            )}
            <div className="grid gap-3 border-t pt-4 sm:grid-cols-2">
              <Field label="Tên giọng">
                <Input value={name} placeholder="VD: Thầy Minh" onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && name.trim() && save.mutate()} />
              </Field>
              <Field label="Dùng cho">
                <ScopeSelect value={scope} onChange={setScope} projects={projects} />
              </Field>
            </div>
            <Button type="button" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
              {save.isPending && <Loader2 className="animate-spin" />} Lưu giọng
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ScopeSelect({ value, onChange, projects }: { value: string; onChange: (v: string) => void; projects: Project[] }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value={SHARED}>Dùng chung</SelectItem>
        {projects.map((p) => (
          <SelectItem key={p.id} value={p.id}>
            <Dot color={p.color ?? COLORS[0]} /> {p.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** Every library voice: hear, rename, move between projects, delete; and make new ones. */
export function VoiceLibrary({ projects }: { projects: Project[] }) {
  const voices = useVoiceLibrary()
  const [creating, setCreating] = useState(false)
  const groups = [
    { id: null as string | null, name: 'Dùng chung', color: undefined as string | undefined },
    ...projects.map((p) => ({ id: p.id as string | null, name: p.name, color: p.color ?? COLORS[0] })),
  ].map((g) => ({ ...g, voices: (voices.data ?? []).filter((v) => (v.project_id ?? null) === g.id) }))
    .filter((g) => g.id === null || g.voices.length > 0)

  return (
    <div className="grid gap-5">
      <div className="flex items-center gap-3">
        <p className="text-sm text-muted-foreground">
          Giọng dùng để lồng tiếng: tạo từ file có người nói, chọn trong phần Lồng tiếng của việc hoặc project.
        </p>
        <Button size="sm" className="ml-auto shrink-0" onClick={() => setCreating(true)}><Plus /> Tạo giọng</Button>
      </div>
      {voices.isLoading ? <Loader2 className="animate-spin text-muted-foreground" /> : groups.map((g) => (
        <section key={g.id ?? SHARED} className="grid gap-2">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            {g.color && <Dot color={g.color} />} {g.name}
            <span className="text-xs font-normal text-muted-foreground tabular-nums">{g.voices.length}</span>
          </h3>
          {g.voices.length === 0
            ? <p className="text-sm text-muted-foreground">Chưa có giọng nào.</p>
            : <ul className="grid gap-2">{g.voices.map((v) => <VoiceRow key={v.id} voice={v} projects={projects} />)}</ul>}
        </section>
      ))}
      {creating && <VoiceCreator open={creating} onOpenChange={setCreating} projects={projects} />}
    </div>
  )
}

function VoiceRow({ voice, projects }: { voice: LibraryVoice; projects: Project[] }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(voice.name)
  const [confirming, setConfirming] = useState(false)
  const [heard, setHeard] = useState<string>()
  const put = (v: LibraryVoice) =>
    queryClient.setQueryData<LibraryVoice[]>(['voice-library'], (list = []) => list.map((x) => (x.id === v.id ? v : x)))

  const save = useMutation({
    mutationFn: async (patch: { name?: string; project_id?: string | null }) => (await saveVoice({
      path: { voice_id: voice.id }, body: { name: voice.name, project_id: voice.project_id ?? null, ...patch },
    })).data,
    onSuccess: put,
    onError: (error) => { setName(voice.name); toast.error(errorMessage(error)) },
  })
  const remove = useMutation({
    mutationFn: async () => deleteVoice({ path: { voice_id: voice.id } }),
    onSuccess: () => queryClient.setQueryData<LibraryVoice[]>(['voice-library'], (list = []) => list.filter((x) => x.id !== voice.id)),
    onError: (error) => toast.error(errorMessage(error)),
  })
  const hear = useMutation({
    mutationFn: async () => previewUrl({ voice_id: voice.id }),
    onSuccess: setHeard,
    onError: (error) => toast.error(errorMessage(error)),
  })

  const length = voice.start != null && voice.end != null ? `${(voice.end - voice.start).toFixed(1)}s` : null
  return (
    <li className="grid gap-2 rounded-md border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input value={name} className="h-8 w-48 min-w-0 flex-1 sm:flex-none" aria-label="Tên giọng"
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== voice.name ? save.mutate({ name }) : setName(voice.name)}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
        <span className="truncate text-xs text-muted-foreground">
          {[voice.origin, length].filter(Boolean).join(' · ')}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <div className="w-44"><ScopeSelect value={voice.project_id ?? SHARED} projects={projects}
            onChange={(v) => save.mutate({ project_id: v === SHARED ? null : v })} /></div>
          <Button type="button" size="icon" variant="ghost" title="Nghe thử giọng đọc tiếng Việt" disabled={hear.isPending}
            onClick={() => hear.mutate()}>
            {hear.isPending ? <Loader2 className="animate-spin" /> : <AudioLines />}
          </Button>
          <Button type="button" size="icon" variant={confirming ? 'destructive' : 'ghost'} disabled={remove.isPending}
            title={confirming ? 'Bấm lần nữa để xoá' : 'Xoá giọng'}
            onClick={() => (confirming ? remove.mutate() : setConfirming(true))} onBlur={() => setConfirming(false)}>
            <Trash2 />
          </Button>
        </div>
      </div>
      <audio src={`/api/voices/${voice.id}/audio`} controls preload="none" className="h-8 w-full" />
      {heard && <audio src={heard} controls autoPlay className="h-8 w-full" />}
    </li>
  )
}
