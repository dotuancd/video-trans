import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Save } from 'lucide-react'
import { toast } from 'sonner'
import { getSubtitles, saveSubtitles, type SubtitlesSaved } from '@/api'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { errorMessage } from '@/lib/api'

const clock = (seconds: number) => {
  const m = Math.floor(seconds / 60)
  const s = (seconds % 60).toFixed(1).padStart(4, '0')
  return `${m}:${s}`
}

const edited = (draft: string | undefined, saved: string) => draft !== undefined && draft.trim() !== saved

/** Loaded cues plus the unsaved edits (of both files when `path` is a .vi.srt with its original next to it),
 * shared by the editor and the player that previews them. */
export function useSubtitleDraft(path: string | undefined, onSaved?: (changed: SubtitlesSaved) => void) {
  const queryClient = useQueryClient()
  const subtitles = useQuery({
    queryKey: ['subtitles', path],
    queryFn: async () => (await getSubtitles({ query: { path: path! } })).data,
    enabled: !!path,
  })
  const cues = subtitles.data?.cues
  const hasSource = !!subtitles.data?.source_path
  const [texts, setTexts] = useState<string[]>([])
  const [sources, setSources] = useState<string[]>([])
  useEffect(() => {
    setTexts(cues?.map((c) => c.text) ?? [])
    setSources(cues?.map((c) => c.source) ?? [])
  }, [cues])
  const dirty = useMemo(
    () => cues?.filter((c, i) => edited(texts[i], c.text) || (hasSource && edited(sources[i], c.source))).length ?? 0,
    [cues, texts, sources, hasSource],
  )
  const save = useMutation({
    mutationFn: async () =>
      (await saveSubtitles({ body: { path: path!, texts, sources: hasSource ? sources : undefined } })).data,
    onSuccess: (changed) => {
      queryClient.invalidateQueries({ queryKey: ['subtitles', path] })
      onSaved?.(changed)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  return { subtitles, cues, hasSource, texts, setTexts, sources, setSources, dirty, save }
}

export type SubtitleDraft = ReturnType<typeof useSubtitleDraft>

const set = (i: number, value: string) => (list: string[]) => list.map((x, j) => (j === i ? value : x))

/** Side by side original / Vietnamese, both editable (or the original alone before translation); timing never changes. */
export function SubtitleEditor({ draft, active, onSeek }: {
  draft: SubtitleDraft
  /** index of the cue under the playhead, -1 if none */
  active: number
  onSeek?: (seconds: number) => void
}) {
  const { subtitles, cues, hasSource, texts, setTexts, sources, setSources, dirty, save } = draft
  const list = useRef<HTMLDivElement>(null)

  // follow the playhead, but never yank the list away from a cue being typed in
  useEffect(() => {
    const box = list.current
    const row = box?.children[active] as HTMLElement | undefined
    if (!box || !row || box.contains(document.activeElement)) return
    if (row.offsetTop < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight)
      box.scrollTo({ top: row.offsetTop - box.clientHeight / 3, behavior: 'smooth' })
  }, [active])

  if (subtitles.isLoading) return <Loader2 className="animate-spin text-muted-foreground" />
  if (subtitles.isError) return <p className="text-sm text-destructive">{errorMessage(subtitles.error)}</p>
  if (!cues) return null

  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {cues.length} câu{dirty ? ` · ${dirty} câu chưa lưu` : ''}
        </p>
        <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Lưu
        </Button>
      </div>
      <div ref={list} className="relative max-h-[40vh] overflow-y-auto rounded-lg border">
        {cues.map((cue, i) => (
          <div key={cue.index} className={cn('grid gap-2 border-b border-l-2 border-l-transparent p-2 last:border-b-0',
            hasSource ? 'sm:grid-cols-[4.5rem_1fr_1fr]' : 'sm:grid-cols-[4.5rem_1fr]',
            (edited(texts[i], cue.text) || (hasSource && edited(sources[i], cue.source))) && 'bg-amber-50/60 dark:bg-amber-950/30',
            i === active && 'border-l-sky-500 bg-sky-50/70 dark:bg-sky-950/40')}>
            <button type="button" className="text-left font-mono text-xs text-muted-foreground hover:text-foreground"
              title="Phát từ đây" onClick={() => onSeek?.(cue.start)}>
              {clock(cue.start)}
            </button>
            {hasSource && (
              <Textarea
                value={sources[i] ?? ''}
                rows={1}
                aria-label="Phụ đề gốc"
                className="min-h-0 w-full resize-none py-1 text-sm text-muted-foreground field-sizing-content focus-visible:text-foreground"
                onChange={(e) => setSources(set(i, e.target.value))}
              />
            )}
            <Textarea
              value={texts[i] ?? ''}
              rows={1}
              className="min-h-0 w-full resize-none py-1 text-sm field-sizing-content"
              onChange={(e) => setTexts(set(i, e.target.value))}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
