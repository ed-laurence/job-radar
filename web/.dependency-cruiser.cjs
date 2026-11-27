/**
 * Web layering (docs/standards/architecture.md, ARCH-40..43):
 *   app -> features -> components -> lib     (arrows point at what a layer may import)
 * Features never import each other; the generated client is reached only through lib/api.
 * @type {import('dependency-cruiser').IConfiguration}
 */
module.exports = {
  forbidden: [
    {
      name: "no-circular",
      severity: "error",
      from: {},
      to: { circular: true },
    },
    {
      name: "lib-is-the-bottom-layer",
      comment: "lib/ must not import components, features or app.",
      severity: "error",
      from: { path: "^src/lib/" },
      to: { path: "^src/(components|features|app)/" },
    },
    {
      name: "components-are-presentational",
      comment: "ARCH-40: components/ import only lib/ (and each other), never features or app.",
      severity: "error",
      from: { path: "^src/components/" },
      to: { path: "^src/(features|app)/" },
    },
    {
      name: "features-do-not-import-app",
      severity: "error",
      from: { path: "^src/features/" },
      to: { path: "^src/app/" },
    },
    {
      name: "no-cross-feature-imports",
      comment: "ARCH-41: features never import each other; move shared code to lib/.",
      severity: "error",
      from: { path: "^src/features/([^/]+)/" },
      to: { path: "^src/features/([^/]+)/", pathNot: "^src/features/$1/" },
    },
    {
      name: "generated-client-only-via-lib-api",
      comment: "ARCH-42: use src/lib/api, never the generated code directly.",
      severity: "error",
      from: { pathNot: "^src/lib/api/" },
      to: { path: "^src/lib/api/generated/" },
    },
    {
      name: "bff-is-server-only",
      comment: "The BFF proxy runs only in route handlers; client code must not bundle it.",
      severity: "error",
      from: { pathNot: "^src/(app/(api|hooks|healthz)/|lib/api/bff)" },
      to: { path: "^src/lib/api/bff" },
    },
    {
      name: "firebase-only-in-lib-auth",
      comment: "The Identity Platform SDK is wrapped by lib/auth.",
      severity: "error",
      from: { pathNot: "^src/lib/auth/" },
      to: { path: "node_modules/(firebase|@firebase)/" },
    },
    {
      name: "no-test-code-in-app",
      comment:
        "Test helpers (src/lib/testing, src/test, *.test.*) never reach a production bundle.",
      severity: "error",
      from: { pathNot: "\\.test\\.tsx?$|^src/test/|^src/lib/testing/|/testing\\.tsx?$" },
      to: {
        path: "\\.test\\.tsx?$|^src/test/|^src/lib/testing/|/testing\\.tsx?$|node_modules/(msw|vitest|@testing-library)/",
      },
    },
    {
      name: "not-to-unresolvable",
      severity: "error",
      from: {},
      to: { couldNotResolve: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    exclude: { path: "^src/lib/api/generated/schema\\.d\\.ts$" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default", "types"],
      extensions: [".ts", ".tsx", ".js", ".mjs", ".cjs", ".d.ts"],
    },
    reporterOptions: { text: { highlightFocused: true } },
  },
};
