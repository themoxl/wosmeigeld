// 4px-Basisraster. Geringe Radien, wenige echte Karten (Abschnitt 10.1).

export const spacing = {
  0: "0",
  1: "4px",
  2: "8px",
  3: "12px",
  4: "16px",
  5: "20px",
  6: "24px",
  8: "32px",
  10: "40px",
  12: "48px",
} as const;

export const radius = {
  none: "0",
  sm: "2px",
  md: "4px",
  lg: "6px",
} as const;

export const zIndex = {
  base: 0,
  stickyHeader: 10,
  dropdown: 20,
  overlay: 30,
  modal: 40,
  toast: 50,
} as const;
