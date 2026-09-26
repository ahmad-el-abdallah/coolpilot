import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: run with VITE_TUF_TOKEN=<token from /etc/tuf-control/token>; changeOrigin keeps the Host check happy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8787', changeOrigin: true } },
  },
})
