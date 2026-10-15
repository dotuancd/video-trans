import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Inbox, Languages, Layers, Loader2, Settings } from 'lucide-react'
import { toast } from 'sonner'
import { createProject, type Project } from '@/api'
import { configText, projectConfig, projectFields, type RunConfig } from '@/components/JobConfig'
import { JobDetail } from '@/components/JobDetail'
import { JobList } from '@/components/JobList'
import { NewJob } from '@/components/NewJob'
import { ALL, COLORS, Dot, NONE, ProjectSettings, ProjectSidebar } from '@/components/Projects'
import { Button } from '@/components/ui/button'
import {
  errorMessage, isActive, loadSaved, STATUS_LABELS, useConfig, useDrivers, useJobs, useLiveJobs, useProjects, useTemplates,
} from '@/lib/api'

/** ?project=…&job=… : the project shown (all, none or its id) and the job open, kept in history. */
function useUrlState() {
  const read = (): { project: string; job?: string } => {
    const query = new URLSearchParams(location.search)
    return { project: query.get('project') || ALL, job: query.get('job') || undefined }
  }
  const [state, setState] = useState(read)
  useEffect(() => {
    const onPop = () => setState(read())
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [])
  const go = (next: { project: string; job?: string }) => {
    const query = new URLSearchParams()
    if (next.project !== ALL) query.set('project', next.project)
    if (next.job) query.set('job', next.job)
    history.pushState(null, '', query.size ? `?${query}` : location.pathname)
    setState(next)
  }
  return [state, go] as const
}

export default function App() {
  const queryClient = useQueryClient()
  const config = useConfig()
  const drivers = useDrivers()
  const templates = useTemplates()
  const jobs = useJobs()
  const projects = useProjects()
  const [{ project: selected, job }, go] = useUrlState()
  const [settings, setSettings] = useState(false)
  const select = (id?: string) => go({ project: selected, job: id })
  useLiveJobs((job) => {
    const message = `${job.title}: ${STATUS_LABELS[job.status as keyof typeof STATUS_LABELS] ?? job.status}`
    const open = { action: { label: 'Xem', onClick: () => select(job.id) } }
    if (job.status === 'done') toast.success(message)
    else if (job.status === 'failed') toast.error(message, open)
    else if (job.status === 'waiting_review') toast.info(`${job.title}: chờ bạn duyệt trước khi lồng tiếng`, open)
  })

  const running = jobs.data?.filter((j) => isActive(j.status)).length ?? 0
  useEffect(() => {
    document.title = running ? `(${running}) Dịch video` : 'Dịch video'
  }, [running])

  const error = config.error ?? drivers.error ?? templates.error ?? projects.error
  if (error) return <p className="p-8 text-destructive">Không kết nối được server: {errorMessage(error)}</p>
  if (!config.data || !drivers.data || !templates.data || !projects.data) {
    return <Loader2 className="m-8 animate-spin text-muted-foreground" />
  }
  const env = { config: config.data, templates: templates.data, drivers: drivers.data }

  const all = jobs.data ?? []
  const project = projects.data.find((p) => p.id === selected)
  const view = project ? project.id : selected === NONE ? NONE : ALL
  const shown = all.filter((j) => view === ALL || (view === NONE ? !j.project_id : j.project_id === view))
  const counts: Record<string, number> = { [ALL]: all.length }
  for (const j of all) counts[j.project_id ?? NONE] = (counts[j.project_id ?? NONE] ?? 0) + 1

  const create = async (name: string) => {
    try {
      // a new project starts with the options last used for a new job
      const last = loadSaved<RunConfig>('new-job-v2', { template: 'default', langs: config.data!.default_langs, preset: '', export: true, stages: {} })
      const fields = projectFields({ template: last.template, langs: last.langs, preset: last.preset, export: last.export, stages: last.stages })
      const color = COLORS[projects.data!.length % COLORS.length]
      const made = (await createProject({ body: { name, color, ...fields } })).data
      queryClient.setQueryData<Project[]>(['projects'], (list = []) => [...list.filter((p) => p.id !== made.id), made])
      go({ project: made.id })
      setSettings(true)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const done = shown.filter((j) => j.status === 'done').length
  const active = shown.filter((j) => isActive(j.status)).length
  const title = project ? project.name : view === NONE ? 'Chưa phân loại' : 'Tất cả'

  return (
    <div className="min-h-svh bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3">
          <Languages className="size-5" />
          <h1 className="font-semibold">Dịch video</h1>
          <span className="hidden text-sm text-muted-foreground sm:inline">Phụ đề → Dịch → Lồng tiếng → Ghép video</span>
          {running > 0 && (
            <span className="ml-auto flex items-center gap-1.5 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> {running} việc đang xử lý
            </span>
          )}
        </div>
      </header>
      <div className="mx-auto grid max-w-7xl items-start gap-4 px-4 py-6 lg:grid-cols-[12.5rem_minmax(0,1fr)] lg:gap-6">
        <div className="min-w-0 lg:sticky lg:top-6">
          <ProjectSidebar projects={projects.data} counts={counts} selected={view} onSelect={(id) => go({ project: id })}
            onCreate={create} />
        </div>
        <main className="grid min-w-0 gap-5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-lg font-semibold">
                {project ? <Dot color={project.color ?? COLORS[0]} /> : view === NONE ? <Inbox className="size-4" /> : <Layers className="size-4" />}
                <span className="truncate">{title}</span>
              </h2>
              <p className="text-sm text-muted-foreground tabular-nums">
                {shown.length} việc · {done} xong · {active} đang chạy
                {project && ` · ${configText(projectConfig(project), env)}`}
              </p>
            </div>
            {project && (
              <Button variant="outline" size="sm" className="ml-auto" onClick={() => setSettings(true)}>
                <Settings /> Cài đặt
              </Button>
            )}
          </div>
          <div className="grid items-start gap-5 xl:grid-cols-[24rem_minmax(0,1fr)]">
            <div className="xl:sticky xl:top-6">
              <NewJob {...env} project={project} onCreated={(ids) => ids.length === 1 && select(ids[0])} />
              {view === ALL && projects.data.length > 0 && (
                <p className="mt-2 px-1 text-xs text-muted-foreground">Video thêm ở đây vào Chưa phân loại. Chọn một project bên trái để thêm thẳng vào đó.</p>
              )}
            </div>
            <section className="grid min-w-0 gap-3">
              <h3 className="text-sm font-medium text-muted-foreground">Việc ({shown.length})</h3>
              {jobs.isLoading ? <Loader2 className="animate-spin text-muted-foreground" /> : (
                <JobList jobs={shown} selected={job} onSelect={select} />
              )}
            </section>
          </div>
        </main>
      </div>
      {project && (
        <ProjectSettings key={project.id} {...env} project={project} open={settings} onOpenChange={setSettings}
          onDeleted={() => { setSettings(false); go({ project: NONE }) }} />
      )}
      <JobDetail job={all.find((j) => j.id === job)} drivers={drivers.data} config={config.data}
        projects={projects.data} onClose={() => select(undefined)} />
    </div>
  )
}
