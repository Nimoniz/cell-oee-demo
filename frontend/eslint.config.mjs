import js from "@eslint/js";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import globals from "globals";

// Deliberately lean: TypeScript strict mode (tsconfig) plus two project-specific rules that
// encode CLAUDE.md's "never convert simulated time to local time" and "colors come from
// lib/stateStyle.ts" rules as compile-time-checked lint errors, not just documentation.
export default [
  js.configs.recommended,
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: { project: "./tsconfig.json" },
      // Next.js code runs in both the browser and the Node server (Server Components, route
      // handlers): both global sets apply, file by file, rather than trying to split them.
      globals: { ...globals.browser, ...globals.node, ...globals.es2021, React: "readonly" },
    },
    plugins: { "@typescript-eslint": tseslint },
    rules: {
      ...tseslint.configs.recommended.rules,
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "CallExpression[callee.property.name=/^(toLocaleString|toLocaleDateString|toLocaleTimeString|getHours|getMinutes|getSeconds|getDay|getMonth|getFullYear|getDate)$/]",
          message: "Use lib/time.ts (UTC-only formatting) instead of local-time Date methods.",
        },
        {
          selector: "Literal[value=/^#[0-9a-fA-F]{3,8}$/]",
          message: "Colors come from lib/stateStyle.ts, never an inline hex literal.",
        },
      ],
    },
  },
  {
    // Node-run config/scripts, not part of the browser bundle.
    files: ["*.config.{ts,mjs}", "scripts/**/*.mjs"],
    languageOptions: { globals: globals.node },
    rules: { "no-restricted-syntax": "off" },
  },
  {
    files: ["src/lib/time.ts", "src/lib/stateStyle.ts", "src/lib/paretoPalette.ts", "src/lib/chartTheme.ts"],
    rules: { "no-restricted-syntax": "off" },
  },
  {
    files: ["**/*.test.ts", "**/*.test.tsx"],
    languageOptions: { globals: { ...globals.node } },
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
];
