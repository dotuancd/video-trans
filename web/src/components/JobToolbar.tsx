import { useEffect, useRef, useState } from 'react'
import { ArrowUpDown, Search, X } from 'lucide-react'
import type { JobCounts } from '@/api'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { SORTS, STATUS_GROUPS, type JobSort, type StatusGroup } from '@/lib/jobs'

/** Above the job list: search (as you stop typing; / to get there), the status groups with their numbers,
 * and the order. */
export function JobToolbar({ q, onQuery, status, onStatus, sort, onSort, counts }: {
  q: string
  onQuery: (q: string) => void
  status?: StatusGroup
  onStatus: (status?: StatusGroup) => void
  sort: JobSort
  onSort: (sort: JobSort) => void
  counts?: JobCounts
}) {
  const [text, setText] = useState(q)
  const [sent, setSent] = useState(q)
  if (q !== sent) { // changed from outside (cleared, another project, Back)
    setSent(q)
    setText(q)
  }
  useEffect(() => {
    if (text === sent) return
    const timer = setTimeout(() => {
      setSent(text)
      onQuery(text)
    }, 300)
    return () => clearTimeout(timer)
  }, [text]) // eslint-disable-line react-hooks/exhaustive-deps

  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const typing = target?.closest('input, textarea, select, [contenteditable=true]')
      if (e.key !== '/' || typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector('[role=dialog]')) return
      e.preventDefault()
      input.current?.focus()
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [])

  const chip = (id: StatusGroup | undefined, label: string, count: number | undefined, hint?: string) => {
    const on = status === id
    return (
      <button key={id ?? 'all'} type="button" aria-pressed={on} title={hint} onClick={() => onStatus(id)}
        className={cn('inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent',
          on ? 'border-foreground/30 bg-card shadow-xs' : 'text-muted-foreground',
          !on && count === 0 && 'opacity-50')}>
        {label}
        <span className="tabular-nums text-muted-foreground">{count ?? '·'}</span>
      </button>
    )
  }
  const choices = SORTS.filter((s) => s.id !== 'cost_desc' || counts?.has_cost || sort === 'cost_desc')

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative w-full sm:w-56">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input ref={input} value={text} placeholder="Tìm việc…  ( / )" aria-label="Tìm việc theo tên hoặc file nguồn"
          className="h-8 pr-8 pl-8" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape' && text) {
              e.stopPropagation()
              setText('')
            }
          }} />
        {text && (
          <button type="button" aria-label="Xoá từ khoá" onClick={() => { setText(''); input.current?.focus() }}
            className="absolute top-1/2 right-2 -translate-y-1/2 rounded-sm text-muted-foreground hover:text-foreground">
            <X className="size-4" />
          </button>
        )}
      </div>
      <div className="flex gap-1.5 overflow-x-auto">
        {chip(undefined, 'Tất cả', counts?.total)}
        {STATUS_GROUPS.map((g) => chip(g.id, g.label, counts?.status[g.id], g.hint))}
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground">
            <ArrowUpDown /> {SORTS.find((s) => s.id === sort)?.label}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuRadioGroup value={sort} onValueChange={(value) => onSort(value as JobSort)}>
            {choices.map((s) => <DropdownMenuRadioItem key={s.id} value={s.id}>{s.label}</DropdownMenuRadioItem>)}
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
