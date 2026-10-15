import { useEffect, useState } from 'react'
import { Languages, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { JobDetail } from '@/components/JobDetail'
import { JobList } from '@/components/JobList'
import { NewJob } from '@/components/NewJob'
import { errorMessage, isActive, STATUS_LABELS, useConfig, useDrivers, useJobs, useLiveJobs, useTemplates } from '@/lib/api'

function useSelectedJob() {
  const read = () => new URLSearchParams(location.search).get('job') || undefined
  const [selected, setSelected] = useState(read)
  useEffect(() => {
    const onPop = () => setSelected(read())
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [])
  const select = (id?: string) => {
    history.pushState(null, '', id ? `?job=${id}` : location.pathname)
    setSelected(id)
  }
  return [selected, select] as const
}

export default function App() {
  const config = useConfig()
  const drivers = useDrivers()
  const templates = useTemplates()
  const jobs = useJobs()
  const [selected, select] = useSelectedJob()
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

  const error = config.error ?? drivers.error ?? templates.error
  if (error) return <p className="p-8 text-destructive">Không kết nối được server: {errorMessage(error)}</p>
  if (!config.data || !drivers.data || !templates.data) return <Loader2 className="m-8 animate-spin text-muted-foreground" />

  return (
    <div className="min-h-svh bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
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
      <main className="mx-auto grid max-w-6xl items-start gap-6 px-4 py-6 lg:grid-cols-[24rem_1fr]">
        <div className="lg:sticky lg:top-6">
          <NewJob config={config.data} templates={templates.data} drivers={drivers.data}
            onCreated={(ids) => ids.length === 1 && select(ids[0])} />
        </div>
        <section className="grid gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Việc ({jobs.data?.length ?? 0})</h2>
          {jobs.isLoading ? <Loader2 className="animate-spin text-muted-foreground" /> : (
            <JobList jobs={jobs.data ?? []} selected={selected} onSelect={select} />
          )}
        </section>
      </main>
      <JobDetail job={jobs.data?.find((j) => j.id === selected)} drivers={drivers.data} config={config.data}
        onClose={() => select(undefined)} />
    </div>
  )
}
