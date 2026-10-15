import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDownToLine, Loader2, Save, Undo2 } from 'lucide-react'
import { toast } from 'sonner'
import { getSubtitles, saveSubtitles, type Cue, type SubtitlesSaved } from '@/api'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { errorMessage } from '@/lib/api'
import { mergeNext, moveWords, newKey, split, type Field, type Row } from '@/lib/cues'

const clock = (seconds: number) => {
  const m = Math.floor(seconds / 60)
  const s = (seconds % 60).toFixed(1).padStart(4, '0')
  return `${m}:${s}`
}

const sameAs = (row: Row, cue: Cue, hasSource: boolean) =>
  row.start === cue.start && row.end === cue.end && row.text.trim() === cue.text
  && (!hasSource || row.source.trim() === cue.source)

/** Loaded cues plus the unsaved edits — texts, and cues merged, split or with words moved between them
 * (of both files when `path` is a .vi.srt with its original next to it), shared by the editor and the player. */
export function useSubtitleDraft(path: string | undefined, onSaved?: (changed: SubtitlesSaved) => void) {
  const queryClient = useQueryClient()
  const subtitles = useQuery({
    queryKey: ['subtitles', path],
    queryFn: async () => (await getSubtitles({ query: { path: path! } })).data,
    enabled: !!path,
  })
  const cues = subtitles.data?.cues
  const hasSource = !!subtitles.data?.source_path
  const [rows, setRows] = useState<Row[]>([])
  const [history, setHistory] = useState<Row[][]>([])
  useEffect(() => {
    setRows(cues?.map((c) => ({ key: newKey(), start: c.start, end: c.end, source: c.source, text: c.text })) ?? [])
    setHistory([])
  }, [cues])
  /** a merge, split or move: undoable as one step */
  const restructure = (next: Row[]) => {
    if (next === rows) return
    setHistory((h) => [...h.slice(-49), rows])
    setRows(next)
  }
  const undo = () => {
    const previous = history.at(-1)
    if (!previous) return
    setRows(previous)
    setHistory((h) => h.slice(0, -1))
  }
  const isEdited = (row: Row) => !cues?.some((c) => sameAs(row, c, hasSource))
  // a merge or split always leaves a cue unlike any loaded one, so this also counts structural edits
  const dirty = useMemo(() => rows.filter((r) => !cues?.some((c) => sameAs(r, c, hasSource))).length,
    [cues, rows, hasSource])
  const save = useMutation({
    mutationFn: async () => (await saveSubtitles({
      body: {
        path: path!,
        cues: rows.map((r) => ({ start: r.start, end: r.end, text: r.text, source: hasSource ? r.source : undefined })),
      },
    })).data,
    onSuccess: (changed) => {
      queryClient.invalidateQueries({ queryKey: ['subtitles', path] })
      onSaved?.(changed)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const words = subtitles.data?.words ?? undefined
  return { subtitles, cues, hasSource, words, rows, setRows, restructure, undo, canUndo: history.length > 0, isEdited, dirty, save }
}

export type SubtitleDraft = ReturnType<typeof useSubtitleDraft>

const HINT = 'Enter: tách câu tại con trỏ · Backspace ở đầu câu: gộp với câu trên · Alt+↑ / Alt+↓: chuyển chữ trước / sau con trỏ lên câu trên / xuống câu dưới · Shift+Enter: xuống dòng'

/** Side by side original / Vietnamese, both editable (or the original alone before translation).
 * Cues can be split, merged, and have words moved to their neighbours; times follow the spoken words when the
 * transcript's word timings are known, else the share of characters. */
export function SubtitleEditor({ draft, active, onSeek }: {
  draft: SubtitleDraft
  /** index of the cue under the playhead, -1 if none */
  active: number
  onSeek?: (seconds: number) => void
}) {
  const { subtitles, cues, hasSource, words, rows, setRows, restructure, undo, canUndo, isEdited, dirty, save } = draft
  const list = useRef<HTMLDivElement>(null)
  const inputs = useRef(new Map<string, HTMLTextAreaElement>())
  const focus = useRef<{ key: number; field: Field; at: number } | null>(null)

  // follow the playhead, but never yank the list away from a cue being typed in
  useEffect(() => {
    const box = list.current
    const row = box?.children[active] as HTMLElement | undefined
    if (!box || !row || box.contains(document.activeElement)) return
    if (row.offsetTop < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight)
      box.scrollTo({ top: row.offsetTop - box.clientHeight / 3, behavior: 'smooth' })
  }, [active])

  // put the caret where a merge or split left the words
  useLayoutEffect(() => {
    const target = focus.current
    if (!target) return
    focus.current = null
    const input = inputs.current.get(`${target.key}:${target.field}`)
    input?.focus()
    input?.setSelectionRange(target.at, target.at)
  }, [rows])

  if (subtitles.isLoading) return <Loader2 className="animate-spin text-muted-foreground" />
  if (subtitles.isError) return <p className="text-sm text-destructive">{errorMessage(subtitles.error)}</p>
  if (!cues) return null

  // the original holds the spoken words, so moving them moves the timing; the translation just follows
  const timingField: Field = hasSource ? 'source' : 'text'

  const setField = (i: number, field: Field, value: string) =>
    setRows((list) => list.map((r, j) => (j === i ? { ...r, [field]: value } : r)))

  const onKeyDown = (i: number, field: Field) => (e: KeyboardEvent<HTMLTextAreaElement>) => {
    const { selectionStart: from, selectionEnd: to, value } = e.currentTarget
    const row = rows[i]
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      const next = split(rows, i, field, from, timingField, words)
      if (next !== rows) focus.current = { key: next[i + 1].key, field, at: 0 }
      restructure(next)
    } else if (e.key === 'Backspace' && from === 0 && to === 0 && i > 0) {
      e.preventDefault()
      const above = rows[i - 1][field].trimEnd()
      focus.current = { key: rows[i - 1].key, field, at: above.length + (above && value.trim() ? 1 : 0) }
      restructure(mergeNext(rows, i - 1))
    } else if (e.key === 'Delete' && from === value.length && to === value.length && i < rows.length - 1) {
      e.preventDefault()
      focus.current = { key: row.key, field, at: value.trimEnd().length }
      restructure(mergeNext(rows, i))
    } else if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault()
      const up = e.key === 'ArrowUp'
      const next = moveWords(rows, i, field, up ? to : from, up, field === timingField, words)
      if (next !== rows) focus.current = { key: row.key, field, at: up ? 0 : next[i][field].length }
      restructure(next)
    }
  }

  const input = (i: number, field: Field, props: object) => (
    <Textarea
      ref={(el) => { const id = `${rows[i].key}:${field}`; if (el) inputs.current.set(id, el); else inputs.current.delete(id) }}
      value={rows[i][field]}
      rows={1}
      onChange={(e) => setField(i, field, e.target.value)}
      onKeyDown={onKeyDown(i, field)}
      {...props}
    />
  )

  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {rows.length} câu{dirty ? ` · ${dirty} câu chưa lưu` : ''}
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" disabled={!canUndo} onClick={undo} title="Hoàn tác gộp / tách / chuyển chữ">
            <Undo2 /> Hoàn tác
          </Button>
          <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Lưu
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{HINT}</p>
      <div ref={list} className="relative max-h-[40vh] overflow-y-auto rounded-lg border">
        {rows.map((row, i) => (
          <div key={row.key} className={cn('group grid gap-2 border-b border-l-2 border-l-transparent p-2 last:border-b-0',
            hasSource ? 'sm:grid-cols-[4.5rem_1fr_1fr]' : 'sm:grid-cols-[4.5rem_1fr]',
            isEdited(row) && 'bg-amber-50/60 dark:bg-amber-950/30',
            i === active && 'border-l-sky-500 bg-sky-50/70 dark:bg-sky-950/40')}>
            <div className="flex items-start justify-between gap-1">
              <button type="button" className="text-left font-mono text-xs text-muted-foreground hover:text-foreground"
                title="Phát từ đây" onClick={() => onSeek?.(row.start)}>
                {clock(row.start)}
              </button>
              {i < rows.length - 1 && (
                <button type="button" title="Gộp với câu dưới" aria-label="Gộp với câu dưới"
                  className="text-muted-foreground opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 hover:text-foreground focus-visible:opacity-100"
                  onClick={() => restructure(mergeNext(rows, i))}>
                  <ArrowDownToLine className="size-3.5" />
                </button>
              )}
            </div>
            {hasSource && input(i, 'source', {
              'aria-label': 'Phụ đề gốc',
              className: 'min-h-0 w-full resize-none py-1 text-sm text-muted-foreground field-sizing-content focus-visible:text-foreground',
            })}
            {input(i, 'text', { className: 'min-h-0 w-full resize-none py-1 text-sm field-sizing-content' })}
          </div>
        ))}
      </div>
    </div>
  )
}
