import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { Loader2, Maximize, X } from 'lucide-react'
import type { Job } from '@/api'
import { Button } from '@/components/ui/button'
import { artifactUrl, errorMessage, useTranscript } from '@/lib/api'
import { mkvHint, playback, VIDEO, type Source } from '@/lib/playback'

/** The page covered by one job's best version (see lib/playback) with its subtitles on one line: watching only.
 * Esc or the close button closes it, as does Back (the URL holds ?play=). */
export function Player({ job, error, lang, onClose }: {
  /** undefined while loading */
  job?: Job
  error?: unknown
  /** the language wanted: the list's most common one */
  lang?: string
  onClose: () => void
}) {
  const root = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Esc in fullscreen leaves fullscreen only
      if (e.key === 'Escape' && !document.fullscreenElement) close.current()
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [])

  // the language is kept from the moment the job is known: the list loading later must not restart the video
  const [kept, keep] = useState<{ lang?: string }>()
  if (job && !kept) keep({ lang })
  const best = job && playback(job, kept ? kept.lang : lang)
  return (
    <div ref={root} role="dialog" aria-modal aria-label={job?.title ?? 'Xem video'}
      className="fixed inset-0 z-50 flex flex-col bg-black text-white">
      <div className="flex items-center gap-2 px-4 py-2">
        <h2 className="min-w-0 flex-1 truncate font-medium">{job?.title}</h2>
        <Button size="icon" variant="ghost" className="text-white hover:bg-white/15 hover:text-white" title="Toàn màn hình"
          onClick={() => void root.current?.requestFullscreen()}>
          <Maximize />
        </Button>
        <Button size="icon" variant="ghost" className="text-white hover:bg-white/15 hover:text-white" title="Đóng (Esc)"
          onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center">
        {error ? <p className="text-sm text-red-300">{errorMessage(error)}</p>
          : !job ? <Loader2 className="animate-spin text-white/60" />
            : !best ? <p className="text-sm text-white/70">Việc này chưa có video hoặc audio để xem.</p>
              : <Media key={best.source.id} jobId={job.id} source={best.source} subtitles={best.subtitles} />}
      </div>
    </div>
  )
}

function Media({ jobId, source, subtitles }: { jobId: string; source: Source; subtitles?: string }) {
  const transcript = useTranscript(jobId, subtitles)
  const [time, setTime] = useState(0)
  const cue = transcript.data?.cues.find((c) => c.start <= time && time < c.end)
  const props = {
    src: artifactUrl(source.artifact), controls: true, autoPlay: true,
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => setTime(e.currentTarget.currentTime),
  }
  return (
    <>
      {VIDEO.test(source.path)
        // fullscreen is the whole player's (the subtitles are drawn over the video, not by it)
        ? <video {...props} controlsList="nofullscreen" className="size-full object-contain" />
        : <audio {...props} className="w-full max-w-2xl" />}
      {cue && (
        <div className="pointer-events-none absolute inset-x-0 bottom-16 flex justify-center px-6">
          <span className="max-w-4xl rounded bg-black/75 px-3 py-1 text-center text-xl sm:text-2xl">{cue.text.replace(/\s*\n\s*/g, ' ')}</span>
        </div>
      )}
      {mkvHint(source.path) && <p className="absolute top-2 text-xs text-white/60">{mkvHint(source.path)}</p>}
    </>
  )
}
