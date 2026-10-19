/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
      colors: {
        ink: { 950: "#0b0f17", 900: "#111723", 850: "#151c2b", 800: "#1b2334", 700: "#273149", 600: "#3a4663", 500: "#5b6888", 400: "#8792ad", 300: "#b3bcd1", 200: "#d7dceb", 100: "#eef1f8" },
        long: "#22c55e", short: "#ef4444", risk: "#ef4444", reward: "#22c55e", accent: "#60a5fa", warn: "#f59e0b",
      },
    },
  },
  plugins: [],
};
