import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from '@/components/ui/sonner'
import App from './App.tsx'
import { BulkDownloadToasts } from '@/lib/bulkDownloads'
import './index.css'

// follow the system light/dark setting
const dark = matchMedia('(prefers-color-scheme: dark)')
const applyTheme = () => document.documentElement.classList.toggle('dark', dark.matches)
applyTheme()
dark.addEventListener('change', applyTheme)

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <BulkDownloadToasts />
      {/* pointer-events: the open job sheet is modal and disables them on <body> */}
      <Toaster richColors position="bottom-right" style={{ pointerEvents: 'auto' }} />
    </QueryClientProvider>
  </StrictMode>,
)
