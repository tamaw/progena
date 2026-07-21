import http from 'node:http'
import https from 'node:https'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const frontendDir = fileURLToPath(new URL('.', import.meta.url))

function shouldProxy(pathname) {
  return pathname.startsWith('/api/') || pathname.startsWith('/v1/')
}

const keepAliveHttpAgent = new http.Agent({ keepAlive: true })
const keepAliveHttpsAgent = new https.Agent({ keepAlive: true })

function createBackendProxyMiddleware(backendBaseUrl) {
  return (req, res, next) => {
    const requestUrl = req.url || '/'
    const pathname = new URL(requestUrl, backendBaseUrl).pathname

    if (!shouldProxy(pathname)) {
      next()
      return
    }

    const targetUrl = new URL(requestUrl, backendBaseUrl)
    const isHttps = targetUrl.protocol === 'https:'
    const client = isHttps ? https : http
    // Keep-alive agent: without it, every proxied request (including every
    // chat POST) pays a fresh TCP connection setup to the backend, adding
    // ~1s of pure connection overhead on top of actual request handling.
    const upstream = client.request(
      targetUrl,
      {
        method: req.method,
        agent: isHttps ? keepAliveHttpsAgent : keepAliveHttpAgent,
        headers: {
          ...req.headers,
          host: targetUrl.host,
          connection: 'keep-alive',
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

function createBackendProxyPlugin(backendBaseUrl) {
  return {
    name: 'backend-proxy',
    enforce: 'pre',
    configureServer(server) {
      server.middlewares.use(createBackendProxyMiddleware(backendBaseUrl))
    },
    configurePreviewServer(server) {
      server.middlewares.use(createBackendProxyMiddleware(backendBaseUrl))
    },
  }
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
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, frontendDir, 'VITE_')
  const backendBaseUrl = new URL(
    env.VITE_BACKEND_BASE_URL
    || globalThis.process?.env?.VITE_BACKEND_BASE_URL
    || 'http://127.0.0.1:8000'
  )

  return {
    plugins: [createBackendProxyPlugin(backendBaseUrl), react()],
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
  }
})
