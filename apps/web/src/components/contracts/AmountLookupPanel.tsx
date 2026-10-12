import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiErrorDetail, listTransactions } from "../../lib/api";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import { useContractContext } from "../../hooks/useContracts";
import type { Transaction } from "../../lib/types";

/**
 * Nachschlagen statt Zuordnen.
 *
 * Die formale Buchungszuordnung (ContractAssignTransactionsPanel, Register
 * "Betragsverlauf") haengt zwingend an einer Vertrags-ID - fuer einen Vertrag,
 * den es noch nicht gibt, laesst sich also noch nichts zuordnen. Dieses Register
 * beantwortet stattdessen nur die Frage, die beim Anlegen tatsaechlich offen
 * ist: "was habe ich diesem Anbieter zuletzt wirklich gezahlt". Ein Treffer
 * uebernimmt ausschliesslich den Betrag ins Formular; die eigentliche
 * Zuordnung folgt danach automatisch, sobald der Vertrag gespeichert ist
 * (siehe ContractFormModal.onCreated in ContractsPage.tsx).
 *
 * Regex ist ein eigener Umschalter statt einer Heuristik auf den Suchtext -
 * dieselbe Begruendung wie beim search_regex-Parameter der API
 * (services/api/app/api/v1/routes/transactions.py): eine erkannte-oder-nicht-
 * Grenze anhand von Sonderzeichen ist fuer den Nutzer nicht vorhersagbar.
 */

interface AmountLookupPanelProps {
  onApplyAmount: (amountMinor: number) => void;
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function lookupErrorMessage(error: unknown): string {
  const detail = apiErrorDetail(error);
  if (detail === "invalid_regex_pattern") return "Ungültiger regulärer Ausdruck.";
  if (detail === "regex_pattern_too_expensive") {
    return "Dieses Muster ist zu aufwendig — bitte eingrenzen.";
  }
  return errorText(error, "Die Suche ist fehlgeschlagen.");
}

export function AmountLookupPanel({ onApplyAmount }: AmountLookupPanelProps) {
  const { token, householdId, enabled } = useContractContext();
  const [draftText, setDraftText] = useState("");
  const [useRegex, setUseRegex] = useState(false);
  const [query, setQuery] = useState<{ text: string; regex: boolean } | null>(null);

  const result = useQuery<Transaction[]>({
    queryKey: ["contract-amount-lookup", householdId, query?.text, query?.regex],
    queryFn: () =>
      listTransactions(token, householdId, {
        limit: 25,
        search: query && !query.regex ? query.text : undefined,
        searchRegex: query && query.regex ? query.text : undefined,
      }),
    enabled: enabled && query !== null,
  });

  const submit = () => {
    const trimmed = draftText.trim();
    if (!trimmed) return;
    setQuery({ text: trimmed, regex: useRegex });
  };

  const items = result.data ?? [];

  return (
    <section className="operation-preview">
      <h3 className="card-heading">In den eigenen Buchungen nachschlagen</h3>
      <p className="text-sm">
        Kennt der Anbieterkatalog oben den Anbieter nicht, hilft oft der Blick in bereits
        importierte Kontobuchungen. Ein Treffer übernimmt nur den Betrag — die Buchung selbst lässt
        sich dem Vertrag nach dem Speichern im Register „Betragsverlauf“ zuordnen.
      </p>
      {/* Kein eigenes <form>: dieses Register steckt bereits im <form> des
          Anlege-Dialogs, und ein verschachteltes <form> ist ungueltiges HTML
          (und liesse den Eintrag hier faelschlich den ganzen Vertrag
          speichern). Enter im Suchfeld und der Knopf loesen die Suche deshalb
          direkt aus, beide mit explizitem type="button"/preventDefault. */}
      <div className="contract-form">
        <div className="form-grid-2">
          <div className="form-field">
            <label className="form-label" htmlFor="amount-lookup-text">
              Text in Empfänger oder Zweck
            </label>
            <input
              id="amount-lookup-text"
              type="search"
              className="form-input"
              value={draftText}
              onChange={(event) => setDraftText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                submit();
              }}
              placeholder={useRegex ? "z. B. mustera.?ssekuranz" : "z. B. Musterassekuranz"}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="amount-lookup-regex">
              <input
                id="amount-lookup-regex"
                type="checkbox"
                checked={useRegex}
                onChange={(event) => setUseRegex(event.target.checked)}
              />{" "}
              Regulärer Ausdruck (Regex)
            </label>
          </div>
        </div>
        <div className="modal-actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={!draftText.trim()}
            onClick={submit}
          >
            Suchen
          </button>
        </div>
      </div>

      {result.isFetching && <p className="placeholder-note">Buchungen werden durchsucht…</p>}
      {result.isError && (
        <p role="alert" className="status-error">
          {lookupErrorMessage(result.error)}
        </p>
      )}
      {query && !result.isFetching && !result.isError && items.length === 0 && (
        <p className="placeholder-note">Keine Buchung passt zu dieser Suche.</p>
      )}

      {items.length > 0 && (
        <ul className="contract-occurrences">
          {items.map((transaction) => (
            <li key={transaction.id}>
              <span className="contract-occurrences__date">{transaction.booked_date}</span>
              <span className="txn-payee">{transaction.payee_label ?? "Ohne Empfänger"}</span>
              <span className="contract-occurrences__amount">
                {formatMinorUnitsAsEuro(transaction.amount_minor)}
              </span>
              <button
                type="button"
                className="btn-inline"
                onClick={() => onApplyAmount(Math.abs(transaction.amount_minor))}
              >
                Betrag übernehmen
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
