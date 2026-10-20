import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Inbox, Languages, Layers, ListOrdered, Loader2, Mic, RefreshCw, Settings } from 'lucide-react'
import { toast } from 'sonner'
import { createProject, type Project } from '@/api'
import { configText, projectConfig, projectFields, type RunConfig } from '@/components/JobConfig'
import { JobDetail } from '@/components/JobDetail'
import { BulkBar } from '@/components/BulkBar'
import { JobList } from '@/components/JobList'
import { JobToolbar } from '@/components/JobToolbar'
import { NewJob } from '@/components/NewJob'
import { Player } from '@/components/Player'
import { ALL, COLORS, Dot, NONE, ProjectSettings, ProjectSidebar, QUEUE, VOICES } from '@/components/Projects'
import { useVoiceLibrary, VoiceLibrary } from '@/components/Voices'
import { QueueView } from '@/components/QueueView'
import { Button } from '@/components/ui/button'
import { errorMessage, loadSaved, save, STATUS_LABELS, useConfig, useDrivers, useProjects, useTemplates } from '@/lib/api'
import {
  addCreated, listKey, SORTS, STATUS_GROUPS, useJob, useJobCounts, useJobList, useLiveJobs, type JobFilter, type JobSort,
  type StatusGroup,
} from '@/lib/jobs'
import { commonLang } from '@/lib/playback'

type UrlState = { project: string; job?: string; status?: StatusGroup; q?: string; play?: string }

/** ?project=…&status=…&q=…&job=…&play=… : the project shown (all, none or its id), the list's status group and search,
 * the job open and the job watched in the player, kept in history. */
function useUrlState() {
  const read = (): UrlState => {
    const query = new URLSearchParams(location.search)
    const status = STATUS_GROUPS.find((g) => g.id === query.get('status'))?.id
    return {
      project: query.get('project') || ALL, job: query.get('job') || undefined, status, q: query.get('q') || undefined,
      play: query.get('play') || undefined,
    }
  }
  const [state, setState] = useState(read)
  useEffect(() => {
    const onPop = () => setState(read())
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [])
  /** replace: no new history entry (each search typed) */
  const go = (next: UrlState, replace = false) => {
    const query = new URLSearchParams()
    if (next.project !== ALL) query.set('project', next.project)
    if (next.status) query.set('status', next.status)
    if (next.q) query.set('q', next.q)
    if (next.job) query.set('job', next.job)
    if (next.play) query.set('play', next.play)
    // opened: this entry opened the player (another job played in it keeps that), which closes by going back
    const opened = !!next.play && (replace ? !!history.state?.opened : !state.play)
    history[replace ? 'replaceState' : 'pushState']({ opened }, '', query.size ? `?${query}` : location.pathname)
    setState(next)
  }
  return [state, go] as const
}

/** The list's order, the same for every project. */
function useSort() {
  const [sort, setSort] = useState<JobSort>(() => {
    const saved = loadSaved<{ sort: JobSort }>('job-list', { sort: 'created_desc' }).sort
    return SORTS.some((s) => s.id === saved) ? saved : 'created_desc'
  })
  const change = (next: JobSort) => {
    setSort(next)
    save('job-list', { sort: next })
  }
  return [sort, change] as const
}

export default function App() {
  const queryClient = useQueryClient()
  const config = useConfig()
  const drivers = useDrivers()
  const templates = useTemplates()
  const projects = useProjects()
  const voices = useVoiceLibrary()
  const [url, go] = useUrlState()
  const { project: selected, job, status, q = '', play } = url
  const [sort, setSort] = useSort()
  const [settings, setSettings] = useState(false)

  const isList = ![QUEUE, VOICES].includes(selected)
  // a project not found (deleted) shows every job
  const listProject = selected === NONE ? NONE : projects.data?.some((p) => p.id === selected) ? selected : undefined
  const filter: JobFilter = { project: listProject, status, q: q || undefined, sort }
  const list = useJobList(filter, isList && !!projects.data)
  const counts = useJobCounts({ project: listProject, q, status })
  const detail = useJob(job)
  const playJob = useJob(play)
  const filterKey = JSON.stringify(listKey(filter))
  const [changed, setChanged] = useState(false) // the list loaded is no longer what the server would give
  const [changedIn, setChangedIn] = useState(filterKey)
  if (changedIn !== filterKey) {
    setChangedIn(filterKey)
    setChanged(false)
  }

  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [everything, setEverything] = useState(false) // checked: every job matching the filter, loaded or not
  const pick = (ids: Set<string>, all = false) => {
    setChecked(ids)
    setEverything(all)
  }
  const anchor = useRef<string>(undefined) // the job last ticked: Shift ticks the jobs between it and another
  const selection = JSON.stringify([selected, status, q])
  const [checkedIn, setCheckedIn] = useState(selection) // another project, status or search: nothing ticked
  if (checkedIn !== selection) {
    setCheckedIn(selection)
    pick(new Set())
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !job && !play && !document.querySelector('[role=dialog]')) pick(new Set())
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [job, play])
  const select = (id?: string) => go({ ...url, job: id, play: undefined })
  // replace: another job of the playlist, Back still closes the player
  const watch = (id?: string, replace = false) => go({ ...url, job: undefined, play: id }, replace)
  useLiveJobs({
    filter,
    // the player's playlist is the latest list: no banner to refresh it under the player
    onListChanged: () => (play ? void queryClient.invalidateQueries({ queryKey: listKey(filter), exact: true }) : setChanged(true)),
    onJobFinished: (job) => {
      const message = `${job.title}: ${STATUS_LABELS[job.status as keyof typeof STATUS_LABELS] ?? job.status}`
      const open = { action: { label: 'Xem', onClick: () => select(job.id) } }
      if (job.status === 'done') toast.success(message)
      else if (job.status === 'failed') toast.error(message, open)
      else if (job.status === 'waiting_review') toast.info(`${job.title}: chờ bạn duyệt trước khi lồng tiếng`, open)
    },
  })

  const running = counts.data?.active ?? 0
  useEffect(() => {
    document.title = running ? `(${running}) Dịch video` : 'Dịch video'
  }, [running])

  const error = config.error ?? drivers.error ?? templates.error ?? projects.error
  if (error) return <p className="p-8 text-destructive">Không kết nối được server: {errorMessage(error)}</p>
  if (!config.data || !drivers.data || !templates.data || !projects.data) {
    return <Loader2 className="m-8 animate-spin text-muted-foreground" />
  }
  const env = { config: config.data, templates: templates.data, drivers: drivers.data }

  const project = projects.data.find((p) => p.id === selected)
  const view = project ? project.id : [NONE, QUEUE, VOICES].includes(selected) ? selected : ALL
  const shown = list.data?.pages.flatMap((p) => p.jobs) ?? []
  const lang = commonLang(shown) // what the player shows first
  const numbers = counts.data
  // NONE is the key of the jobs of no project
  const sidebar: Record<string, number> = {
    ...numbers?.projects, [ALL]: numbers?.all ?? 0, [QUEUE]: numbers?.queue ?? 0, [VOICES]: voices.data?.length ?? 0,
  }
  const total = (status ? numbers?.status[status] : numbers?.total) ?? shown.length
  const refresh = () => {
    setChanged(false)
    void queryClient.resetQueries({ queryKey: listKey(filter), exact: true })
  }

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

  const check = (id: string, range: boolean) => {
    const next = new Set(checked)
    const on = !checked.has(id)
    const from = shown.findIndex((j) => j.id === anchor.current)
    const to = shown.findIndex((j) => j.id === id)
    const ids = range && from >= 0 ? shown.slice(Math.min(from, to), Math.max(from, to) + 1).map((j) => j.id) : [id]
    for (const x of ids) if (on) next.add(x); else next.delete(x)
    anchor.current = id
    pick(next)
  }

  const title = project ? project.name : view === NONE ? 'Chưa phân loại' : view === QUEUE ? 'Hàng đợi' : view === VOICES ? 'Giọng đọc' : 'Tất cả'

  return (
    <div className="min-h-svh bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
          <Languages className="size-5" />
          <h1 className="font-semibold">Dịch video</h1>
          <span className="hidden text-sm text-muted-foreground sm:inline">Phụ đề → Dịch → Lồng tiếng → Ghép video</span>
          {running > 0 && (
            <button type="button" onClick={() => go({ project: QUEUE })} title="Xem hàng đợi"
              className="ml-auto flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground">
              <Loader2 className="size-4 animate-spin" /> {running} việc đang xử lý
            </button>
          )}
        </div>
      </header>
      <div className="mx-auto grid max-w-6xl items-start gap-4 px-4 py-6 lg:grid-cols-[12.5rem_minmax(0,1fr)] lg:gap-6">
        <div className="min-w-0 lg:sticky lg:top-6">
          <ProjectSidebar projects={projects.data} counts={sidebar} selected={view} onSelect={(id) => go({ project: id })}
            onCreate={create} />
        </div>
        <main className="grid min-w-0 gap-5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-lg font-semibold">
                {project ? <Dot color={project.color ?? COLORS[0]} /> : view === NONE ? <Inbox className="size-4" />
                  : view === QUEUE ? <ListOrdered className="size-4" /> : view === VOICES ? <Mic className="size-4" /> : <Layers className="size-4" />}
                <span className="truncate">{title}</span>
              </h2>
              {project && <p className="text-sm text-muted-foreground">{configText(projectConfig(project), env)}</p>}
            </div>
            {project && (
              <Button variant="outline" size="sm" className="ml-auto" onClick={() => setSettings(true)}>
                <Settings /> Cài đặt
              </Button>
            )}
          </div>
          {view === QUEUE ? <QueueView onOpen={select} /> : view === VOICES ? <VoiceLibrary projects={projects.data} /> : <div className="grid gap-6">
            <div>
              <NewJob {...env} project={project} onCreated={(ids) => {
                void addCreated(queryClient, filter, ids)
                if (ids.length === 1) select(ids[0])
              }} />
              {view === ALL && projects.data.length > 0 && (
                <p className="mt-2 px-1 text-xs text-muted-foreground">Video thêm ở đây vào Chưa phân loại. Chọn một project bên trái để thêm thẳng vào đó.</p>
              )}
            </div>
            <section className="grid min-w-0 gap-3">
              <JobToolbar q={q} onQuery={(text) => go({ project: selected, status, q: text || undefined }, true)}
                status={status} onStatus={(group) => go({ project: selected, status: group, q: q || undefined })}
                sort={sort} onSort={setSort} counts={numbers} />
              <BulkBar jobs={shown} total={total} counts={numbers} filter={filter} checked={checked} everything={everything}
                onChecked={pick} projects={projects.data} drivers={drivers.data} view={view}
                onOpenProject={(id) => go({ project: id })} onOpenJob={select} />
              {changed && (
                <Button size="sm" variant="secondary" className="sticky top-2 z-10 justify-self-center rounded-full shadow-md"
                  onClick={refresh} title="Thứ tự hoặc bộ lọc không còn đúng với các thay đổi mới nhất">
                  <RefreshCw /> Danh sách đã thay đổi · Làm mới
                </Button>
              )}
              {list.isLoading ? <Loader2 className="animate-spin text-muted-foreground" />
                : list.isError ? <p className="text-sm text-destructive">{errorMessage(list.error)}</p> : (
                  <JobList jobs={shown} selected={job} onSelect={select} onPlay={watch} lang={lang} checked={checked} onCheck={check}
                    filtered={!!status || !!q} onClearFilter={() => go({ project: selected })}
                    more={!!list.hasNextPage} loadingMore={list.isFetchingNextPage} onMore={() => void list.fetchNextPage()} />
                )}
            </section>
          </div>}
        </main>
      </div>
      {project && (
        <ProjectSettings key={project.id} {...env} project={project} open={settings} onOpenChange={setSettings}
          onDeleted={() => { setSettings(false); go({ project: NONE }) }} />
      )}
      {play && (
        <Player job={playJob.data} error={playJob.error} lang={lang} onPlay={(id) => watch(id, true)}
          playlist={isList ? {
            jobs: shown, sort, more: !!list.hasNextPage, loadingMore: list.isFetchingNextPage,
            onMore: () => void list.fetchNextPage(),
          } : undefined}
          onClose={() => (history.state?.opened ? history.back() : watch(undefined))} />
      )}
      <JobDetail job={job ? detail.data : undefined} drivers={drivers.data} config={config.data}
        projects={projects.data} onClose={() => select(undefined)} />
    </div>
  )
}
