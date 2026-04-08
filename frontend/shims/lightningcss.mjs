import { createRequire } from "node:module";
import browserslistToTargets from "../node_modules/lightningcss/node/browserslistToTargets.js";
import composeVisitors from "../node_modules/lightningcss/node/composeVisitors.js";
import { Features } from "../node_modules/lightningcss/node/flags.js";

const require = createRequire(import.meta.url);
const native = require("lightningcss-win32-x64-msvc");

export const transform = native.transform;
export const transformStyleAttribute = native.transformStyleAttribute;
export const bundle = native.bundle;
export const bundleAsync = native.bundleAsync;
export { browserslistToTargets, composeVisitors, Features };

const lightningcss = {
  transform,
  transformStyleAttribute,
  bundle,
  bundleAsync,
  browserslistToTargets,
  composeVisitors,
  Features,
};

export default lightningcss;
