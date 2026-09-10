import { defineConfig } from 'vite'

export default defineConfig({
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
