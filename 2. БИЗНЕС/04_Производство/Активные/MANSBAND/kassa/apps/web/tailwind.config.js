/** @type {import('tailwindcss').Config} */
const rgb = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: rgb("--ink-950"),
          900: rgb("--ink-900"),
          850: rgb("--ink-850"),
          800: rgb("--ink-800"),
          700: rgb("--ink-700"),
          600: rgb("--ink-600"),
          500: rgb("--ink-500"),
        },
        // Монохромный акцент (фирстиль MANSBAND: чёрное/белое)
        gold: {
          DEFAULT: rgb("--gold"),
          soft: rgb("--gold-soft"),
          dim: rgb("--gold-dim"),
        },
        mute: {
          DEFAULT: rgb("--mute"),
          soft: rgb("--mute-soft"),
        },
        // text-white / bg-white: в тёмной — белый, в светлой — почти чёрный
        white: rgb("--fg"),
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "var(--shadow-card)",
        glow: "var(--shadow-glow)",
      },
      borderRadius: {
        xl2: "1.1rem",
      },
    },
  },
  plugins: [],
};
