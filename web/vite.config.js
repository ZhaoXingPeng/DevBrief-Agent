import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/health': 'http://127.0.0.1:8765',
    },
  },
  resolve: {
    alias: {
      vue: 'vue/dist/vue.esm-bundler.js',
    },
  },
  build: {
    outDir: '../src/devbrief/integration/static',
    emptyOutDir: true,
  },
})
