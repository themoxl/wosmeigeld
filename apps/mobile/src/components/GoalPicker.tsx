import { StyleSheet } from "react-native";
import type { SavingsGoal } from "../api/types";
import { space } from "../theme";
import { Badge, Card, DataRow } from "./ui";

type Props = {
  goals: SavingsGoal[];
  value: string | null;
  onChange: (goalId: string | null) => void;
};

/**
 * Flache Sparziel-Auswahl fuer den Split-Editor (Architekturplan 6.7:
 * goal_id sitzt am Split, analog zu category_id) - Struktur-Pendant zu
 * CategoryPicker, aber ohne Gruppenkoepfe.
 *
 * Die getroffene Wahl traegt eine ausgeschriebene Pille "Gewählt" statt nur
 * einen farbigen Rahmen: in einer Liste gleich aussehender Zeilen ist die
 * Farbe allein kein verlaessliches Merkmal.
 */
export function GoalPicker({ goals, value, onChange }: Props) {
  const rows: { id: string | null; label: string }[] = [
    { id: null, label: "Kein Sparziel" },
    ...goals.map((goal) => ({ id: goal.id, label: goal.name })),
  ];

  return (
    <Card padding={space.md} style={styles.card}>
      {rows.map((row, index) => {
        const selected = row.id === value;
        return (
          <DataRow
            key={row.id ?? "none"}
            title={row.label}
            avatarLabel={row.label}
            avatarIcon={row.id === null ? "remove-circle-outline" : "flag-outline"}
            titleBadge={selected ? <Badge label="Gewählt" tone="positive" size="small" /> : null}
            onPress={() => onChange(row.id)}
            accessibilityLabel={selected ? `${row.label}, gewählt` : row.label}
            divider={index < rows.length - 1}
          />
        );
      })}
    </Card>
  );
}

const styles = StyleSheet.create({
  // Die Zeilen bringen ihren eigenen senkrechten Abstand und ihre Trennlinie
  // mit; der Kartenabstand wuerde sie zusaetzlich auseinanderziehen.
  card: { gap: 0 },
});
