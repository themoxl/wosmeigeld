const { defineConfig } = require("eslint/config");
const expoConfig = require("eslint-config-expo/flat");

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ["dist/*", ".expo/*", "expo-env.d.ts"],
  },
  {
    // jest.setup.js liegt ausserhalb von app//src/ und wird von "expo lint"
    // nicht erfasst, von einem direkten "eslint ." dagegen schon - dort fehlt
    // sonst das globale jest-Objekt (no-undef).
    files: ["jest.setup.js", "**/*.test.{ts,tsx}", "**/__tests__/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { jest: "readonly" },
    },
  },
]);
