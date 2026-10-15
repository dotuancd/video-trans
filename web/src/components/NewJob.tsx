import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ChevronDown, FolderOpen, HardDrive, Loader2, Rocket, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { addJobs, type Entry } from '@/api'
import { FileBrowser } from '@/components/FileBrowser'
import { groupUploads, UploadDrop } from '@/components/UploadDrop'
import { Field, StepParams, SwitchField, type PreviewContext } from '@/components/StepParams'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import {
  errorMessage, guessStart, loadSaved, save, STEP_LABELS, uploadFile, type Options, type Start, type StepName,
} from '@/lib/api'

const STEPS: StepName[] = ['transcribe', 'translate', 'dub', 'mux']
const FIRST_STEP: Record<Start, number> = { media: 0, srt: 1, vi_srt: 2 }
const START_LABELS: Record<Start, string> = { media: 'Video/audio', srt: 'Phụ đề gốc .srt', vi_srt: 'Phụ đề Việt .vi.srt' }

type Form = {
  /** files uploaded from this computer, or a path on the server */
  from: 'upload' | 'server'
  path: string
  start: Start
  until: StepName
  recursive: boolean
  overwrite: boolean
  out: string
  options: Options
}

export function NewJob({ root, defaults, onCreated }: {
  root: string
  defaults: Options
  onCreated: (jobIds: number[]) => void
}) {
  const [form, setForm] = useState<Form>(() => {
    const saved = loadSaved<Form>('new-job', {
      from: 'upload', path: '', start: 'media', until: 'translate', recursive: false, overwrite: false, out: '', options: defaults,
    })
    // options saved by an older version may miss fields: fill from the server defaults
    const options = Object.fromEntries(STEPS.map((s) => [s, { ...defaults[s], ...saved.options?.[s] }])) as Options
    return { ...saved, options }
  })
  const [browsing, setBrowsing] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [progress, setProgress] = useState(new Map<File, number>())
  useEffect(() => save('new-job', form), [form])

  const uploading = form.from === 'upload'
  const groups = groupUploads(files)
  // uploads may mix videos and subtitles: the steps shown start from the earliest of them
  const first = uploading
    ? Math.min(...groups.map((g) => FIRST_STEP[g.start]), groups.length ? Infinity : 0)
    : FIRST_STEP[form.start]
  const last = Math.max(STEPS.indexOf(form.until), first)
  const included = STEPS.slice(first, last + 1)
  const update = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  const setPath = (path: string, kind?: Entry['kind']) => update({ path, start: guessStart(path, kind) ?? form.start })
  const isFolder = guessStart(form.path) === null
  // the voice preview clones and reads the chosen file (the server finds its .srt/.vi.srt or video)
  const path = form.path.trim()
  const previewContext: PreviewContext | undefined = uploading || isFolder || !path ? undefined
    : form.start === 'media' ? { media: path } : { subtitles: path }

  const create = useMutation({
    mutationFn: async () => {
      const params = Object.assign({}, ...included.map((s) => form.options[s]))
      if (uploading) return uploadAndAdd(params)
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
      if (uploading) {
        setFiles([])
        setProgress(new Map())
      }
      if (data.jobs.length) toast.success(`Đã thêm ${data.jobs.length} việc vào hàng đợi`)
      data.skipped.forEach((message) => toast.warning(message))
      onCreated(data.jobs)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  /** Each video with its subtitles into a folder of its own (two videos at a time), then a job per video. */
  async function uploadAndAdd(params: Record<string, unknown>) {
    const sources: { start: Start; path: string }[] = []
    const queue = [...groups]
    const worker = async () => {
      for (let group = queue.shift(); group; group = queue.shift()) {
        let id: string | undefined
        for (const file of group.files) {
          const uploaded = await uploadFile(file, id, (done) => setProgress((p) => new Map(p).set(file, done)))
          id = uploaded.id
          if (file === group.source) sources.push({ start: group.start, path: uploaded.path })
        }
      }
    }
    await Promise.all([worker(), worker()])
    const result = { jobs: [] as number[], skipped: [] as string[] }
    for (const start of new Set(sources.map((s) => s.start))) {
      const steps = STEPS.slice(FIRST_STEP[start], Math.max(last, FIRST_STEP[start]) + 1)
      const { data } = await addJobs({
        body: {
          paths: sources.filter((s) => s.start === start).map((s) => s.path),
          start,
          overwrite: form.overwrite,
          translate: steps.includes('translate'),
          dub: steps.includes('dub'),
          mux: steps.includes('mux'),
          params,
        },
      })
      result.jobs.push(...data.jobs)
      result.skipped.push(...data.skipped)
    }
    return result
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Việc mới</CardTitle>
        <CardDescription>Tải lên video (kèm phụ đề nếu có), chọn bước cuối cần làm rồi bấm Bắt đầu.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-5" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
          <Tabs value={form.from} onValueChange={(from) => update({ from: from as Form['from'] })}>
            <TabsList className="w-full">
              <TabsTrigger value="upload"><Upload /> Tải lên</TabsTrigger>
              <TabsTrigger value="server"><HardDrive /> Trên máy chủ</TabsTrigger>
            </TabsList>
            <TabsContent value="upload" className="pt-2">
              <UploadDrop files={files} onChange={setFiles} progress={progress} disabled={create.isPending} />
            </TabsContent>
            <TabsContent value="server" className="grid gap-5 pt-2">
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
            </TabsContent>
          </Tabs>

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
              {!uploading && (
                <SwitchField label="Quét thư mục con" checked={form.recursive} onChange={(recursive) => update({ recursive })} />
              )}
              <SwitchField label="Ghi đè file đã có" hint="Mặc định bỏ qua bước đã có kết quả"
                checked={form.overwrite} onChange={(overwrite) => update({ overwrite })} />
              {!uploading && (
                <Field label="Thư mục output" hint="Để trống = cạnh file gốc">
                  <Input value={form.out} className="font-mono text-xs" onChange={(e) => update({ out: e.target.value })} />
                </Field>
              )}
              {included.map((s) => (
                <AdvancedStep key={s} name={s} form={form} update={update} />
              ))}
            </CollapsibleContent>
          </Collapsible>

          <Button type="submit" size="lg" disabled={(uploading ? !files.length : !form.path.trim()) || create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Rocket />}
            {create.isPending && uploading ? 'Đang tải lên…' : 'Bắt đầu'}
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
