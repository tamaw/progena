import http from 'node:http'
import https from 'node:https'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendBaseUrl = new URL(globalThis.process?.env?.VITE_BACKEND_BASE_URL || 'http://127.0.0.1:8000')

function shouldProxy(pathname) {
  return pathname.startsWith('/api/') || pathname.startsWith('/v1/')
}

function createBackendProxyMiddleware() {
  return (req, res, next) => {
    const requestUrl = req.url || '/'
    const pathname = new URL(requestUrl, backendBaseUrl).pathname

    if (!shouldProxy(pathname)) {
      next()
      return
    }

    const targetUrl = new URL(requestUrl, backendBaseUrl)
    const client = targetUrl.protocol === 'https:' ? https : http
    const upstream = client.request(
      targetUrl,
      {
        method: req.method,
        headers: {
          ...req.headers,
          host: targetUrl.host,
          connection: 'close',
        },
      },
      (upstreamRes) => {
        res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers)
        upstreamRes.pipe(res)
      }
    )

    upstream.on('error', (error) => {
      res.statusCode = 502
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ error: `Proxy error: ${error.message}` }))
    })

    req.pipe(upstream)
  }
}

const backendProxyPlugin = {
  name: 'backend-proxy',
  enforce: 'pre',
  configureServer(server) {
    server.middlewares.use(createBackendProxyMiddleware())
  },
  configurePreviewServer(server) {
    server.middlewares.use(createBackendProxyMiddleware())
  },
}

function manualChunks(id) {
  if (!id.includes('/node_modules/')) return undefined

  if (id.includes('/highlight.js/')) return 'syntax-highlighting'
  if (id.includes('/@xyflow/')) return 'flow-vendor'
  if (id.includes('/chart.js/') || id.includes('/react-chartjs-2/')) return 'charts-vendor'
  if (id.includes('/@reduxjs/') || id.includes('/react-redux/')) return 'state-vendor'
  if (
    id.includes('/react/')
    || id.includes('/react-dom/')
    || id.includes('/react-router-dom/')
    || id.includes('/react-router/')
    || id.includes('/scheduler/')
  ) return 'react-vendor'

  return undefined
}


// https://vite.dev/config/
export default defineConfig({
  plugins: [backendProxyPlugin, react()],
  base: '/',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  },
  server: {
    port: 5173,
    watch: {
      usePolling: true,
      interval: 150,
      awaitWriteFinish: {
        stabilityThreshold: 120,
        pollInterval: 20,
      },
    },
    proxy: {}
  },
  preview: {
    port: 4173,
    proxy: {}
  }
})
