/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["-apple-system", "BlinkMacSystemFont", "Trebuchet MS", "Roboto", "Ubuntu", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
      colors: {
        tv: { bg: "#131722", panel: "#1e222d", hover: "#2a2e39", line: "#2a2e39", text: "#d1d4dc", muted: "#787b86", dim: "#50535e", strong: "#f0f3fa" },
        up: "#089981", down: "#f23645", accent: "#2962ff", warn: "#f7a600",
      },
    },
  },
  plugins: [],
};
