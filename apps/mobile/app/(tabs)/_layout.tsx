import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Platform, StyleSheet, type ColorValue } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { space, typeScale, useTheme } from "../../src/theme";

// Bottom Tabs nach docs/mobile_redesign_showcase.html:
// Heute | Berichte | Budget | Vertraege | Mehr.
//
// Gegenueber der vorherigen Fassung nimmt "Berichte" den Platz von
// "Buchungen" ein. Begruendung aus dem Showcase (Abschnitt "UX-Entscheidungen"):
// die Buchungsliste ist im Alltag passiv und repetitiv - man schaut sie
// hoechstens an, wenn etwas unklar ist. Berichte beantworten dagegen die
// Frage, wegen der man die App ueberhaupt oeffnet ("wohin fliesst mein
// Geld?"). Die Buchungsliste bleibt vollstaendig erreichbar: ueber den
// Konten-Strip auf Heute, ueber "Alle Buchungen", ueber jede
// Kategorie-Aufschluesselung und ueber Mehr - sie steht nur nicht mehr auf
// dem knappsten Platz der App. Die Route /buchungen aendert sich dabei NICHT
// (href: null blendet nur den Tab-Eintrag aus), damit alle bestehenden
// Verweise und Benachrichtigungs-Ziele weiter funktionieren.
//
// Ohne native Kopfzeile: jeder Bildschirm bringt seine eigene, mitscrollende
// Ueberschrift (ScreenHeader) mit. Das spart auf einem 6-Zoll-Display die
// dauerhaft belegte Titelleiste und ist die Gestaltung des Showcase.
type TabIconName = React.ComponentProps<typeof Ionicons>["name"];

function TabIcon({
  name,
  nameActive,
  color,
  focused,
}: {
  name: TabIconName;
  nameActive: TabIconName;
  color: ColorValue;
  focused: boolean;
}) {
  return <Ionicons name={focused ? nameActive : name} color={color} size={24} />;
}

export default function TabLayout() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();

  const barHeight = 60 + insets.bottom;

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: theme.chrome,
          borderTopColor: theme.border,
          borderTopWidth: StyleSheet.hairlineWidth,
          height: barHeight,
          paddingTop: 6,
          paddingBottom: insets.bottom > 0 ? insets.bottom : 8,
          // Android zieht ohne diese Angabe eine graue Materialschicht ueber
          // die Farbe, die dann weder zum dunklen noch zum hellen Theme passt.
          elevation: 0,
        },
        tabBarLabelStyle: {
          fontSize: typeScale.micro,
          fontWeight: "700",
          marginTop: space.hair,
        },
        tabBarActiveTintColor: theme.positive,
        tabBarInactiveTintColor: theme.textSecondary,
        tabBarHideOnKeyboard: Platform.OS === "android",
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Heute",
          tabBarIcon: ({ color, focused }) => (
            <TabIcon name="home-outline" nameActive="home" color={color} focused={focused} />
          ),
        }}
      />
      <Tabs.Screen
        name="berichte"
        options={{
          title: "Berichte",
          tabBarIcon: ({ color, focused }) => (
            <TabIcon
              name="stats-chart-outline"
              nameActive="stats-chart"
              color={color}
              focused={focused}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="budget"
        options={{
          title: "Budget",
          tabBarIcon: ({ color, focused }) => (
            <TabIcon name="disc-outline" nameActive="disc" color={color} focused={focused} />
          ),
        }}
      />
      <Tabs.Screen
        name="vertraege"
        options={{
          title: "Verträge",
          tabBarIcon: ({ color, focused }) => (
            <TabIcon
              name="document-text-outline"
              nameActive="document-text"
              color={color}
              focused={focused}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="mehr"
        options={{
          title: "Mehr",
          tabBarIcon: ({ color, focused }) => (
            <TabIcon
              name="ellipsis-horizontal-circle-outline"
              nameActive="ellipsis-horizontal-circle"
              color={color}
              focused={focused}
            />
          ),
        }}
      />
      {/* Weiterhin erreichbar unter /buchungen, aber nicht mehr in der
          Tableiste - siehe Begruendung im Dateikopf. */}
      <Tabs.Screen name="buchungen" options={{ href: null, title: "Buchungen" }} />
    </Tabs>
  );
}
