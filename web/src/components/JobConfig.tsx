import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RotateCcw, SlidersHorizontal } from 'lucide-react'
import { listPresets, type Config, type DriverInfo, type Preset, type Project, type TemplateInfo } from '@/api'
import { AdvancedOptions, changedCount, Count, GENERAL, resetForm, type Stage, type StageForm } from '@/components/AdvancedOptions'
import { DriverParams, DriverSelect, Field, type PreviewContext } from '@/components/Params'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { driversOf, type Params } from '@/lib/api'

/** What a job runs with: the template, target languages and the options (shaped like a preset). */
export type RunConfig = {
  template: string
  langs: string[]
  /** the preset the options were taken from ('' = none) */
  preset: string
  export: boolean
  stages: Record<string, StageForm>
}

/** Select value of "no preset": the template as it is */
const NO_PRESET = '-'
/** What "Mặc định" sets: what a preset is compared with to tell it was changed */
const BLANK: Omit<Preset, 'name'> = { export: true, stages: {} }
export const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export const projectConfig = (p: Project): RunConfig => ({
  template: p.template ?? 'default', langs: p.langs ?? [], preset: p.preset ?? '',
  export: p.options?.export ?? true, stages: (p.options?.stages ?? {}) as RunConfig['stages'],
})
/** What is saved of a project (no id, counts) */
export const projectBody = ({ name, color, template, langs, options, preset, export_dir }: Project) =>
  ({ name, color, template, langs, options, preset, export_dir })
export const projectFields = (c: RunConfig) => ({
  template: c.template, langs: c.langs, preset: c.preset || null, options: { export: c.export, stages: c.stages },
})

type Env = { config: Config; templates: TemplateInfo[]; drivers: DriverInfo[] }

/** The template's stages as the user set them (driver, on/off, params), and the body of a job created with them. */
export function resolve(value: RunConfig, { config, templates, drivers }: Env) {
  const template = templates.find((t) => t.name === value.template) ?? templates[0]
  const stages: Stage[] = (template?.stages ?? []).filter((s) => s.stage !== 'source').map((conf) => {
    const choices = driversOf(drivers, conf.stage as string)
    const set = value.stages[conf.stage as string] ?? {}
    const fallback = conf.driver === 'env' ? config.default_drivers[conf.stage as string] : (conf.driver as string)
    const name = set.driver ?? fallback
    const driver = choices.find((d) => d.name === name)
    return {
      stage: conf.stage as string,
      enabled: !!driver && (set.enabled ?? (conf.enabled as boolean)),
      base: { enabled: !!choices.find((d) => d.name === fallback) && (conf.enabled as boolean), driver: fallback },
      driver, drivers: choices,
      params: { ...(conf.params as Params), ...(set.params?.[name] ?? {}) },
      defaults: conf.params as Params,
    }
  })
  const perLang = stages.some((s) => s.enabled && s.driver?.per_lang)
  const overrides = Object.fromEntries(stages.map((s) => [s.stage, {
    enabled: s.enabled, ...(s.driver && { driver: s.driver.name, params: s.params }),
  }]))
  return {
    template, stages, perLang,
    valid: !perLang || value.langs.length > 0,
    body: { langs: perLang ? value.langs : [], template: template?.name ?? value.template, overrides, export: value.export },
  }
}

/** "Dịch + lồng tiếng · vi, ja · Chất lượng" */
export function configText(value: RunConfig, env: Env) {
  const { template, perLang } = resolve(value, env)
  return [template?.title || template?.name, perLang ? value.langs.join(', ') || '—' : null, value.preset || 'Mặc định']
    .filter(Boolean).join(' · ')
}

/** Which parts differ from `base` (a project's defaults), by label. */
function differences(value: RunConfig, base: RunConfig) {
  const out: string[] = []
  if (value.template !== base.template) out.push('việc cần làm')
  if (!same([...value.langs].sort(), [...base.langs].sort())) out.push('ngôn ngữ')
  if (value.preset !== base.preset || !same(clean(value), clean(base))) out.push('tuỳ chọn')
  return out
}

export function differsFrom(value: RunConfig, base: RunConfig) {
  return differences(value, base).length > 0
}

/** Template, target languages, translate engine, voice, preset and advanced options. With `base`, what
 * differs from it is named and can be reset. */
export function JobConfig({ value, onChange, base, context, project, ...env }: Env & {
  value: RunConfig
  onChange: (value: RunConfig) => void
  base?: RunConfig
  context?: PreviewContext
  /** a project's defaults: the brief shows its instructions (shared by its videos), not one video's summary */
  project?: boolean
}) {
  const { config, templates } = env
  const [advanced, setAdvanced] = useState(false)
  const update = (patch: Partial<RunConfig>) => onChange({ ...value, ...patch })
  const { template, stages, perLang } = resolve(value, env)
  const stageOf = (name: string) => stages.find((s) => s.stage === name && s.enabled)

  const setStage = (stage: string, patch: StageForm) =>
    update({ stages: { ...value.stages, [stage]: { ...value.stages[stage], ...patch } } })
  const setParams = (s: Stage, params: Params) =>
    setStage(s.stage, { params: { ...value.stages[s.stage]?.params, [s.driver!.name]: params } })
  const resetStage = (stage: string) => {
    if (stage !== GENERAL) {
      const s = stages.find((x) => x.stage === stage)!
      const { [stage]: _old, ...others } = value.stages
      const kept = resetForm(s, value.stages[stage])
      return update({ stages: kept ? { ...others, [stage]: kept } : others })
    }
    // on/off of the optional steps lives in Chung
    const stagesOn = Object.fromEntries(Object.entries(value.stages).map(([k, v]) => [k, { ...v, enabled: undefined }]))
    update({ export: true, stages: stagesOn })
  }

  const presets = useQuery({ queryKey: ['presets'], queryFn: async () => (await listPresets()).data, staleTime: Infinity })
  const picked = presets.data?.find((p) => p.name === value.preset)
  const options: Omit<Preset, 'name'> = { export: value.export, stages: value.stages }
  const edited = !same(clean(options), clean(picked ?? BLANK))
  const pickPreset = (name: string) => {
    const p = presets.data?.find((x) => x.name === name)
    update({ preset: p ? name : '', export: p?.export ?? true, stages: (p?.stages ?? {}) as RunConfig['stages'] })
  }
  /** after saving: the options stay, only the name they belong to changes */
  const namePreset = (name: string) => update({ preset: name })
  const changed = (value.export ? 0 : 1) + stages.reduce((n, s) => n + changedCount(s), 0)

  const translate = stageOf('translate')
  // the brief only serves an LLM translation (the server turns it off otherwise)
  const brief = translate?.driver?.reads?.includes('brief') ? stageOf('brief') : undefined
  const dub = stageOf('dub')
  const diff = base ? differences(value, base) : []

  function presetHint() {
    if (picked && edited) return `Đã sửa so với “${picked.name}”, chưa lưu`
    if (picked) return changed ? `${changed} tuỳ chọn khác mặc định` : undefined
    if (changed) return `${changed} tuỳ chọn khác mặc định · lưu thành preset trong Nâng cao`
    return presets.data?.length ? undefined : 'Lưu tuỳ chọn nâng cao thành preset để dùng lại'
  }

  return (
    <div className="grid gap-5">
      {base && diff.length > 0 && (
        <div className="flex items-center gap-2 rounded-md bg-sky-50 px-3 py-2 text-xs text-sky-800 dark:bg-sky-950/50 dark:text-sky-200">
          <span className="min-w-0 flex-1">Khác cấu hình project: {diff.join(', ')}</span>
          <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => onChange(base)}>
            <RotateCcw /> Đặt lại
          </Button>
        </div>
      )}

      <Field label="Việc cần làm" hint={template?.description}>
        <div className="grid grid-cols-2 gap-1 rounded-lg border bg-muted/40 p-1">
          {templates.map((t) => (
            <button key={t.name} type="button" onClick={() => update({ template: t.name })}
              className={cn('rounded-md px-2 py-1.5 text-xs font-medium transition-colors sm:text-sm',
                t.name === template?.name ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
              {t.title || t.name}
            </button>
          ))}
        </div>
      </Field>

      {perLang && (
        <Field label="Dịch sang">
          <div className="flex flex-wrap gap-1.5">
            {config.languages.map((lang) => {
              const on = value.langs.includes(lang.code)
              return (
                <button key={lang.code} type="button" aria-pressed={on}
                  onClick={() => update({ langs: on ? value.langs.filter((l) => l !== lang.code) : [...value.langs, lang.code] })}
                  className={cn('rounded-full border px-3 py-1 text-xs transition-colors',
                    on ? 'border-primary bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>
                  {lang.name}
                </button>
              )
            })}
          </div>
        </Field>
      )}

      {translate && (
        <div className="grid gap-4">
          <Field label="Dịch bằng">
            <DriverSelect drivers={translate.drivers} value={translate.driver!.name}
              onChange={(driver) => setStage('translate', { driver })} />
          </Field>
          <DriverParams driver={translate.driver!} value={translate.params} only={['model']}
            onChange={(params) => setParams(translate, params)} />
          {translate.driver?.langs && value.langs.some((l) => !translate.driver!.langs!.includes(l)) && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              {translate.driver.title} chỉ dịch sang {translate.driver.langs.join(', ')}: ngôn ngữ khác sẽ bị bỏ qua.
            </p>
          )}
        </div>
      )}

      {brief && (
        <DriverParams driver={brief.driver!} value={brief.params} only={[project ? 'instructions' : 'summary']}
          onChange={(params) => setParams(brief, params)} />
      )}

      {dub && (
        <DriverParams driver={dub.driver!} value={dub.params} only={['voice']} context={context}
          onChange={(params) => setParams(dub, params)} />
      )}

      <Field label="Cấu hình" hint={presetHint()}>
        <div className="flex gap-2">
          <Select value={picked ? picked.name : NO_PRESET} onValueChange={(v) => pickPreset(v === NO_PRESET ? '' : v)}>
            <SelectTrigger className="w-0 min-w-0 flex-1 overflow-hidden *:data-[slot=select-value]:block *:data-[slot=select-value]:truncate">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_PRESET}>Mặc định</SelectItem>
              {presets.data?.map((p) => <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button type="button" variant="outline" onClick={() => setAdvanced(true)} title="Tuỳ chọn nâng cao">
            <SlidersHorizontal /> Nâng cao <Count n={changed} />
          </Button>
        </div>
      </Field>

      <AdvancedOptions open={advanced} onOpenChange={setAdvanced} stages={stages} exported={value.export}
        onExport={(exported) => update({ export: exported })} setStage={setStage} setParams={setParams}
        resetStage={resetStage} context={context}
        preset={picked?.name ?? ''} presets={presets.data ?? []} current={options} onPreset={namePreset} />
    </div>
  )
}

/** Options without empty parts, to compare them with a preset whatever way they were set. */
function clean(options: Omit<Preset, 'name'>) {
  const stages = Object.entries(options.stages ?? {}).flatMap(([stage, set]) => {
    const params = Object.entries(set.params ?? {}).filter(([, p]) => p && Object.keys(p).length)
    const kept = {
      ...(set.driver != null && { driver: set.driver }),
      ...(set.enabled != null && { enabled: set.enabled }),
      ...(params.length && { params: Object.fromEntries(params.sort(([a], [b]) => a.localeCompare(b))) }),
    }
    return Object.keys(kept).length ? [[stage, kept] as const] : []
  }).sort(([a], [b]) => a.localeCompare(b))
  return { export: options.export ?? true, stages: Object.fromEntries(stages) }
}
