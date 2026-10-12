/** Farbpunkt vor einem Kategorienamen - dasselbe Muster wie CategoryPicker
 *  und CategoriesPage (`category-picker-dot`). Farbe ist nie die einzige
 *  Kennzeichnung, deshalb steht daneben immer der volle Text.
 *
 *  Der Randabstand steht hier und nicht an der geteilten CSS-Klasse: dort,
 *  wo `category-picker-dot` schon in einem Flex-Container mit `gap` sitzt
 *  (CategoryPicker, CategoriesPage), verdoppelte ein Rand am Punkt selbst
 *  den Abstand. Diese Komponente steht dagegen meist direkt in einer
 *  Tabellenzelle ohne `gap` und braucht den Abstand deshalb selbst. */
export function CategoryDot({ color }: { color: string | null }) {
  if (!color) return null;
  return (
    <span
      className="category-picker-dot"
      style={{ backgroundColor: color, marginRight: "6px" }}
      aria-hidden="true"
    />
  );
}
