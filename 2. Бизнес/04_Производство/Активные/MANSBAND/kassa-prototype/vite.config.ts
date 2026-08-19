import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteSingleFile } from 'vite-plugin-singlefile'

// https://vite.dev/config/
// Сборка в ОДИН index.html (JS+CSS+картинки вшиты внутрь) —
// файл открывается двойным кликом из file:// без сервера.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  base: './',
  build: {
    // Инлайнить все ассеты в base64 (логотип ~16кб и т.п.)
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    assetsDir: '.',
  },
})
