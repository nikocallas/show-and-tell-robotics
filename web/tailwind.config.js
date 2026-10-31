import defaultTheme from "tailwindcss/defaultTheme";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "InterVariable", ...defaultTheme.fontFamily.sans],
        mono: ["JetBrains Mono", "SFMono-Regular", ...defaultTheme.fontFamily.mono],
      },
      colors: {
        // The one accent color. Status colors (emerald/rose/amber) are semantic, not accent.
        accent: {
          50: "#f0f5ff",
          100: "#e0eaff",
          200: "#c2d5ff",
          300: "#93b4ff",
          400: "#5d8bff",
          500: "#3366ff",
          600: "#1f4fe0",
          700: "#1a3fb3",
          800: "#1b3a8f",
          900: "#1c3370",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06)",
        pop: "0 12px 32px -8px rgba(16, 24, 40, 0.18), 0 4px 8px -4px rgba(16, 24, 40, 0.08)",
      },
      keyframes: {
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(2px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pulse-soft": "pulse-soft 1.6s ease-in-out infinite",
        "fade-in": "fade-in 220ms ease-out",
      },
    },
  },
  plugins: [],
};
