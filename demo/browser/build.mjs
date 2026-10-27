// Bundles + minifies the demo with a source map: `node build.mjs`  ->  dist/app.min.js(.map)
import { build } from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";

mkdirSync("dist", { recursive: true });
await build({
  entryPoints: ["app.ts"],
  bundle: true,
  minify: true,
  sourcemap: true,
  sourcesContent: true,
  format: "iife",
  target: "es2020",
  outfile: "dist/app.min.js",
});
copyFileSync("index.html", "dist/index.html");
console.log("built dist/app.min.js + app.min.js.map");
