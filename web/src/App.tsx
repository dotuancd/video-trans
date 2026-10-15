import { useEffect, useState } from 'react'
import { Languages, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { JobDetail } from '@/components/JobDetail'
import { JobList } from '@/components/JobList'
import { NewJob } from '@/components/NewJob'
import { errorMessage, fileName, STATUS_LABELS, useConfig, useJobs, useLiveJobs, type Options } from '@/lib/api'

function useSelectedJob() {
  const read = () => Number(new URLSearchParams(location.search).get('job')) || undefined
  const [selected, setSelected] = useState(read)
  useEffect(() => {
    const onPop = () => setSelected(read())
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [])
  const select = (id?: number) => {
    history.pushState(null, '', id ? `?job=${id}` : location.pathname)
    setSelected(id)
  }
  return [selected, select] as const
}

export default function App() {
  const config = useConfig()
  const jobs = useJobs()
  const [selected, select] = useSelectedJob()
  useLiveJobs((job) => {
    const message = `${fileName(job.source)}: ${STATUS_LABELS[job.status]}`
    if (job.status === 'done') toast.success(message)
    else if (job.status === 'failed') toast.error(message, { action: { label: 'Xem', onClick: () => select(job.id) } })
  })

  const running = jobs.data?.filter((j) => j.status === 'running' || j.status === 'queued').length ?? 0
  useEffect(() => {
    document.title = running ? `(${running}) Dịch video` : 'Dịch video'
  }, [running])

  if (config.isError) {
    return <p className="p-8 text-destructive">Không kết nối được server: {errorMessage(config.error)}</p>
  }
  if (!config.data) return <Loader2 className="m-8 animate-spin text-muted-foreground" />

  return (
    <div className="min-h-svh bg-background">
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
          <Languages className="size-5" />
          <h1 className="font-semibold">Dịch video</h1>
          <span className="text-sm text-muted-foreground">Phụ đề → Dịch → Lồng tiếng → Ghép video</span>
          {running > 0 && (
            <span className="ml-auto flex items-center gap-1.5 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> {running} việc đang xử lý
            </span>
          )}
        </div>
      </header>
      <main className="mx-auto grid max-w-6xl items-start gap-6 px-4 py-6 lg:grid-cols-[24rem_1fr]">
        <div className="lg:sticky lg:top-6">
          <NewJob root={config.data.root} defaults={config.data.defaults as Options}
            onCreated={(ids) => ids.length === 1 && select(ids[0])} />
        </div>
        <section className="grid gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Việc ({jobs.data?.length ?? 0})</h2>
          {jobs.isLoading ? <Loader2 className="animate-spin text-muted-foreground" /> : (
            <JobList jobs={jobs.data ?? []} selected={selected} onSelect={select} />
          )}
        </section>
      </main>
      <JobDetail job={jobs.data?.find((j) => j.id === selected)} onClose={() => select(undefined)} />
    </div>
  )
}
