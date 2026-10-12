import type { ReactTestInstance } from "react-test-renderer";

/**
 * @types/react-test-renderer tippt `ReactTestInstance.type` als React-DOM-
 * `ElementType` (Tag-Namen wie "div"/"a"), nicht als React-Native-
 * Hostkomponenten ("View", "Text"). Ein direkter Literalvergleich wie
 * `node.type === "View"` schlaegt deshalb im Compiler mit "no overlap" fehl,
 * obwohl er zur Laufzeit korrekt ist (React Native liefert Host-Typen als
 * einfache Strings). Dieser Helfer vergleicht ueber einen nicht-literalen
 * string-Parameter und vermeidet damit den Compilerfehler.
 */
export function isHostElementOfType(node: ReactTestInstance, tag: string): boolean {
  return typeof node.type === "string" && node.type === tag;
}
