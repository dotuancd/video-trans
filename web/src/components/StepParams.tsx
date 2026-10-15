import { useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader2, Play, Upload, X } from 'lucide-react'
import { toast } from 'sonner'
import { listVoices, uploadVoice } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { errorMessage, fileName, type StepName, type StepOptions } from '@/lib/api'

/** voice value: clone the speaker of the video (the dub step's default) */
const SOURCE_VOICE = 'source'

/** The files the voice preview can use: the video to clone from and the .vi.srt to read.
 * Either is enough: the server finds the other (and the .srt timing the clip) next to it. */
export type PreviewContext = { media?: string; subtitles?: string }

type FieldsProps = {
  name: StepName
  value: StepOptions
  onChange: (value: StepOptions) => void
  /** main: the options most people change · extra: the rest · all: both */
  mode?: 'main' | 'extra' | 'all'
  context?: PreviewContext
}

/** The options of one pipeline step: used when starting jobs and when running a step again. */
export function StepParams({ name, value, onChange, mode = 'all', context }: FieldsProps) {
  const set = (key: string, v: StepOptions[string]) => onChange({ ...value, [key]: v })
  const main = mode !== 'extra'
  const advanced = mode !== 'main'
  switch (name) {
    case 'transcribe':
      return advanced ? (
        <Field label="Ngôn ngữ nói" hint="Để trống để tự nhận">
          <Input
            value={(value.language as string) ?? ''}
            placeholder="en"
            onChange={(e) => set('language', e.target.value || null)}
          />
        </Field>
      ) : null
    case 'translate': {
      const engine = value.engine as string
      return (
        <div className="grid gap-4">
          {main && <Field label="Engine dịch">
            <Select value={engine} onValueChange={(v) => set('engine', v)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">Local — chạy trên máy, miễn phí</SelectItem>
                <SelectItem value="openai">OpenAI — cần OPENAI_API_KEY</SelectItem>
                <SelectItem value="claude">Claude — dùng gói Pro/Max</SelectItem>
              </SelectContent>
            </Select>
          </Field>}
          {main && engine === 'openai' && (
            <Field label="Model OpenAI">
              <Input
                value={(value.openai_model as string) ?? ''}
                placeholder="gpt-5-mini"
                onChange={(e) => set('openai_model', e.target.value || null)}
              />
            </Field>
          )}
          {main && engine === 'claude' && (
            <Field label="Model Claude">
              <Select value={(value.claude_model as string) ?? 'sonnet'} onValueChange={(v) => set('claude_model', v)}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="sonnet">Sonnet</SelectItem>
                  <SelectItem value="haiku">Haiku (nhanh)</SelectItem>
                  <SelectItem value="opus">Opus (tốt nhất)</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          )}
          {advanced && engine === 'local' && (
            <>
              <Field label="Model local">
                <Select value={value.model as string} onValueChange={(v) => set('model', v)}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="vinai">VinAI EN→VI (khuyên dùng)</SelectItem>
                    <SelectItem value="opus">Opus-MT (nhanh, kém hơn)</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <SliderField label="Beam" hint="1 = nhanh nhất" min={1} max={8} step={1}
                value={value.beam as number} onChange={(v) => set('beam', v)} />
            </>
          )}
          {advanced && engine !== 'local' && (
            <SliderField label="Request song song" min={1} max={8} step={1}
              value={value.workers as number} onChange={(v) => set('workers', v)} />
          )}
        </div>
      )
    }
    case 'dub':
      return (
        <div className="grid gap-4">
          {main && <VoicePicker value={value} onChange={onChange} context={context} />}
          {advanced && (
            <>
              <SliderField label="Tăng tốc tối đa" hint="Khi câu tiếng Việt dài hơn khoảng trống" min={1} max={2}
                step={0.05} format={(v) => `${v.toFixed(2)}×`}
                value={value.max_speed as number} onChange={(v) => set('max_speed', v)} />
              <SwitchField label="Model int8" hint="Nhanh hơn, có thể méo tiếng"
                checked={!!value.int8} onChange={(v) => set('int8', v)} />
            </>
          )}
        </div>
      )
    case 'mux':
      return advanced ? (
        <div className="grid gap-4">
          <SliderField label="Âm lượng audio gốc" hint="0 = bỏ hẳn" min={0} max={1} step={0.05}
            format={(v) => `${Math.round(v * 100)}%`}
            value={value.original_volume as number} onChange={(v) => set('original_volume', v)} />
          <SwitchField label="Giữ audio gốc" hint="Thêm track lồng tiếng thay vì thay thế"
            checked={!!value.add_track} onChange={(v) => set('add_track', v)} />
        </div>
      ) : null
  }
}

function VoicePicker({ value, onChange, context }: {
  value: StepOptions
  onChange: (v: StepOptions) => void
  context?: PreviewContext
}) {
  const voices = useQuery({
    queryKey: ['voices'],
    queryFn: async () => (await listVoices()).data,
    staleTime: Infinity,
    retry: false,
  })
  const fileInput = useRef<HTMLInputElement>(null)
  const [heard, setHeard] = useState<{ url: string; text: string }>()
  const refAudio = value.ref_audio as string | null
  const fromSource = !refAudio && value.voice === SOURCE_VOICE
  // the video voice needs a single video (or its subtitles): a folder has none to clone from
  const canPreview = fromSource ? !!(context?.media || context?.subtitles) : !!(voices.data || refAudio)

  const upload = useMutation({
    mutationFn: async (file: File) => (await uploadVoice({ body: { file } })).data,
    onSuccess: (data) => onChange({ ...value, ref_audio: data.path }),
    onError: (error) => toast.error(errorMessage(error)),
  })
  const preview = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/voices/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // no text: the server reads a sentence of the .vi.srt (the moment the voice was cloned from)
        body: JSON.stringify({ voice: value.voice, ref_audio: refAudio, int8: !!value.int8, ...context }),
      })
      if (!response.ok) throw new Error((await response.json()).detail ?? response.statusText)
      const text = decodeURIComponent(response.headers.get('X-Preview-Text') ?? '')
      return { url: URL.createObjectURL(await response.blob()), text }
    },
    onSuccess: (result) => {
      if (heard) URL.revokeObjectURL(heard.url)
      setHeard(result)
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const placeholder = voices.isLoading ? 'Đang tải giọng…' : voices.isError ? errorMessage(voices.error) : 'Giọng mặc định'
  return (
    <div className="grid gap-2">
      <Label>Giọng đọc</Label>
      <div className="flex gap-2">
        {refAudio ? (
          <div className="flex h-9 w-0 min-w-0 flex-1 items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm">
            <span className="truncate">Clone: {fileName(refAudio)}</span>
            <button type="button" className="ml-auto text-muted-foreground hover:text-foreground"
              aria-label="Bỏ giọng clone" onClick={() => onChange({ ...value, ref_audio: null })}>
              <X className="size-4" />
            </button>
          </div>
        ) : (
          <Select value={(value.voice as string) ?? undefined} onValueChange={(v) => onChange({ ...value, voice: v })}>
            <SelectTrigger className="w-0 min-w-0 flex-1 overflow-hidden *:data-[slot=select-value]:block *:data-[slot=select-value]:truncate">
              <SelectValue placeholder={placeholder} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SOURCE_VOICE}>Giọng gốc (clone từ video)</SelectItem>
              {voices.data?.map((voice) => (
                <SelectItem key={voice.name} value={voice.name}>{voice.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button type="button" variant="outline" size="icon" title="Clone giọng từ clip 3–8 giây"
          onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
          {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
        </Button>
        <Button type="button" variant="outline" size="icon" onClick={() => preview.mutate()}
          title={canPreview ? 'Nghe thử một câu của phụ đề' : 'Chọn một video (không phải thư mục) để nghe thử giọng gốc'}
          disabled={preview.isPending || !canPreview}>
          {preview.isPending ? <Loader2 className="animate-spin" /> : <Play />}
        </Button>
        <input ref={fileInput} type="file" accept="audio/*" hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload.mutate(file)
            e.target.value = ''
          }} />
      </div>
      {fromSource && (
        <p className="text-xs text-muted-foreground">
          Lấy 3–8 giây người nói trong video làm mẫu. Không có video thì dùng giọng mặc định.
        </p>
      )}
      {heard && (
        <>
          <audio src={heard.url} controls autoPlay className="h-9 w-full" />
          {heard.text && <p className="text-xs text-muted-foreground italic">“{heard.text}”</p>}
        </>
      )}
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function SliderField({ label, hint, value, onChange, min, max, step, format = String }: {
  label: string
  hint?: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step: number
  format?: (v: number) => string
}) {
  return (
    <div className="grid gap-3">
      <div className="flex items-baseline justify-between">
        <Label>{label}</Label>
        <span className="text-sm tabular-nums text-muted-foreground">{format(value)}</span>
      </div>
      <Slider value={[value]} min={min} max={max} step={step} onValueChange={([v]) => onChange(v)} />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function SwitchField({ label, hint, checked, onChange }: {
  label: string
  hint?: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4">
      <span className="grid gap-0.5">
        <span className="text-sm font-medium">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  )
}
