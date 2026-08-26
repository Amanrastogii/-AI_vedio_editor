import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d14",
        surface: "#13161f",
        surface2: "#1b1f2e",
        border: "#272c3f",
        accent: "#7c83f5",
        accent2: "#56cfb2",
        accent3: "#f5a623",
        accent4: "#e96b8c",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
