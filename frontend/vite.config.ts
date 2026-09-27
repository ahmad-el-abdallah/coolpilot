import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: run with VITE_COOLPILOT_TOKEN=<token from /etc/coolpilot/token>; changeOrigin keeps the Host check happy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8787', changeOrigin: true } },
  },
})
