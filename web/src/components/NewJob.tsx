import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { FolderOpen, HardDrive, Link, Loader2, Rocket, SlidersHorizontal, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { createJobs, listPresets, type Config, type DriverInfo, type Entry, type Preset, type TemplateInfo } from '@/api'
import { AdvancedOptions, changedCount, Count, GENERAL, resetForm, type Stage, type StageForm } from '@/components/AdvancedOptions'
import { FileBrowser } from '@/components/FileBrowser'
import { DriverParams, DriverSelect, Field, SwitchField, type PreviewContext } from '@/components/Params'
import { groupUploads, UploadDrop } from '@/components/UploadDrop'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { driversOf, errorMessage, loadSaved, save, uploadFile, type Params } from '@/lib/api'

const MEDIA = /\.(mp4|mkv|mov|m4v|webm|avi|wav|mp3|m4a|aac|flac|ogg)$/i

/** Select value of "no preset": the template as it is */
const NO_PRESET = '-'
/** What "Mặc định" sets: what a preset is compared with to tell it was changed */
const BLANK: Omit<Preset, 'name'> = { export: true, stages: {} }
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

type Form = {
  from: 'upload' | 'server' | 'url'
  path: string
  urls: string
  recursive: boolean
  useSubtitles: boolean
  export: boolean
  template: string
  langs: string[]
  /** the preset the options were taken from ('' = none) */
  preset: string
  stages: Record<string, StageForm>
}

export function NewJob({ config, templates, drivers, onCreated }: {
  config: Config
  templates: TemplateInfo[]
  drivers: DriverInfo[]
  onCreated: (jobIds: string[]) => void
}) {
  const [form, setForm] = useState<Form>(() => loadSaved<Form>('new-job-v2', {
    from: 'upload', path: '', urls: '', recursive: false, useSubtitles: true, export: true,
    template: 'default', langs: config.default_langs, preset: '', stages: {},
  }))
  const [browsing, setBrowsing] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [progress, setProgress] = useState(new Map<File, number>())
  useEffect(() => save('new-job-v2', form), [form])
  const update = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))

  const template = templates.find((t) => t.name === form.template) ?? templates[0]
  // the template's stages as the user set them: driver, on/off and params
  const stages: Stage[] = (template?.stages ?? []).filter((s) => s.stage !== 'source').map((conf) => {
    const choices = driversOf(drivers, conf.stage as string)
    const set = form.stages[conf.stage as string] ?? {}
    const fallback = conf.driver === 'env' ? config.default_drivers[conf.stage as string] : (conf.driver as string)
    const name = set.driver ?? fallback
    const driver = choices.find((d) => d.name === name)
    return {
      stage: conf.stage as string,
      enabled: !!driver && (set.enabled ?? (conf.enabled as boolean)),
      base: { enabled: !!choices.find((d) => d.name === fallback) && (conf.enabled as boolean), driver: fallback },
      driver, drivers: choices,
      params: { ...(conf.params as Params), ...(set.params?.[name] ?? {}) },
      defaults: conf.params as Params,
    }
  })
  const stageOf = (name: string) => stages.find((s) => s.stage === name && s.enabled)
  const setStage = (stage: string, patch: StageForm) =>
    update({ stages: { ...form.stages, [stage]: { ...form.stages[stage], ...patch } } })
  const setParams = (s: Stage, params: Params) =>
    setStage(s.stage, { params: { ...form.stages[s.stage]?.params, [s.driver!.name]: params } })
  const resetStage = (stage: string) => {
    if (stage !== GENERAL) {
      const s = stages.find((x) => x.stage === stage)!
      const { [stage]: _old, ...others } = form.stages
      const kept = resetForm(s, form.stages[stage])
      return update({ stages: kept ? { ...others, [stage]: kept } : others })
    }
    // on/off of the optional steps lives in Chung
    const stagesOn = Object.fromEntries(Object.entries(form.stages).map(([k, v]) => [k, { ...v, enabled: undefined }]))
    update({ export: true, stages: stagesOn })
  }

  const presets = useQuery({ queryKey: ['presets'], queryFn: async () => (await listPresets()).data, staleTime: Infinity })
  const picked = presets.data?.find((p) => p.name === form.preset)
  const options: Omit<Preset, 'name'> = { export: form.export, stages: form.stages }
  const edited = !same(clean(options), clean(picked ?? BLANK))
  const pickPreset = (name: string) => {
    const p = presets.data?.find((x) => x.name === name)
    update({ preset: p ? name : '', export: p?.export ?? true, stages: (p?.stages ?? {}) as Form['stages'] })
  }
  /** after saving: the options stay, only the name they belong to changes */
  const namePreset = (name: string) => update({ preset: name })
  const changed = (form.export ? 0 : 1) + stages.reduce((n, s) => n + changedCount(s), 0)

  const perLang = stages.some((s) => s.enabled && s.driver?.per_lang)
  const translate = stageOf('translate')
  const dub = stageOf('dub')

  const path = form.path.trim()
  const previewContext: PreviewContext | undefined =
    form.from === 'server' && MEDIA.test(path) ? { path, lang: form.langs[0] ?? 'vi' } : undefined

  const create = useMutation({
    mutationFn: async () => {
      const overrides = Object.fromEntries(stages.map((s) => [s.stage, {
        enabled: s.enabled, ...(s.driver && { driver: s.driver.name, params: s.params }),
      }]))
      const body = { langs: perLang ? form.langs : [], template: template.name, overrides, export: form.export }
      if (form.from === 'upload') {
        const sources = await uploadAll()
        return (await createJobs({ body: { ...body, sources, use_subtitles: true } })).data
      }
      const sources = form.from === 'url' ? form.urls.split('\n') : [path]
      return (await createJobs({
        body: { ...body, sources, recursive: form.recursive, use_subtitles: form.useSubtitles },
      })).data
    },
    onSuccess: (data) => {
      if (form.from === 'upload') {
        setFiles([])
        setProgress(new Map())
      }
      if (data.jobs.length) toast.success(`Đã thêm ${data.jobs.length} việc vào hàng đợi`)
      data.skipped.forEach((message) => toast.warning(message))
      onCreated(data.jobs)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  /** Each video with its subtitles into a folder of its own (two videos at a time); returns the job sources. */
  async function uploadAll() {
    const sources: string[] = []
    const queue = groupUploads(files)
    const worker = async () => {
      for (let group = queue.shift(); group; group = queue.shift()) {
        let id: string | undefined
        // the video first: its folder is where the subtitles go
        for (const file of [group.source, ...group.files.filter((f) => f !== group.source)]) {
          const uploaded = await uploadFile(file, id, (done) => setProgress((p) => new Map(p).set(file, done)))
          id = uploaded.id
          if (file === group.source) sources.push(uploaded.path)
        }
      }
    }
    await Promise.all([worker(), worker()])
    return sources
  }

  function presetHint() {
    if (picked && edited) return `Đã sửa so với “${picked.name}”, chưa lưu`
    if (picked) return changed ? `${changed} tuỳ chọn khác mặc định` : undefined
    if (changed) return `${changed} tuỳ chọn khác mặc định · lưu thành preset trong Nâng cao`
    return presets.data?.length ? undefined : 'Lưu tuỳ chọn nâng cao thành preset để dùng lại'
  }

  const ready = form.from === 'upload' ? files.length > 0 : form.from === 'url' ? !!form.urls.trim() : !!path

  return (
    <Card>
      <CardHeader>
        <CardTitle>Việc mới</CardTitle>
        <CardDescription>Chọn video, việc cần làm và ngôn ngữ đích rồi bấm Bắt đầu.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-5" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
          <Tabs value={form.from} onValueChange={(from) => update({ from: from as Form['from'] })}>
            <TabsList className="w-full">
              <TabsTrigger value="upload"><Upload /> Tải lên</TabsTrigger>
              <TabsTrigger value="server"><HardDrive /> Trên máy</TabsTrigger>
              <TabsTrigger value="url"><Link /> URL</TabsTrigger>
            </TabsList>
            <TabsContent value="upload" className="pt-2">
              <UploadDrop files={files} onChange={setFiles} progress={progress} disabled={create.isPending} />
            </TabsContent>
            <TabsContent value="server" className="grid gap-4 pt-2">
              <div className="grid gap-2">
                <Label htmlFor="path">File hoặc thư mục</Label>
                <div className="flex gap-2">
                  <Input id="path" value={form.path} placeholder={`${config.root}/Videos`} className="font-mono text-xs"
                    onChange={(e) => update({ path: e.target.value })} />
                  <Button type="button" variant="outline" onClick={() => setBrowsing(true)}>
                    <FolderOpen /> Duyệt
                  </Button>
                </div>
              </div>
              <SwitchField label="Dùng phụ đề có sẵn" hint="<tên>.srt / <tên>.vi.srt cạnh video thay cho bước tạo ra chúng"
                checked={form.useSubtitles} onChange={(useSubtitles) => update({ useSubtitles })} />
              {path && !/\.[a-z0-9]{2,4}$/i.test(path) && (
                <SwitchField label="Quét thư mục con" checked={form.recursive} onChange={(recursive) => update({ recursive })} />
              )}
            </TabsContent>
            <TabsContent value="url" className="grid gap-2 pt-2">
              <Textarea rows={3} value={form.urls} placeholder="https://www.youtube.com/watch?v=… (mỗi dòng một video)"
                className="font-mono text-xs" onChange={(e) => update({ urls: e.target.value })} />
              {driversOf(drivers, 'source').find((d) => d.name === 'url')?.available && (
                <p className="text-xs text-destructive">{driversOf(drivers, 'source').find((d) => d.name === 'url')?.available}</p>
              )}
            </TabsContent>
          </Tabs>

          <Field label="Việc cần làm" hint={template?.description}>
            <div className="grid grid-cols-2 gap-1 rounded-lg border bg-muted/40 p-1">
              {templates.map((t) => (
                <button key={t.name} type="button" onClick={() => update({ template: t.name })}
                  className={cn('rounded-md px-2 py-1.5 text-xs font-medium transition-colors sm:text-sm',
                    t.name === template?.name ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
                  {t.title || t.name}
                </button>
              ))}
            </div>
          </Field>

          {perLang && (
            <Field label="Dịch sang">
              <div className="flex flex-wrap gap-1.5">
                {config.languages.map((lang) => {
                  const on = form.langs.includes(lang.code)
                  return (
                    <button key={lang.code} type="button"
                      onClick={() => update({ langs: on ? form.langs.filter((l) => l !== lang.code) : [...form.langs, lang.code] })}
                      className={cn('rounded-full border px-3 py-1 text-xs transition-colors',
                        on ? 'border-primary bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>
                      {lang.name}
                    </button>
                  )
                })}
              </div>
            </Field>
          )}

          {translate && (
            <div className="grid gap-4">
              <Field label="Dịch bằng">
                <DriverSelect drivers={translate.drivers} value={translate.driver!.name}
                  onChange={(driver) => setStage('translate', { driver })} />
              </Field>
              <DriverParams driver={translate.driver!} value={translate.params} only={['model']}
                onChange={(params) => setParams(translate, params)} />
              {translate.driver?.langs && form.langs.some((l) => !translate.driver!.langs!.includes(l)) && (
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  {translate.driver.title} chỉ dịch sang {translate.driver.langs.join(', ')}: ngôn ngữ khác sẽ bị bỏ qua.
                </p>
              )}
            </div>
          )}

          {dub && (
            <DriverParams driver={dub.driver!} value={dub.params} only={['voice']} context={previewContext}
              onChange={(params) => setParams(dub, params)} />
          )}

          <Field label="Cấu hình" hint={presetHint()}>
            <div className="flex gap-2">
              <Select value={picked ? picked.name : NO_PRESET} onValueChange={(v) => pickPreset(v === NO_PRESET ? '' : v)}>
                <SelectTrigger className="w-0 min-w-0 flex-1 overflow-hidden *:data-[slot=select-value]:block *:data-[slot=select-value]:truncate">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_PRESET}>Mặc định</SelectItem>
                  {presets.data?.map((p) => <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <Button type="button" variant="outline" onClick={() => setAdvanced(true)} title="Tuỳ chọn nâng cao">
                <SlidersHorizontal /> Nâng cao <Count n={changed} />
              </Button>
            </div>
          </Field>

          <Button type="submit" size="lg" disabled={!ready || create.isPending || (perLang && !form.langs.length)}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Rocket />}
            {create.isPending && form.from === 'upload' ? 'Đang tải lên…' : 'Bắt đầu'}
          </Button>
        </form>
      </CardContent>
      <AdvancedOptions open={advanced} onOpenChange={setAdvanced} stages={stages} exported={form.export}
        onExport={(exported) => update({ export: exported })} setStage={setStage} setParams={setParams}
        resetStage={resetStage} context={previewContext}
        preset={picked?.name ?? ''} presets={presets.data ?? []} current={options} onPreset={namePreset} />
      <FileBrowser open={browsing} onOpenChange={setBrowsing} initial={form.path || config.root}
        onPick={({ path: picked }: { path: string; kind: Entry['kind'] }) => update({ path: picked })} />
    </Card>
  )
}

/** Options without empty parts, to compare them with a preset whatever way they were set. */
function clean(options: Omit<Preset, 'name'>) {
  const stages = Object.entries(options.stages ?? {}).flatMap(([stage, set]) => {
    const params = Object.entries(set.params ?? {}).filter(([, p]) => p && Object.keys(p).length)
    const kept = {
      ...(set.driver != null && { driver: set.driver }),
      ...(set.enabled != null && { enabled: set.enabled }),
      ...(params.length && { params: Object.fromEntries(params.sort(([a], [b]) => a.localeCompare(b))) }),
    }
    return Object.keys(kept).length ? [[stage, kept] as const] : []
  }).sort(([a], [b]) => a.localeCompare(b))
  return { export: options.export ?? true, stages: Object.fromEntries(stages) }
}
