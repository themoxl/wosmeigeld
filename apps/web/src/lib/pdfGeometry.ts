export type PdfBox = [number, number, number, number];

/** Unrotated PDF CropBox coordinates mapped onto the displayed page. */
export function displayedBox(box: PdfBox, rotation: number): PdfBox {
  const [x0, y0, x1, y1] = box;
  if (rotation === 90) return [1 - y1, x0, 1 - y0, x1];
  if (rotation === 180) return [1 - x1, 1 - y1, 1 - x0, 1 - y0];
  if (rotation === 270) return [y0, 1 - x1, y1, 1 - x0];
  return box;
}
