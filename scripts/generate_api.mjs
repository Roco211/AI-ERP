import { writeFile } from "node:fs/promises";
import openapiTS, { astToString, COMMENT_HEADER, stringToAST } from "openapi-typescript";

// XLSX multipart fields are browser binary objects, not misleading text values.
const binary = stringToAST("type Binary = Blob;")[0].type;
const ast = await openapiTS(new URL("../docs/api/openapi.json", import.meta.url), {
  transform(schema) {
    if (schema.type === "string" && schema.format === "binary") return binary;
  },
});
await writeFile(
  new URL("../apps/web/generated/api/schema.d.ts", import.meta.url),
  COMMENT_HEADER + astToString(ast),
);
