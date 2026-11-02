import tailwind from "bun-plugin-tailwind";
import { rm } from "node:fs/promises";

await rm("./dist", { recursive: true, force: true });
const result = await Bun.build({
  entrypoints: ["./index.html"],
  outdir: "./dist",
  target: "browser",
  splitting: true,
  publicPath: "/assets/",
  minify: true,
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  plugins: [tailwind],
});
if (!result.success) throw new AggregateError(result.logs, "前端构建失败");
for (const output of result.outputs)
  console.log(
    `${output.path.split("/").at(-1)} ${(output.size / 1024).toFixed(1)} KiB`,
  );
