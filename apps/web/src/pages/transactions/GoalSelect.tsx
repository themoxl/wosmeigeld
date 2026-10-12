import { forwardRef, type SelectHTMLAttributes } from "react";
import type { SavingsGoal } from "../../lib/types";

// Sparziel-Dropdown fuer den Split-Editor (Abschnitt 6.7: goal_id sitzt am
// Split, analog zu category_id). value === "" bedeutet "kein Sparziel"
// (goal_id: null).
interface GoalSelectProps extends Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  "value" | "onChange"
> {
  goals: SavingsGoal[] | undefined;
  value: string;
  onChange: (value: string) => void;
  noneLabel?: string;
}

export const GoalSelect = forwardRef<HTMLSelectElement, GoalSelectProps>(function GoalSelect(
  { goals, value, onChange, noneLabel = "Kein Sparziel", className, id, ...rest },
  ref,
) {
  // Archivierte Ziele duerfen nicht mehr neu zugeordnet werden. Eine bereits
  // bestehende Zuordnung bleibt aber als aktueller Wert sichtbar und kann so
  // bewusst beibehalten oder geloest werden.
  const selectableGoals = goals?.filter((goal) => goal.status !== "cancelled" || goal.id === value);

  return (
    <select
      ref={ref}
      id={id}
      className={className ?? "form-input"}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      {...rest}
    >
      <option value="">{noneLabel}</option>
      {selectableGoals?.map((goal) => (
        <option key={goal.id} value={goal.id}>
          {goal.name}
          {goal.status === "cancelled" ? " (Archiviert)" : ""}
        </option>
      ))}
    </select>
  );
});
