import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, HardDrive, Link, Loader2, Rocket, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { createJobs, saveProject, type Config, type DriverInfo, type Entry, type Project, type TemplateInfo } from '@/api'
import { FileBrowser } from '@/components/FileBrowser'
import { configText, differsFrom, JobConfig, projectBody, projectConfig, projectFields, resolve, type RunConfig } from '@/components/JobConfig'
import { SwitchField, type PreviewContext } from '@/components/Params'
import { isReady, UploadDrop, UploadTray, useUploadTray } from '@/components/UploadDrop'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { driversOf, errorMessage, loadSaved, save } from '@/lib/api'

const MEDIA = /\.(mp4|mkv|mov|m4v|webm|avi|wav|mp3|m4a|aac|flac|ogg)$/i

/** Where the videos come from, and (for jobs of no project) the options last used. */
type Form = RunConfig & {
  from: 'upload' | 'server' | 'url'
  path: string
  urls: string
  recursive: boolean
  useSubtitles: boolean
}

/** Adds videos: into `project` with its options (which can be changed for this run, or saved back into it),
 * else with the options last used here. Dropped files are uploaded right away and started with Bắt đầu. */
export function NewJob({ config, templates, drivers, project, onCreated }: {
  config: Config
  templates: TemplateInfo[]
  drivers: DriverInfo[]
  project?: Project
  onCreated: (jobIds: string[]) => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Form>(() => loadSaved<Form>('new-job-v2', {
    from: 'upload', path: '', urls: '', recursive: false, useSubtitles: true, export: true,
    template: 'default', langs: config.default_langs, preset: '', stages: {},
  }))
  useEffect(() => save('new-job-v2', form), [form])
  const update = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  const [browsing, setBrowsing] = useState(false)
  const tray = useUploadTray()

  // in a project: its options, or the ones changed for this run (null: the project's)
  const base = project ? projectConfig(project) : undefined
  const [edited, setEdited] = useState<{ project: string; value: RunConfig } | null>(null)
  const edit = project && edited?.project === project.id ? edited.value : null
  const setEdit = (value: RunConfig | null) => setEdited(value && project ? { project: project.id, value } : null)
  const run: RunConfig = base ? edit ?? base : form
  const setRun = (value: RunConfig) => (base ? setEdit(value) : update(value))
  const differs = !!base && !!edit && differsFrom(edit, base)
  const env = { config, templates, drivers }
  const { body, valid } = resolve(run, env)

  const scope = project?.id ?? 'none'
  const rows = tray.rows.filter((r) => r.scope === scope)
  const picked = rows.filter((r) => isReady(r) && r.checked)
  const uploading = rows.filter((r) => !isReady(r) && !r.error).length

  const path = form.path.trim()
  const context: PreviewContext | undefined =
    form.from === 'server' && MEDIA.test(path) ? { path, lang: run.langs[0] ?? 'vi' } : undefined

  const create = useMutation({
    mutationFn: async (keep: boolean) => {
      if (keep && project) {
        await saveProject({ path: { project_id: project.id }, body: { ...projectBody(project), ...projectFields(run) } })
        void queryClient.invalidateQueries({ queryKey: ['projects'] })
      }
      const common = { ...body, project: project?.id ?? null }
      if (form.from === 'upload') {
        return (await createJobs({ body: { ...common, sources: picked.map((r) => r.path!), use_subtitles: true } })).data
      }
      const sources = form.from === 'url' ? form.urls.split('\n') : [path]
      return (await createJobs({
        body: { ...common, sources, recursive: form.recursive, use_subtitles: form.useSubtitles },
      })).data
    },
    onSuccess: (data, keep) => {
      if (form.from === 'upload') tray.remove(picked.map((r) => r.key))
      if (keep) setEdit(null)
      if (data.jobs.length) {
        toast.success(`Đã thêm ${data.jobs.length} việc vào hàng đợi${keep && project ? `, đã lưu cấu hình vào ${project.name}` : ''}`)
      }
      data.skipped.forEach((message) => toast.warning(message))
      onCreated(data.jobs)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const ready = form.from === 'upload' ? picked.length > 0 : form.from === 'url' ? !!form.urls.trim() : !!path
  const disabled = !ready || !valid || create.isPending
  const count = form.from === 'upload' && picked.length ? ` · ${picked.length} việc` : ''
  const icon = create.isPending ? <Loader2 className="animate-spin" /> : <Rocket />

  return (
    <Card>
      <CardHeader>
        <CardTitle>{project ? `Thêm video vào ${project.name}` : 'Việc mới'}</CardTitle>
        <CardDescription>
          {project ? 'Thả vào là tải lên ngay. Tải xong được chọn sẵn, bấm Bắt đầu.' : 'Chọn video, việc cần làm và ngôn ngữ đích rồi bấm Bắt đầu.'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-5" onSubmit={(e) => { e.preventDefault(); create.mutate(false) }}>
          <Tabs value={form.from} onValueChange={(from) => update({ from: from as Form['from'] })}>
            <TabsList className="w-full">
              <TabsTrigger value="upload"><Upload /> Tải lên</TabsTrigger>
              <TabsTrigger value="server"><HardDrive /> Trên máy</TabsTrigger>
              <TabsTrigger value="url"><Link /> URL</TabsTrigger>
            </TabsList>
            <TabsContent value="upload" className="grid gap-2 pt-2">
              <UploadDrop compact={rows.length > 0} onFiles={(files) => tray.add(scope, files)} />
              {rows.length > 0 && (
                <UploadTray rows={rows} onCheck={tray.check} onRemove={(key) => tray.remove([key])} />
              )}
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

          {base && (
            <div className="flex items-center gap-3 rounded-lg border bg-muted/40 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{edit ? 'Cấu hình cho lần chạy này' : 'Theo cấu hình project'}</div>
                <div className="truncate text-xs text-muted-foreground">{configText(run, env)}</div>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => setEdit(edit ? null : base)}>
                {edit ? 'Bỏ thay đổi' : 'Sửa'}
              </Button>
            </div>
          )}
          {(!base || edit) && <JobConfig {...env} value={run} onChange={setRun} base={base} context={context} />}

          <div className="grid gap-2">
            {form.from === 'upload' && rows.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {uploading ? `Đang tải lên ${uploading} video · ` : ''}
                {picked.length ? `${picked.length} video đã chọn` : 'Chọn video đã tải lên để bắt đầu'}
              </p>
            )}
            {differs ? (
              <div className="grid grid-cols-2 gap-2">
                <Button type="submit" variant="outline" disabled={disabled}>Chỉ chạy lần này</Button>
                <Button type="button" disabled={disabled} onClick={() => create.mutate(true)}
                  title={`Video thêm sau vào ${project!.name} cũng dùng cấu hình này`}>
                  {icon} Chạy & lưu cấu hình
                </Button>
              </div>
            ) : (
              <Button type="submit" size="lg" disabled={disabled}>{icon} Bắt đầu{count}</Button>
            )}
          </div>
        </form>
      </CardContent>
      <FileBrowser open={browsing} onOpenChange={setBrowsing} initial={form.path || config.root}
        onPick={({ path: picked }: { path: string; kind: Entry['kind'] }) => update({ path: picked })} />
    </Card>
  )
}
