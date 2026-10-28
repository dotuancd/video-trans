import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { deletePreset, savePreset, type DriverInfo, type Preset } from '@/api'
import { DriverParams, DriverSelect, SwitchField, type PreviewContext } from '@/components/Params'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { errorMessage, stageLabel, type Params } from '@/lib/api'

/** What the user changed of a stage of the template: its driver, on/off, and params per driver. */
export type StageForm = { driver?: string; enabled?: boolean; params?: Record<string, Params> }

/** A stage of the template as the user set it; `defaults` are the template's params for it. */
export type Stage = {
  stage: string
  enabled: boolean
  /** the template's own on/off and driver, to count what the user changed */
  base: { enabled: boolean; driver: string }
  driver: DriverInfo | undefined
  drivers: DriverInfo[]
  params: Params
  defaults: Params
}

/** Options shown in the new job card itself, not in the sheet (nor counted as advanced). */
const MAIN: Record<string, string[]> = { translate: ['model'], brief: ['summary'], dub: ['voice', 'ref_audio'] }
const OPTIONAL = ['separate', 'refine']
/** Stages off in the template that run when the user asks (Tạo mindmap): their options can be set while off. */
const ON_DEMAND = ['mindmap']

type Schema = { properties?: Record<string, { default?: unknown }> }
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/** How many advanced options of the stage differ from the template. */
export function changedCount(s: Stage): number {
  const main = MAIN[s.stage] ?? []
  let n = s.enabled !== s.base.enabled ? 1 : 0
  if (s.stage !== 'translate' && s.driver && s.driver.name !== s.base.driver) n++
  if ((!s.enabled && !ON_DEMAND.includes(s.stage)) || !s.driver) return n
  const props = (s.driver.params_schema as Schema).properties ?? {}
  for (const key of Object.keys(props)) {
    if (main.includes(key)) continue
    const initial = key in s.defaults ? s.defaults[key] : props[key].default
    if (key in s.params && !same(s.params[key], initial)) n++
  }
  return n
}

/** The stage's form reset to the template, keeping what the card shows (driver of translate, model, voice). */
export function resetForm(s: Stage, form: StageForm | undefined): StageForm | undefined {
  const main = MAIN[s.stage]
  if (!main || !form) return undefined
  const kept: StageForm = s.stage === 'translate' && form.driver ? { driver: form.driver } : {}
  const name = s.driver?.name
  const params = name ? Object.fromEntries(Object.entries(form.params?.[name] ?? {}).filter(([k]) => main.includes(k))) : {}
  return { ...kept, ...(name && Object.keys(params).length ? { params: { [name]: params } } : {}) }
}

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  stages: Stage[]
  exported: boolean
  onExport: (exported: boolean) => void
  setStage: (stage: string, patch: StageForm) => void
  setParams: (s: Stage, params: Params) => void
  resetStage: (stage: string) => void
  context?: PreviewContext
  /** the preset picked in the card ('' = none), the presets, what to save as one, and pick one */
  preset: string
  presets: Preset[]
  current: Omit<Preset, 'name'>
  onPreset: (name: string) => void
}

export const GENERAL = 'general'

/** The advanced options of a new job in a wide sheet: steps on the left, the params of one on the right. */
export function AdvancedOptions(props: Props) {
  const { open, onOpenChange, stages, exported, onExport, setStage, setParams, context, preset } = props
  const [tab, setTab] = useState(GENERAL)
  const shown = stages.filter((s) => s.driver || OPTIONAL.includes(s.stage))
  const current = shown.find((s) => s.stage === tab)
  const optional = shown.filter((s) => OPTIONAL.includes(s.stage))
  const generalCount = (exported ? 0 : 1) + optional.filter((s) => s.enabled !== s.base.enabled).length
  const count = (s: Stage) => changedCount(s) - (OPTIONAL.includes(s.stage) && s.enabled !== s.base.enabled ? 1 : 0)

  const tabs = [
    { id: GENERAL, label: 'Chung', count: generalCount, off: false },
    ...shown.map((s) => ({ id: s.stage, label: stageLabel(s.stage), count: count(s), off: !s.enabled })),
  ]

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-3xl">
        <SheetHeader className="border-b pr-12">
          <SheetTitle>Tuỳ chọn nâng cao</SheetTitle>
          <SheetDescription>
            {preset ? `Preset “${preset}” · ` : ''}Áp dụng cho việc sắp tạo, tự nhớ cho lần sau.
          </SheetDescription>
        </SheetHeader>

        <div className="grid min-h-0 flex-1 grid-rows-[auto_1fr] sm:grid-cols-[12rem_1fr] sm:grid-rows-1">
          <nav className="flex gap-1 overflow-x-auto border-b bg-muted/40 p-2 sm:flex-col sm:overflow-y-auto sm:border-r sm:border-b-0">
            {tabs.map((t) => (
              <button key={t.id} type="button" onClick={() => setTab(t.id)}
                className={cn('flex shrink-0 items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-sm font-medium whitespace-nowrap transition-colors',
                  t.id === tab ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                  t.off && 'opacity-50')}>
                {t.label}
                <Count n={t.count} />
              </button>
            ))}
          </nav>

          <div className="grid content-start gap-5 overflow-y-auto p-5">
            {!current ? (
              <>
                <SwitchField label="Ghi kết quả cạnh file gốc" hint="<tên>.vi.srt, <tên>.dub.vi.mp4… (luôn tải về được trong việc)"
                  checked={exported} onChange={onExport} />
                {optional.map((s) => (
                  <SwitchField key={s.stage} label={`Bước ${stageLabel(s.stage)}`}
                    hint={s.driver ? s.driver.available ?? s.driver.title : 'Chưa có driver cho bước này'}
                    checked={s.enabled} disabled={!s.driver || !!s.driver.available}
                    onChange={(enabled) => setStage(s.stage, { enabled })} />
                ))}
              </>
            ) : (
              <StagePanel s={current} setStage={setStage} setParams={setParams} context={context} />
            )}
          </div>
        </div>

        <SheetFooter className="flex-row flex-wrap items-center border-t">
          <Button type="button" variant="ghost" size="sm" className="text-muted-foreground"
            onClick={() => props.resetStage(current?.stage ?? GENERAL)}>
            Mặc định cho {current ? 'bước này' : 'mục này'}
          </Button>
          <span className="flex-1" />
          <SavePreset {...props} />
          <Button type="button" size="sm" onClick={() => onOpenChange(false)}>Xong</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

function StagePanel({ s, setStage, setParams, context }: {
  s: Stage
  setStage: Props['setStage']
  setParams: Props['setParams']
  context?: PreviewContext
}) {
  const main = MAIN[s.stage] ?? []
  const keys = Object.keys((s.driver?.params_schema as Schema | undefined)?.properties ?? {})
  const rest = keys.filter((k) => !main.includes(k))
  return (
    <>
      <p className="flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {stageLabel(s.stage)}
        {s.driver && <span className="font-mono normal-case tracking-normal">{s.driver.name}</span>}
      </p>
      {!s.enabled && s.driver && ON_DEMAND.includes(s.stage) && (
        <p className="text-sm text-muted-foreground">Chỉ chạy khi bạn bấm “Tạo mindmap”, với các tuỳ chọn này.</p>
      )}
      {!s.enabled && !(s.driver && ON_DEMAND.includes(s.stage)) ? (
        <div className="grid justify-items-start gap-3">
          <p className="text-sm text-muted-foreground">
            {s.driver ? 'Bước này đang tắt.' : 'Chưa có driver cho bước này.'}
          </p>
          {s.driver && !s.driver.available && (
            <Button type="button" variant="outline" size="sm" onClick={() => setStage(s.stage, { enabled: true })}>Bật bước này</Button>
          )}
        </div>
      ) : (
        <>
          {s.drivers.length > 1 && s.stage !== 'translate' && (
            <DriverSelect drivers={s.drivers} value={s.driver!.name} onChange={(driver) => setStage(s.stage, { driver })} />
          )}
          {rest.length ? (
            <DriverParams driver={s.driver!} value={s.params} context={context}
              only={main.length ? main.map((k) => `!${k}`) : undefined}
              onChange={(params) => setParams(s, params)} />
          ) : (
            <p className="text-sm text-muted-foreground">Bước này không có tuỳ chọn thêm (tuỳ chọn chính nằm ở form Việc mới).</p>
          )}
        </>
      )}
    </>
  )
}

/** Save the options as a preset (a new name, or over the picked one), or delete the picked one. */
function SavePreset({ preset, presets, current, onPreset }: Props) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const trimmed = name.trim()
  const exists = presets.some((p) => p.name === trimmed)
  const done = () => {
    setOpen(false)
    void queryClient.invalidateQueries({ queryKey: ['presets'] })
  }
  const save = useMutation({
    mutationFn: async () => (await savePreset({ path: { name: trimmed }, body: current })).data,
    onSuccess: (saved) => {
      queryClient.setQueryData<Preset[]>(['presets'], (list = []) => [...list.filter((p) => p.name !== saved.name), saved])
      onPreset(saved.name)
      toast.success(`Đã lưu preset “${saved.name}”`)
      done()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const remove = useMutation({
    mutationFn: async () => deletePreset({ path: { name: preset } }),
    onSuccess: () => {
      queryClient.setQueryData<Preset[]>(['presets'], (list = []) => list.filter((p) => p.name !== preset))
      toast.success(`Đã xoá preset “${preset}”`)
      onPreset('')
      done()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setName(preset) }}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" size="sm"><Save /> Lưu preset</Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-72 gap-3">
        <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (trimmed) save.mutate() }}>
          <p className="text-sm font-medium">Lưu thành preset</p>
          <Input autoFocus value={name} placeholder="VD: Anime JP→VI" maxLength={80} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" size="sm" disabled={!trimmed || save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />}
            {exists ? `Ghi đè “${trimmed}”` : 'Lưu preset mới'}
          </Button>
        </form>
        {preset && (
          <Button type="button" variant="ghost" size="sm" className="justify-start text-destructive hover:text-destructive"
            disabled={remove.isPending} onClick={() => remove.mutate()}>
            <Trash2 /> Xoá preset “{preset}”
          </Button>
        )}
      </PopoverContent>
    </Popover>
  )
}

export function Count({ n }: { n: number }) {
  if (!n) return null
  return (
    <span className="inline-grid h-5 min-w-5 place-items-center rounded-full bg-primary/10 px-1.5 text-[11px] font-semibold text-primary tabular-nums">
      {n}
    </span>
  )
}
