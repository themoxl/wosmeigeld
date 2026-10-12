import { StyleSheet, View } from "react-native";

type Props = {
  /** Farbe aus useTheme() - hier kommt bewusst nie ein Literal an. */
  color: string;
  size?: number;
};

/**
 * Farbpunkt als schneller Anker in einer Zeile (Systemstatus, Kontostrip).
 *
 * Ohne Beschriftung und fuer Vorlesehilfen ausgeblendet: der Punkt darf die
 * Aussage nie allein tragen, deshalb steht sie in der Zeile daneben immer
 * ausgeschrieben (siehe ServiceHealthRow). Waere er vorlesbar, kaeme dort nur
 * ein sinnloses "Bild" an.
 */
export function StatusDot({ color, size = 10 }: Props) {
  return (
    <View
      accessibilityElementsHidden
      importantForAccessibility="no"
      style={[
        styles.dot,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: color,
        },
      ]}
    />
  );
}

const styles = StyleSheet.create({
  dot: {
    // In einer Zeile mit langem Text wuerde der Punkt sonst zusammengedrueckt
    // und aus dem Kreis eine Ellipse.
    flexShrink: 0,
  },
});
