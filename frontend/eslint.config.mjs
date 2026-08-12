import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import reactHooks from "eslint-plugin-react-hooks";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Gate calibration: these two families are large-volume, low-risk debt that we
    // clean up incrementally. Keep them visible as warnings instead of blocking CI.
    plugins: { "react-hooks": reactHooks },
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      // Honor the `_`-prefix convention for intentionally-unused vars/args/catch bindings.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      // React Compiler (react-hooks v6+) diagnostics — surfaced as warnings for now.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/purity": "warn",
    },
  },
  {
    // CommonJS tooling/e2e helpers legitimately use require(); no ESM here.
    files: ["**/*.cjs", "scripts/**/*.js"],
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
  {
    files: ["e2e/**", "**/*.spec.ts", "**/*.spec.tsx"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "dist/**",
    "release/**",
    "electron/**",
    "node_modules/**",
    "next-env.d.ts",
    "nul",
    "**/*.test.mjs",
  ]),
]);

export default eslintConfig;
