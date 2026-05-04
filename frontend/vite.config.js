import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Hand-tuned manual chunks — Vite warns when any single chunk
        // exceeds 500 kB. The biggest contributors to our main bundle were
        // recharts (~200 kB), React+ReactDOM (~150 kB), and our app code
        // (~250 kB). Splitting recharts and react-vendor into their own
        // chunks lets the browser cache them across deploys (recharts
        // rarely changes; React major bumps are sparse) and brings the
        // main bundle below the warning threshold.
        manualChunks: (id) => {
          if (id.includes('node_modules/recharts')) return 'recharts'
          if (
            id.includes('node_modules/react/') ||
            id.includes('node_modules/react-dom/') ||
            id.includes('node_modules/scheduler/')
          ) {
            return 'react-vendor'
          }
        },
      },
    },
    // The PDF libraries (html2canvas, jspdf) are already dynamic-imported
    // in ReportDisplay.tsx and end up in their own async chunks. Bumping
    // the warning threshold to 600 kB so the build is silent unless we
    // genuinely regress past the new vendor split.
    chunkSizeWarningLimit: 600,
  },
})
