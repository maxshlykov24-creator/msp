/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#000000",
          900: "#0C0D11",
          850: "#15171C",
          800: "#1A1D23",
          700: "#23272F",
          600: "#2E333D",
          500: "#3A404C",
        },
        // Монохромный акцент (фирстиль MANSBAND: чёрное/белое)
        gold: {
          DEFAULT: "#FFFFFF",
          soft: "#FFFFFF",
          dim: "#8A8C92",
        },
        mute: {
          DEFAULT: "#8A8C92",
          soft: "#C2C7D0",
        },
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
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 30px -12px rgba(0,0,0,0.7)",
        glow: "0 0 0 1px rgba(255,255,255,0.14), 0 10px 40px -12px rgba(255,255,255,0.10)",
      },
      borderRadius: {
        xl2: "1.1rem",
      },
    },
  },
  plugins: [],
};
