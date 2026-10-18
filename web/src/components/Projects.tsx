import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Inbox, Layers, ListOrdered, Mic, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { deleteProject, saveProject, type Config, type DriverInfo, type Project, type TemplateInfo } from '@/api'
import { FileBrowser } from '@/components/FileBrowser'
import { JobConfig, projectBody, projectConfig, projectFields } from '@/components/JobConfig'
import { Field } from '@/components/Params'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { errorMessage } from '@/lib/api'

export const COLORS = ['#14a394', '#e0456b', '#e09a1a', '#6366f1', '#3fa34d', '#0e8fd0']
/** What the sidebar shows: every job, the jobs of no project, or a project's */
export const ALL = 'all'
export const NONE = 'none'
/** Not a project: the workers' queue */
export const QUEUE = 'queue'
/** Not a project: the voice library */
export const VOICES = 'voices'

export const Dot = ({ color }: { color: string }) =>
  <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: color }} />

/** Projects down the left (a strip that scrolls sideways on phones); a new one is named in place. */
export function ProjectSidebar({ projects, counts, selected, onSelect, onCreate }: {
  projects: Project[]
  counts: Record<string, number>
  selected: string
  onSelect: (id: string) => void
  onCreate: (name: string) => void
}) {
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const done = (create: boolean) => {
    if (create && name.trim()) onCreate(name.trim())
    setName('')
    setNaming(false)
  }
  const item = (id: string, label: string, icon: ReactNode) => (
    <button key={id} type="button" onClick={() => onSelect(id)} aria-current={selected === id}
      className={cn('flex min-w-0 shrink-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent',
        selected === id && 'bg-card font-medium shadow-[inset_0_0_0_1px_var(--color-border)]')}>
      {icon}
      <span className="truncate">{label}</span>
      <span className="ml-auto pl-2 text-xs text-muted-foreground tabular-nums">{counts[id] ?? 0}</span>
    </button>
  )
  return (
    <nav aria-label="Projects" className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
      <div className="hidden px-2 pb-1 text-[11px] font-medium tracking-wider text-muted-foreground uppercase lg:block">Projects</div>
      {item(ALL, 'Tất cả', <Layers className="size-4 shrink-0 text-muted-foreground" />)}
      {item(NONE, 'Chưa phân loại', <Inbox className="size-4 shrink-0 text-muted-foreground" />)}
      {item(QUEUE, 'Hàng đợi', <ListOrdered className="size-4 shrink-0 text-muted-foreground" />)}
      {item(VOICES, 'Giọng đọc', <Mic className="size-4 shrink-0 text-muted-foreground" />)}
      <div className="mx-2 my-1 hidden h-px bg-border lg:block" />
      {projects.map((p) => item(p.id, p.name, <Dot color={p.color ?? COLORS[0]} />))}
      {naming ? (
        <Input autoFocus className="h-8 w-48 shrink-0 lg:w-full" placeholder="Tên project, Enter để tạo" value={name}
          onChange={(e) => setName(e.target.value)} onBlur={() => done(true)}
          onKeyDown={(e) => { if (e.key === 'Enter') done(true); if (e.key === 'Escape') done(false) }} />
      ) : (
        <button type="button" onClick={() => setNaming(true)}
          className="flex shrink-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground">
          <Plus className="size-4" /> Project mới
        </button>
      )}
    </nav>
  )
}

/** A project's name, color, default options and export folder; saved as they are changed. */
export function ProjectSettings({ project, open, onOpenChange, onDeleted, ...env }: {
  project: Project
  open: boolean
  onOpenChange: (open: boolean) => void
  onDeleted: () => void
  config: Config
  templates: TemplateInfo[]
  drivers: DriverInfo[]
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState(project)
  const [browsing, setBrowsing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const saved = useRef(JSON.stringify(projectBody(project)))
  const update = (patch: Partial<Project>) => setDraft((d) => ({ ...d, ...patch }))

  const save = useMutation({
    mutationFn: async (p: Project) => (await saveProject({ path: { project_id: p.id }, body: projectBody(p) })).data,
    onSuccess: (p) => {
      saved.current = JSON.stringify(projectBody(p))
      queryClient.setQueryData<Project[]>(['projects'], (list = []) => list.map((x) => (x.id === p.id ? p : x)))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  // saved a moment after the last change; an empty name is not saved
  useEffect(() => {
    if (!draft.name.trim() || JSON.stringify(projectBody(draft)) === saved.current) return
    const timer = setTimeout(() => save.mutate(draft), 500)
    return () => clearTimeout(timer)
  }, [draft]) // eslint-disable-line react-hooks/exhaustive-deps

  const remove = useMutation({
    mutationFn: async () => deleteProject({ path: { project_id: project.id } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      toast.success(`Đã xoá ${project.name}. Các việc chuyển về Chưa phân loại`)
      onDeleted()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-md">
        <SheetHeader className="border-b pr-12">
          <SheetTitle>Cài đặt project</SheetTitle>
          <SheetDescription>Mặc định cho video mới thêm vào project. Đổi ở đây không chạy lại video cũ.</SheetDescription>
        </SheetHeader>
        <div className="grid content-start gap-5 overflow-y-auto p-4">
          <Field label="Tên">
            <Input value={draft.name} onChange={(e) => update({ name: e.target.value })} />
          </Field>
          <Field label="Màu">
            <div className="flex gap-2">
              {COLORS.map((c) => (
                <button key={c} type="button" aria-label={`Màu ${c}`} aria-pressed={draft.color === c}
                  onClick={() => update({ color: c })} style={{ background: c }}
                  className={cn('size-6 rounded-full border-2 border-background ring-1 ring-border', draft.color === c && 'ring-2 ring-foreground')} />
              ))}
            </div>
          </Field>
          <JobConfig {...env} project value={projectConfig(draft)} onChange={(c) => update(projectFields(c))}
            context={{ project_id: project.id }} />
          <Field label="Lưu kết quả vào" hint="Để trống: cạnh file gốc">
            <div className="flex gap-2">
              <Input value={draft.export_dir ?? ''} placeholder="Cạnh file gốc" className="font-mono text-xs"
                onChange={(e) => update({ export_dir: e.target.value || null })} />
              <Button type="button" variant="outline" onClick={() => setBrowsing(true)}><FolderOpen /> Duyệt</Button>
            </div>
          </Field>
          <div className="border-t pt-4">
            <Button type="button" variant={confirming ? 'destructive' : 'ghost'} size="sm" disabled={remove.isPending}
              onClick={() => (confirming ? remove.mutate() : setConfirming(true))} onBlur={() => setConfirming(false)}>
              <Trash2 /> {confirming ? 'Bấm lần nữa để xoá' : 'Xoá project'}
            </Button>
            <p className="mt-1 text-xs text-muted-foreground">Các việc của project vẫn giữ, chuyển về Chưa phân loại.</p>
          </div>
        </div>
      </SheetContent>
      <FileBrowser open={browsing} onOpenChange={setBrowsing} initial={draft.export_dir || env.config.root}
        onPick={({ path, kind }) => update({ export_dir: kind === 'dir' ? path : path.split('/').slice(0, -1).join('/') })} />
    </Sheet>
  )
}
