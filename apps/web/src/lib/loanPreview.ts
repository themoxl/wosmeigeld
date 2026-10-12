// Ueberschlagsrechnung fuer die Sofortvorschau beim Anlegen einer Foerderung.
//
// Bewusst eine eigene, kleine Rechnung im Browser statt eines Serveraufrufs:
// die Vorschau soll sich beim Tippen aendern, und fuer "was kostet mich das im
// Monat" braucht es keine Abrufplaene, Gebuehren oder Sondertilgungen. Der
// verbindliche Verlauf kommt weiterhin aus
// GET /v1/debt-plans/{id}/schedule, sobald der Plan gespeichert ist - deshalb
// heisst das hier Ueberschlag und nicht Tilgungsplan.

export interface LoanPreviewInput {
  principalMinor: number;
  /** Sollzins in Basispunkten pro Jahr, z. B. 312 = 3,12 %. */
  nominalRateBpsAnnual: number;
  /** Anfaengliche Tilgung in Basispunkten pro Jahr, z. B. 200 = 2 %. */
  initialRepaymentRateBps: number;
  /** Monate, in denen nur Zinsen gezahlt werden. */
  repaymentFreeMonths: number;
  /** Sollzinsbindung in Monaten; danach gilt `followUpRateBpsAnnual`. */
  fixedRateMonths: number;
  /** Angenommener Anschlusszins nach der Bindung. */
  followUpRateBpsAnnual: number;
  /** Rechenhorizont; begrenzt die Simulation. */
  horizonMonths?: number;
  /** Gesamte Vertragslaufzeit; schaltet auf Volltilgung innerhalb dieser Laufzeit. */
  targetTermMonths?: number;
  repaymentType?: "annuity" | "bullet";
}

export interface LoanPreview {
  /** Rate waehrend der tilgungsfreien Zeit (nur Zinsen). 0, wenn es keine gibt. */
  paymentDuringRepaymentFreeMinor: number;
  /** Rate nach Beginn der Tilgung. */
  paymentMinor: number;
  /** Dieselbe Rate auf ein Jahr gerechnet. */
  yearlyPaymentMinor: number;
  /** Gezahlte Zinsen bis zum Ende der Sollzinsbindung. */
  interestUntilFixedEndMinor: number;
  /** Restschuld zum Ende der Sollzinsbindung. */
  balanceAtFixedEndMinor: number;
  /** Monat der Volltilgung, oder null wenn innerhalb des Horizonts nicht erreicht. */
  payoffMonth: number | null;
  /** Zinsen ueber die gesamte simulierte Laufzeit. */
  totalInterestMinor: number;
}

function round(value: number): number {
  return Math.round(value);
}

export function calculateLoanPreview(input: LoanPreviewInput): LoanPreview {
  const {
    principalMinor,
    nominalRateBpsAnnual,
    initialRepaymentRateBps,
    repaymentFreeMonths,
    fixedRateMonths,
    followUpRateBpsAnnual,
    horizonMonths = 480,
    targetTermMonths,
    repaymentType = "annuity",
  } = input;

  const monthlyRate = (month: number) =>
    (month < fixedRateMonths ? nominalRateBpsAnnual : followUpRateBpsAnnual) / 10_000 / 12;
  const balanceAfterTerm = (payment: number) => {
    let remaining = principalMinor;
    for (let month = repaymentFreeMonths; month < (targetTermMonths ?? 0); month += 1) {
      remaining += remaining * monthlyRate(month) - payment;
    }
    return remaining;
  };
  let annuityMinor =
    (principalMinor * (nominalRateBpsAnnual + initialRepaymentRateBps)) / 10_000 / 12;
  if (targetTermMonths && repaymentType === "annuity") {
    let low = 0;
    let high = principalMinor;
    for (let step = 0; step < 80; step += 1) {
      const middle = (low + high) / 2;
      if (balanceAfterTerm(middle) <= 0) high = middle;
      else low = middle;
    }
    annuityMinor = high;
  } else if (repaymentType === "bullet") {
    annuityMinor = (principalMinor * nominalRateBpsAnnual) / 10_000 / 12;
  }
  const interestOnlyMinor = (principalMinor * nominalRateBpsAnnual) / 10_000 / 12;

  let balance = principalMinor;
  let interestUntilFixedEnd = 0;
  let totalInterest = 0;
  let balanceAtFixedEnd = principalMinor;
  let payoffMonth: number | null = null;

  for (let month = 0; month < horizonMonths; month += 1) {
    // Nach dem Ende der Bindung gilt der Anschlusszins. Die Rate bleibt
    // unveraendert - das entspricht der ueblichen Prolongation, bei der die
    // Bank die Annuitaet fortschreibt und sich nur die Aufteilung verschiebt.
    const rateBps = month < fixedRateMonths ? nominalRateBpsAnnual : followUpRateBpsAnnual;
    const interest = (balance * rateBps) / 10_000 / 12;
    const isBulletFinalMonth = repaymentType === "bullet" && month === (targetTermMonths ?? 0) - 1;
    const scheduled = isBulletFinalMonth
      ? balance + interest
      : month < repaymentFreeMonths || repaymentType === "bullet"
        ? interest
        : annuityMinor;
    const payment = Math.min(scheduled, balance + interest);

    totalInterest += interest;
    if (month < fixedRateMonths) {
      interestUntilFixedEnd += interest;
    }
    balance = balance + interest - payment;
    if (month === fixedRateMonths - 1) {
      balanceAtFixedEnd = balance;
    }
    if (balance <= 0.5) {
      payoffMonth = month + 1;
      if (month < fixedRateMonths) {
        balanceAtFixedEnd = 0;
      }
      break;
    }
  }

  return {
    paymentDuringRepaymentFreeMinor: repaymentFreeMonths > 0 ? round(interestOnlyMinor) : 0,
    paymentMinor: round(annuityMinor),
    yearlyPaymentMinor: round(annuityMinor * 12),
    interestUntilFixedEndMinor: round(interestUntilFixedEnd),
    balanceAtFixedEndMinor: round(balanceAtFixedEnd),
    payoffMonth,
    totalInterestMinor: round(totalInterest),
  };
}
