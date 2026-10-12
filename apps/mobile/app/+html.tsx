import { ScrollViewStyleReset } from "expo-router/html";
import type { ReactNode } from "react";
import { darkTheme, lightTheme } from "../src/theme/palette";

// This file is web-only and used to configure the root HTML for every
// web page during static rendering.
// The contents of this function only run in Node.js environments and
// do not have access to the DOM or browser APIs.
export default function Root({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />

        {/*
          Disable body scrolling on web. This makes ScrollView components work closer to how they do on native.
          However, body scrolling is often nice to have for mobile web. If you want to enable it, remove this line.
        */}
        <ScrollViewStyleReset />

        {/* Using raw CSS styles as an escape-hatch to ensure the background color never flickers in dark-mode. */}
        <style dangerouslySetInnerHTML={{ __html: responsiveBackground }} />
        {/* Add any additional <head> elements that you want globally available on web... */}
      </head>
      <body>{children}</body>
    </html>
  );
}

// Der Seitengrund kommt direkt aus src/theme/palette.ts. Diese Datei laeuft
// nur zur Build-Zeit in Node.js und kann keine RN-Styles verwenden - die
// Werte werden deshalb in rohes CSS eingesetzt statt dupliziert, damit
// Hell-/Dunkelgrund nie gegen die Palette auseinanderlaufen.
const responsiveBackground = `
body {
  background-color: ${lightTheme.background};
}
@media (prefers-color-scheme: dark) {
  body {
    background-color: ${darkTheme.background};
  }
}`;
