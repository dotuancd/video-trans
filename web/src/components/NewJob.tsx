import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ChevronDown, FolderOpen, Loader2, Rocket } from 'lucide-react'
import { toast } from 'sonner'
import { addJobs, type Entry } from '@/api'
import { FileBrowser } from '@/components/FileBrowser'
import { Field, StepParams, SwitchField, type PreviewContext } from '@/components/StepParams'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { errorMessage, loadSaved, save, STEP_LABELS, type Options, type StepName } from '@/lib/api'

type Start = 'media' | 'srt' | 'vi_srt'
const STEPS: StepName[] = ['transcribe', 'translate', 'dub', 'mux']
const FIRST_STEP: Record<Start, number> = { media: 0, srt: 1, vi_srt: 2 }
const START_LABELS: Record<Start, string> = { media: 'Video/audio', srt: 'Phụ đề gốc .srt', vi_srt: 'Phụ đề Việt .vi.srt' }

type Form = {
  path: string
  start: Start
  until: StepName
  recursive: boolean
  overwrite: boolean
  out: string
  options: Options
}

function guessStart(path: string, kind?: Entry['kind']): Start | null {
  const name = path.toLowerCase()
  if (kind === 'vi_srt' || name.endsWith('.vi.srt')) return 'vi_srt'
  if (kind === 'srt' || name.endsWith('.srt')) return 'srt'
  if (kind === 'media' || /\.(mp4|mkv|mov|m4v|webm|avi|wav|mp3|m4a|aac|flac|ogg)$/.test(name)) return 'media'
  return null   // a folder: keep what the user chose
}

export function NewJob({ root, defaults, onCreated }: {
  root: string
  defaults: Options
  onCreated: (jobIds: number[]) => void
}) {
  const [form, setForm] = useState<Form>(() => {
    const saved = loadSaved<Form>('new-job', {
      path: '', start: 'media', until: 'translate', recursive: false, overwrite: false, out: '', options: defaults,
    })
    // options saved by an older version may miss fields: fill from the server defaults
    const options = Object.fromEntries(STEPS.map((s) => [s, { ...defaults[s], ...saved.options?.[s] }])) as Options
    return { ...saved, options }
  })
  const [browsing, setBrowsing] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  useEffect(() => save('new-job', form), [form])

  const first = FIRST_STEP[form.start]
  const last = Math.max(STEPS.indexOf(form.until), first)
  const included = STEPS.slice(first, last + 1)
  const update = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  const setPath = (path: string, kind?: Entry['kind']) => update({ path, start: guessStart(path, kind) ?? form.start })
  const isFolder = guessStart(form.path) === null
  // the voice preview clones and reads the chosen file (the server finds its .srt/.vi.srt or video)
  const path = form.path.trim()
  const previewContext: PreviewContext | undefined = isFolder || !path ? undefined
    : form.start === 'media' ? { media: path } : { subtitles: path }

  const create = useMutation({
    mutationFn: async () => {
      const params = Object.assign({}, ...included.map((s) => form.options[s]))
      const { data } = await addJobs({
        body: {
          path: form.path.trim(),
          start: form.start,
          recursive: form.recursive,
          overwrite: form.overwrite,
          out: form.out.trim() || null,
          translate: included.includes('translate'),
          dub: included.includes('dub'),
          mux: included.includes('mux'),
          params,
        },
      })
      return data
    },
    onSuccess: (data) => {
      if (data.jobs.length) toast.success(`Đã thêm ${data.jobs.length} việc vào hàng đợi`)
      data.skipped.forEach((message) => toast.warning(message))
      onCreated(data.jobs)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Việc mới</CardTitle>
        <CardDescription>Chọn video hoặc cả thư mục, chọn bước cuối cần làm rồi bấm Bắt đầu.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-5" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
          <div className="grid gap-2">
            <Label htmlFor="path">File hoặc thư mục</Label>
            <div className="flex gap-2">
              <Input id="path" value={form.path} placeholder={`${root}/Videos`} className="font-mono text-xs"
                onChange={(e) => setPath(e.target.value)} />
              <Button type="button" variant="outline" onClick={() => setBrowsing(true)}>
                <FolderOpen /> Duyệt
              </Button>
            </div>
          </div>

          {isFolder && form.path && (
            <Field label="Trong thư mục, xử lý">
              <Segmented value={form.start} onChange={(start) => update({ start })}
                options={(Object.keys(START_LABELS) as Start[]).map((s) => ({ value: s, label: START_LABELS[s] }))} />
            </Field>
          )}

          <Field label="Làm đến bước">
            <Segmented value={STEPS[last]} onChange={(until) => update({ until })}
              options={STEPS.map((s, i) => ({ value: s, label: STEP_LABELS[s], disabled: i < first, dim: i < first }))}
              fill={(value) => STEPS.indexOf(value) >= first && STEPS.indexOf(value) <= last} />
          </Field>

          {included.filter((s) => s === 'translate' || s === 'dub').map((s) => (
            <StepParams key={s} name={s} mode="main" value={form.options[s]} context={previewContext}
              onChange={(v) => update({ options: { ...form.options, [s]: v } })} />
          ))}

          <Collapsible open={advanced} onOpenChange={setAdvanced}>
            <CollapsibleTrigger asChild>
              <Button type="button" variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
                <ChevronDown className={cn('transition-transform', advanced && 'rotate-180')} />
                Tuỳ chọn nâng cao
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="grid gap-4 pt-3">
              <SwitchField label="Quét thư mục con" checked={form.recursive} onChange={(recursive) => update({ recursive })} />
              <SwitchField label="Ghi đè file đã có" hint="Mặc định bỏ qua bước đã có kết quả"
                checked={form.overwrite} onChange={(overwrite) => update({ overwrite })} />
              <Field label="Thư mục output" hint="Để trống = cạnh file gốc">
                <Input value={form.out} className="font-mono text-xs" onChange={(e) => update({ out: e.target.value })} />
              </Field>
              {included.map((s) => (
                <AdvancedStep key={s} name={s} form={form} update={update} />
              ))}
            </CollapsibleContent>
          </Collapsible>

          <Button type="submit" size="lg" disabled={!form.path.trim() || create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Rocket />}
            Bắt đầu
          </Button>
        </form>
      </CardContent>
      <FileBrowser open={browsing} onOpenChange={setBrowsing} initial={form.path || root}
        onPick={({ path, kind }) => setPath(path, kind)} />
    </Card>
  )
}

/** The options of one step that are not in the main form. */
function AdvancedStep({ name, form, update }: { name: StepName; form: Form; update: (p: Partial<Form>) => void }) {
  return (
    <div className="grid gap-3">
      <Separator />
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{STEP_LABELS[name]}</p>
      <StepParams name={name} mode="extra" value={form.options[name]}
        onChange={(v) => update({ options: { ...form.options, [name]: v } })} />
    </div>
  )
}

export function Segmented<T extends string>({ value, onChange, options, fill }: {
  value: T
  onChange: (value: T) => void
  options: { value: T; label: string; disabled?: boolean; dim?: boolean }[]
  fill?: (value: T) => boolean
}) {
  return (
    <div className="flex rounded-lg border bg-muted/40 p-1">
      {options.map((option) => {
        const on = fill ? fill(option.value) : option.value === value
        return (
          <button
            key={option.value}
            type="button"
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex-1 rounded-md px-1 py-1.5 text-xs font-medium whitespace-nowrap transition-colors sm:text-sm',
              option.value === value
                ? 'bg-primary text-primary-foreground shadow-sm'
                : on
                  ? 'bg-primary/15 text-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              option.dim && 'opacity-40',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
