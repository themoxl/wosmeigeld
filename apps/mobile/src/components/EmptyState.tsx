import { StyleSheet, Text, View } from "react-native";
import { leading, space, typeScale, useTheme } from "../theme";

type Props = {
  title: string;
  description: string;
};

/** Ruhiger Leerzustand fuer Screens/Bereiche ohne echte Daten ("noch keine Daten"). */
export function EmptyState({ title, description }: Props) {
  const theme = useTheme();

  return (
    <View style={styles.container}>
      <Text style={[styles.title, { color: theme.textPrimary }]}>{title}</Text>
      <Text style={[styles.description, { color: theme.textSecondary }]}>{description}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.xxl,
    paddingVertical: space.xxxl,
    gap: space.sm,
  },
  title: {
    fontSize: typeScale.large,
    fontWeight: "700",
    textAlign: "center",
  },
  description: {
    fontSize: typeScale.base,
    textAlign: "center",
    lineHeight: leading(typeScale.base),
  },
});
