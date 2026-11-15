/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        noc: {
          950: '#0B0F17', // Main background
          900: '#111827', // Card surface
          850: '#161F30', // Surface elevated
          800: '#1E293B', // Subtle borders
          700: '#334155', // Muted borders & icons
          600: '#475569',
          400: '#94A3B8', // Secondary text
          200: '#E2E8F0', // Primary text
          100: '#F1F5F9', // Heading highlight
        },
        accent: {
          cyan: '#06B6D4',
          sky: '#0EA5E9',
          indigo: '#6366F1',
        },
        status: {
          healthy: '#10B981',
          warning: '#F59E0B',
          critical: '#F43F5E',
          neutral: '#64748B',
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
