import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Play, Plus, X } from 'lucide-react'
import { toast } from 'sonner'
import { listPresetVoices, type DriverInfo, type LibraryVoice } from '@/api'
import { useVoiceLibrary, VoiceCreator } from '@/components/Voices'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { errorMessage, fileName, useProjects, type Params } from '@/lib/api'
import { findJob } from '@/lib/jobs'

/** voice value: clone the speaker of the video (the dub driver's default) */
const SOURCE_VOICE = 'source'
/** select value prefix of a library voice */
const LIBRARY = 'lib:'

/** What the voice preview clones from and reads: a job (its video and subtitles of `lang`), or a video
 * on the server (with <name>.srt / <name>.vi.srt next to it). */
export type PreviewContext = { job_id?: string; lang?: string; path?: string; project_id?: string | null }

type Prop = {
  type?: string
  title?: string
  description?: string
  format?: string
  default?: unknown
  enum?: string[]
  anyOf?: Prop[]
  minimum?: number
  maximum?: number
  additionalProperties?: Prop
}
type Schema = { properties?: Record<string, Prop>; required?: string[] }

/** The parameters of a driver: a form drawn from its JSON Schema, with a voice picker for dubbing. */
export function DriverParams({ driver, value, onChange, context, only }: {
  driver: DriverInfo
  value: Params
  onChange: (value: Params) => void
  context?: PreviewContext
  /** draw only these fields (the main ones), or all but these when prefixed with ! */
  only?: string[]
}) {
  const schema = driver.params_schema as Schema
  const voice = driver.stage === 'dub' && 'voice' in (schema.properties ?? {})
  const skip = [...(voice ? ['voice', 'ref_audio'] : []), ...(schema.required ?? [])]
  const keys = Object.keys(schema.properties ?? {}).filter((k) => !skip.includes(k))
  const shown = only ? keys.filter((k) => (only[0]?.startsWith('!') ? !only.includes(`!${k}`) : only.includes(k))) : keys
  const showVoice = voice && (!only || (only[0]?.startsWith('!') ? !only.includes('!voice') : only.includes('voice')))
  return (
    <div className="grid gap-4">
      {showVoice && <VoicePicker value={value} onChange={onChange} context={context} />}
      {shown.map((key) => (
        <PropField key={key} name={key} prop={schema.properties![key]} value={value[key]}
          onChange={(v) => onChange({ ...value, [key]: v })} />
      ))}
    </div>
  )
}

function PropField({ name, prop, value, onChange }: {
  name: string
  prop: Prop
  value: unknown
  onChange: (value: unknown) => void
}) {
  const nullable = prop.anyOf?.some((p) => p.type === 'null') ?? false
  const base = prop.anyOf?.find((p) => p.type !== 'null') ?? prop
  const label = prop.title ?? name
  const hint = prop.description
  const current = value === undefined ? prop.default : value
  if (base.enum) {
    return (
      <Field label={label} hint={hint}>
        <Select value={String(current ?? '')} onValueChange={onChange}>
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            {base.enum.map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
    )
  }
  if (base.type === 'boolean') {
    return <SwitchField label={label} hint={hint} checked={!!current} onChange={onChange} />
  }
  if (base.type === 'integer' || base.type === 'number') {
    const { minimum: min, maximum: max } = base
    if (min !== undefined && max !== undefined) {
      const step = base.type === 'integer' ? 1 : max - min > 2 ? 0.5 : 0.05
      return <SliderField label={label} hint={hint} min={min} max={max} step={step}
        value={Number(current ?? min)} onChange={onChange}
        format={(v) => (base.type === 'integer' ? String(v) : v.toFixed(step < 0.1 ? 2 : 1))} />
    }
    return (
      <Field label={label} hint={hint}>
        <Input type="number" value={current === null || current === undefined ? '' : String(current)}
          onChange={(e) => onChange(e.target.value === '' && nullable ? null : Number(e.target.value))} />
      </Field>
    )
  }
  if (base.type === 'object') {
    return <MapField label={label} hint={hint} value={(current as Record<string, string>) ?? {}} onChange={onChange} />
  }
  if (prop.format === 'textarea') {
    return (
      <Field label={label} hint={hint}>
        <Textarea rows={4} value={(current as string) ?? ''} onChange={(e) => onChange(e.target.value)} />
      </Field>
    )
  }
  return (
    <Field label={label} hint={hint}>
      <Input value={(current as string) ?? ''} onChange={(e) => onChange(e.target.value === '' && nullable ? null : e.target.value)} />
    </Field>
  )
}

/** A string → string map as "a => b" lines (replacement lists, glossaries). */
function MapField({ label, hint, value, onChange }: {
  label: string
  hint?: string
  value: Record<string, string>
  onChange: (value: Record<string, string>) => void
}) {
  const [text, setText] = useState(() => Object.entries(value).map(([k, v]) => `${k} => ${v}`).join('\n'))
  const parse = (raw: string) => Object.fromEntries(raw.split('\n')
    .map((line) => line.split('=>').map((s) => s.trim()))
    .filter(([k, v]) => k && v !== undefined))
  return (
    <Field label={label} hint={`${hint ? `${hint} · ` : ''}mỗi dòng: sai => đúng`}>
      <Textarea rows={3} value={text} className="font-mono text-xs" placeholder="cloud code => Claude Code"
        onChange={(e) => { setText(e.target.value); onChange(parse(e.target.value)) }} />
    </Field>
  )
}

export function VoicePicker({ value, onChange, context }: {
  value: Params
  onChange: (v: Params) => void
  context?: PreviewContext
}) {
  const queryClient = useQueryClient()
  const presets = useQuery({
    queryKey: ['voices', 'presets'],
    queryFn: async () => (await listPresetVoices()).data,
    staleTime: Infinity,
    retry: false,
  })
  const library = useVoiceLibrary()
  const projects = useProjects()
  const [creating, setCreating] = useState(false)
  const [heard, setHeard] = useState<{ url: string; text: string }>()
  const { project_id: contextProject, ...previewContext } = context ?? {}
  // the project whose voices come first: given, or the job's
  const projectId = contextProject ?? (context?.job_id ? findJob(queryClient, context.job_id)?.project_id : undefined)
  const refAudio = (value.ref_audio as string | null | undefined) ?? null
  const voice = (value.voice as string | undefined) ?? SOURCE_VOICE
  const own = library.data?.find((v) => v.path === refAudio)
  const fromSource = !refAudio && voice === SOURCE_VOICE
  const canPreview = fromSource ? !!(previewContext.job_id || previewContext.path) : true
  const mine = (library.data ?? []).filter((v) => projectId && v.project_id === projectId)
  const shared = (library.data ?? []).filter((v) => !v.project_id)
  const others = (library.data ?? []).filter((v) => v.project_id && v.project_id !== projectId)
  const projectName = (id: string) => projects.data?.find((p) => p.id === id)?.name

  const pick = (selected: string) => {
    const voice = library.data?.find((v) => `${LIBRARY}${v.id}` === selected)
    onChange(voice ? { ...value, voice: SOURCE_VOICE, ref_audio: voice.path } : { ...value, voice: selected, ref_audio: null })
  }
  const preview = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/voices/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // no text: the server reads a sentence of the subtitles (the moment the voice was cloned from)
        body: JSON.stringify({ voice, ref_audio: refAudio, int8: !!value.int8, ...previewContext }),
      })
      if (!response.ok) throw await response.json().catch(() => new Error(response.statusText))
      const text = decodeURIComponent(response.headers.get('X-Preview-Text') ?? '')
      return { url: URL.createObjectURL(await response.blob()), text }
    },
    onSuccess: (result) => {
      if (heard) URL.revokeObjectURL(heard.url)
      setHeard(result)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const placeholder = presets.isLoading ? 'Đang tải giọng…' : presets.isError ? errorMessage(presets.error) : 'Giọng mặc định'
  const item = (v: LibraryVoice, suffix?: string) => (
    <SelectItem key={v.id} value={`${LIBRARY}${v.id}`}>{v.name}{suffix ? ` · ${suffix}` : ''}</SelectItem>
  )
  return (
    <div className="grid gap-2">
      <Label>Giọng đọc</Label>
      <div className="flex gap-2">
        {refAudio && !own ? (
          <div className="flex h-9 w-0 min-w-0 flex-1 items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm">
            <span className="truncate">Clone: {fileName(refAudio)}</span>
            <button type="button" className="ml-auto text-muted-foreground hover:text-foreground"
              aria-label="Bỏ giọng clone" onClick={() => onChange({ ...value, ref_audio: null })}>
              <X className="size-4" />
            </button>
          </div>
        ) : (
          <Select value={own ? `${LIBRARY}${own.id}` : voice} onValueChange={pick}>
            <SelectTrigger className="w-0 min-w-0 flex-1 overflow-hidden *:data-[slot=select-value]:block *:data-[slot=select-value]:truncate">
              <SelectValue placeholder={placeholder} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SOURCE_VOICE}>Giọng gốc (tự clone từ video)</SelectItem>
              {mine.length > 0 && <SelectGroup><SelectLabel>Của project</SelectLabel>{mine.map((v) => item(v))}</SelectGroup>}
              {shared.length > 0 && <SelectGroup><SelectLabel>Dùng chung</SelectLabel>{shared.map((v) => item(v))}</SelectGroup>}
              {others.length > 0 && (
                <SelectGroup><SelectLabel>Project khác</SelectLabel>{others.map((v) => item(v, projectName(v.project_id!)))}</SelectGroup>
              )}
              {!!presets.data?.length && (
                <SelectGroup>
                  <SelectLabel>Giọng mẫu</SelectLabel>
                  {presets.data.map((v) => <SelectItem key={v.name} value={v.name}>{v.label}</SelectItem>)}
                </SelectGroup>
              )}
            </SelectContent>
          </Select>
        )}
        <Button type="button" variant="outline" size="icon" title="Tạo giọng từ file có người nói"
          onClick={() => setCreating(true)}>
          <Plus />
        </Button>
        <Button type="button" variant="outline" size="icon" onClick={() => preview.mutate()}
          title={canPreview ? 'Nghe thử một câu của phụ đề' : 'Chọn một video trên máy để nghe thử giọng gốc'}
          disabled={preview.isPending || !canPreview}>
          {preview.isPending ? <Loader2 className="animate-spin" /> : <Play />}
        </Button>
      </div>
      {fromSource && (
        <p className="text-xs text-muted-foreground">
          Tự chọn 3–8 giây người nói rõ nhất trong video. Giọng chưa ưng: bấm + để tự chọn đoạn và lưu lại dùng tiếp.
        </p>
      )}
      {heard && (
        <>
          <audio src={heard.url} controls autoPlay className="h-9 w-full" />
          {heard.text && <p className="text-xs text-muted-foreground italic">“{heard.text}”</p>}
        </>
      )}
      {creating && <VoiceCreator open={creating} onOpenChange={setCreating} projects={projects.data ?? []} projectId={projectId}
        jobId={previewContext.job_id} lang={previewContext.lang}
        onCreated={(v) => onChange({ ...value, voice: SOURCE_VOICE, ref_audio: v.path })} />}
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function SliderField({ label, hint, value, onChange, min, max, step, format = String }: {
  label: string
  hint?: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step: number
  format?: (v: number) => string
}) {
  return (
    <div className="grid gap-3">
      <div className="flex items-baseline justify-between">
        <Label>{label}</Label>
        <span className="text-sm tabular-nums text-muted-foreground">{format(value)}</span>
      </div>
      <Slider value={[value]} min={min} max={max} step={step} onValueChange={([v]) => onChange(v)} />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function SwitchField({ label, hint, checked, onChange, disabled }: {
  label: string
  hint?: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4">
      <span className="grid gap-0.5">
        <span className="text-sm font-medium">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </span>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </label>
  )
}

/** Pick the driver of a stage; drivers that can't run here are shown with the reason, disabled. */
export function DriverSelect({ drivers, value, onChange }: {
  drivers: DriverInfo[]
  value: string
  onChange: (name: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent>
        {drivers.map((d) => (
          <SelectItem key={d.name} value={d.name} disabled={!!d.available}>
            {d.title}{d.available ? ` — ${d.available}` : ''}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
