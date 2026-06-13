import deckyPlugin from "@decky/rollup";

// Standard Decky build: bundles src/index.tsx → dist/index.js, externalizing the
// SteamOS-provided React/@decky runtime. mpd-core is bundled in (pure TS).
export default deckyPlugin();
