import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import playwright from "eslint-plugin-playwright";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "coverage",
      "node_modules",
      "playwright-report",
      "test-results",
      "blob-report",
    ],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs.flat["recommended-latest"],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Playwright-Tests laufen node-seitig (Test-Runner), nicht im Browser,
    // und enthalten kein React - die erste Regel-Gruppe oben matcht "e2e/**"
    // trotzdem mit (Flat-Config-Bloecke sind additiv), daher hier gezielt
    // die React-spezifischen Regeln fuer diesen Ordner abschalten.
    files: ["e2e/**/*.ts"],
    ...playwright.configs["flat/recommended"],
    languageOptions: {
      ...playwright.configs["flat/recommended"].languageOptions,
      globals: { ...globals.node },
    },
    rules: {
      ...playwright.configs["flat/recommended"].rules,
      "react-hooks/rules-of-hooks": "off",
      "react-refresh/only-export-components": "off",
      "no-empty-pattern": "off",
    },
  },
);
