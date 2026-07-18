import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const versionSource = readFileSync(
  new URL('../src/research_monitor/_version.py', import.meta.url),
  'utf8',
)
const version = versionSource.match(/^VERSION = "([^"]+)"$/m)?.[1]
if (!version) throw new Error('Unable to read Research Monitor release version')

export default defineConfig({
  define: { __RESEARCH_MONITOR_VERSION__: JSON.stringify(version) },
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        // Preserve the production server's exact-Origin policy while using
        // Vite as a same-machine development reverse proxy.
        headers: { Origin: 'http://127.0.0.1:8765' },
      },
    },
  },
})
