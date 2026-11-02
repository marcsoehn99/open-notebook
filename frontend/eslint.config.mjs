import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";
import reviewerPlugin from "../lint-rules/eslint/index.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    plugins: { reviewer: reviewerPlugin },
    rules: {
      "reviewer/quiz-poll-includes-queued": "error",
      "reviewer/no-set-query-data-outside-hooks": "error",
      "reviewer/no-ref-write-during-render": "error",
    },
  },
];

export default eslintConfig;
