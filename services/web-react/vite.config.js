import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-only convenience: proxy the same paths nginx.conf proxies in production,
// so `npm run dev` can talk to a real gateway without setting agora.gatewayBase.
const gatewayProxyTarget = process.env.VITE_GATEWAY_PROXY_TARGET || 'http://localhost:8090'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '^/(api/|auth/|audit|users|me|agents|agent-instances|mailboxes|health)': {
        target: gatewayProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
