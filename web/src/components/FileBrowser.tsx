import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowUp, Captions, FileAudio, FileText, FileVideo, Folder, Loader2 } from 'lucide-react'
import { listDir, type Entry } from '@/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { errorMessage } from '@/lib/api'

const ICONS = { dir: Folder, media: FileVideo, srt: Captions, vi_srt: Captions, dub: FileVideo, audio: FileAudio, other: FileText }
const SELECTABLE = new Set(['dir', 'media', 'srt', 'vi_srt'])

/** Pick a folder or a video / .srt / .vi.srt file under the server's root. */
export function FileBrowser({ open, onOpenChange, initial, onPick }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial?: string
  onPick: (entry: { path: string; kind: Entry['kind'] }) => void
}) {
  const [folder, setFolder] = useState(initial ?? '')
  const [selected, setSelected] = useState<Entry>()
  const listing = useQuery({
    queryKey: ['fs', folder],
    queryFn: async () => (await listDir({ query: { path: folder } })).data,
    enabled: open,
  })

  const go = (path: string) => {
    setFolder(path)
    setSelected(undefined)
  }
  const pick = (entry: { path: string; kind: Entry['kind'] }) => {
    onPick(entry)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Chọn video, phụ đề hoặc thư mục</DialogTitle>
          <DialogDescription className="font-mono text-xs break-all">{listing.data?.path ?? folder}</DialogDescription>
        </DialogHeader>
        <ScrollArea className="h-[min(60vh,28rem)] rounded-md border">
          {listing.isLoading && <Loader2 className="m-4 animate-spin text-muted-foreground" />}
          {listing.isError && <p className="p-4 text-sm text-destructive">{errorMessage(listing.error)}</p>}
          <ul className="p-1">
            {listing.data?.parent && (
              <Row icon={ArrowUp} name=".." onClick={() => go(listing.data!.parent!)} onOpen={() => go(listing.data!.parent!)} />
            )}
            {listing.data?.entries.map((entry) => (
              <Row
                key={entry.path}
                icon={ICONS[entry.kind]}
                name={entry.name}
                muted={!SELECTABLE.has(entry.kind)}
                active={selected?.path === entry.path}
                onClick={() => (entry.dir ? go(entry.path) : SELECTABLE.has(entry.kind) && setSelected(entry))}
                onOpen={() => !entry.dir && SELECTABLE.has(entry.kind) && pick(entry)}
              />
            ))}
          </ul>
        </ScrollArea>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="outline" disabled={!listing.data}
            onClick={() => listing.data && pick({ path: listing.data.path, kind: 'dir' })}>
            Dùng thư mục này
          </Button>
          <Button disabled={!selected} onClick={() => selected && pick(selected)}>
            Chọn {selected ? `“${selected.name}”` : ''}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Row({ icon: Icon, name, muted, active, onClick, onOpen }: {
  icon: typeof Folder
  name: string
  muted?: boolean
  active?: boolean
  onClick: () => void
  onOpen: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        onDoubleClick={onOpen}
        className={cn(
          'flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent',
          active && 'bg-accent font-medium',
          muted && 'text-muted-foreground',
        )}
      >
        <Icon className="size-4 shrink-0" />
        <span className="truncate">{name}</span>
      </button>
    </li>
  )
}
