import { Stack } from "expo-router";
import { useTheme } from "../../src/theme";

// Eigener Stack fuer die Detailseiten unter "Mehr", damit sie mit
// Kopfzeile/Zurueck-Button ausserhalb der Bottom Tabs geoeffnet werden.
export default function MehrLayout() {
  const theme = useTheme();

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.surface },
        headerTintColor: theme.textPrimary,
        headerShadowVisible: false,
        contentStyle: { backgroundColor: theme.background },
      }}
    />
  );
}
