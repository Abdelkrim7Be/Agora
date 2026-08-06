import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev-only convenience: proxy the same paths nginx.conf proxies in production,
// so `npm run dev` can talk to a real gateway without setting agora.gatewayBase.
const gatewayProxyTarget = process.env.VITE_GATEWAY_PROXY_TARGET || 'http://localhost:8090'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Matches the `@/` prefix shadcn generates, so components can be copied in
    // from the registry without rewriting every import.
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  server: {
    proxy: {
      '^/(api/|auth/|audit|users|me|agents|agent-instances|mailboxes|health)': {
        target: gatewayProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
