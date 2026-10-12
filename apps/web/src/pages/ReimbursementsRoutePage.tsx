import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthProvider";
import { DocumentInspector } from "../components/reimbursements/DocumentInspector";
import { useHousehold } from "../household/HouseholdProvider";
import { useReimbursementDocumentFile } from "../hooks/useReimbursementDocumentFile";
import {
  createReimbursementPerson,
  confirmReimbursementExtraction,
  deleteReimbursementDocument,
  getReimbursementCaseCandidates,
  getReimbursementExtraction,
  getReimbursementSummary,
  listReimbursementCases,
  listReimbursementDocuments,
  linkReimbursementDocumentCase,
  listReimbursementPeople,
  reimbursementDocumentsQueryKey,
  reprocessAllReimbursementDocuments,
  reprocessReimbursementDocument,
  uploadReimbursementDocument,
  type ExtractedDocumentData,
  type DocumentLinkOptions,
  type ReimbursementCaseCandidate,
  fetchReimbursementDocumentPage,
  listReimbursementPayers,
  getReimbursementCasePreview,
  unlinkReimbursementDocumentCase,
  reimbursementErrorMessage,
} from "../lib/reimbursementsApi";
import { OpenMarksPanel } from "../components/reimbursements/OpenMarksPanel";
import {
  convertMarkToCase,
  createAllocation,
  deleteAllocation,
  listCaseAllocations,
  listReimbursementMarks,
  listTransactionCandidates,
  reimbursementAllocationsQueryKey,
  reimbursementCandidatesQueryKey,
  reimbursementMarksQueryKey,
  removeReimbursementMark,
} from "../lib/reimbursementsApi";
import { ReimbursementsPage } from "./ReimbursementsPage";

/** Status ohne fertige Auswertung. Ein Abruf liefe hier zwangslaeufig ins 404. */
const WITHOUT_EXTRACTION = new Set(["queued", "extracting"]);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

export function ReimbursementsRoutePage() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const year = new Date().getFullYear();
  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);
  const [personName, setPersonName] = useState("");
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedBankCaseId, setSelectedBankCaseId] = useState<string | null>(null);
  const [selectedItemIndex, setSelectedItemIndex] = useState<number | null>(null);

  const marksQuery = useQuery({
    queryKey: reimbursementMarksQueryKey(householdId, true),
    queryFn: () => listReimbursementMarks(token, householdId, { onlyOpen: true }),
    enabled,
  });

  const convertMark = useMutation({
    mutationFn: (vars: { transactionId: string; insuredPersonId: string }) =>
      convertMarkToCase(token, householdId, vars.transactionId, vars.insuredPersonId),
    onSuccess: () => {
      // Der neue Vorgang taucht in der Vorgangsliste auf und die Marke faellt
      // aus der offenen - beide Ansichten sind sonst sofort veraltet.
      void queryClient.invalidateQueries({
        queryKey: reimbursementMarksQueryKey(householdId, true),
      });
      void queryClient.invalidateQueries({ queryKey: ["reimbursement-cases", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["reimbursement-summary", householdId] });
    },
  });

  const removeMark = useMutation({
    mutationFn: (transactionId: string) =>
      removeReimbursementMark(token, householdId, transactionId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: reimbursementMarksQueryKey(householdId, true),
      }),
  });

  const peopleQuery = useQuery({
    queryKey: ["reimbursement-people", selectedHouseholdId],
    queryFn: () => listReimbursementPeople(token, householdId),
    enabled,
  });
  const casesQuery = useQuery({
    queryKey: ["reimbursement-cases", selectedHouseholdId],
    queryFn: () => listReimbursementCases(token, householdId),
    enabled,
  });
  const summaryQuery = useQuery({
    queryKey: ["reimbursement-summary", selectedHouseholdId, year],
    queryFn: () => getReimbursementSummary(token, householdId, year),
    enabled,
  });
  const documentsQuery = useQuery({
    queryKey: reimbursementDocumentsQueryKey(householdId),
    queryFn: () => listReimbursementDocuments(token, householdId),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.some(
        (document) =>
          document.processing_status === "queued" || document.processing_status === "extracting",
      )
        ? 3_000
        : false,
  });
  const payersQuery = useQuery({
    queryKey: ["reimbursement-payers", householdId],
    queryFn: () => listReimbursementPayers(token, householdId),
    enabled,
  });

  const documents = documentsQuery.data ?? [];
  // Nach dem Loeschen verschwindet das Dokument aus der Liste; die Auswahl
  // laeuft damit von selbst ins Leere statt einen toten Verweis zu halten.
  const selectedDocument = documents.find((item) => item.id === selectedDocumentId) ?? null;
  const extractionReady =
    selectedDocument !== null && !WITHOUT_EXTRACTION.has(selectedDocument.processing_status);

  const extractionQuery = useQuery({
    queryKey: ["reimbursement-extraction", selectedHouseholdId, selectedDocument?.id],
    queryFn: () => getReimbursementExtraction(token, householdId, selectedDocument?.id as string),
    enabled: enabled && extractionReady,
  });
  const effectiveData =
    extractionQuery.data?.confirmed_data ??
    extractionQuery.data?.previous_confirmed_data ??
    extractionQuery.data?.structured_data;
  const itemIndex = effectiveData?.items?.length ? (selectedItemIndex ?? 0) : null;
  // Kandidaten erst, wenn es etwas zuzuordnen gibt: ohne Auswertung und
  // bei bereits zugeordnetem Dokument waere der Abruf ohne Zweck.
  const candidatesQuery = useQuery({
    queryKey: [
      "reimbursement-case-candidates",
      selectedHouseholdId,
      selectedDocument?.id,
      itemIndex,
      extractionQuery.data?.id,
      extractionQuery.data?.confirmed_at,
    ],
    queryFn: () =>
      getReimbursementCaseCandidates(token, householdId, selectedDocument?.id as string, itemIndex),
    enabled:
      enabled &&
      extractionReady &&
      Boolean(extractionQuery.data) &&
      (selectedDocument?.case_id == null || itemIndex !== null),
  });
  const documentFile = useReimbursementDocumentFile(
    accessToken ?? null,
    selectedHouseholdId ?? null,
    selectedDocument?.id ?? null,
  );
  const loadDocumentPage = useCallback(
    (page: number) =>
      fetchReimbursementDocumentPage(token, householdId, selectedDocument?.id as string, page),
    [token, householdId, selectedDocument?.id],
  );
  const loadCasePreview = useCallback(
    (caseId: string, options: DocumentLinkOptions) =>
      getReimbursementCasePreview(
        token,
        householdId,
        selectedDocument?.id as string,
        caseId,
        options,
      ),
    [token, householdId, selectedDocument?.id],
  );

  const invalidateAfterChange = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: reimbursementDocumentsQueryKey(householdId) }),
      queryClient.invalidateQueries({ queryKey: ["reimbursement-cases", selectedHouseholdId] }),
      queryClient.invalidateQueries({ queryKey: ["reimbursement-summary", selectedHouseholdId] }),
      queryClient.invalidateQueries({
        queryKey: ["reimbursement-case-candidates", selectedHouseholdId],
      }),
      queryClient.invalidateQueries({ queryKey: ["reimbursement-payers", householdId] }),
    ]);

  // --- Bankabgleich -------------------------------------------------------
  // Kandidaten und Zuordnungen haengen am ausgewaehlten Vorgang und werden
  // erst geladen, wenn einer offen ist. Beide Abrufe gehen ueber denselben
  // Schluessel wie die Invalidierung unten - sonst zeigte die Liste nach dem
  // Zuordnen weiterhin die alte Buchung als frei an.
  const bankCandidatesQuery = useQuery({
    queryKey: reimbursementCandidatesQueryKey(householdId, selectedBankCaseId),
    queryFn: () => listTransactionCandidates(token, householdId, selectedBankCaseId as string),
    enabled: enabled && Boolean(selectedBankCaseId),
  });

  const bankAllocationsQuery = useQuery({
    queryKey: reimbursementAllocationsQueryKey(householdId, selectedBankCaseId),
    queryFn: () => listCaseAllocations(token, householdId, selectedBankCaseId as string),
    enabled: enabled && Boolean(selectedBankCaseId),
  });

  /** Vier Invalidierungen, nicht zwei. Eine Zuordnung aendert den Vorgang
   *  (Status und erstatteter Betrag), die Jahreszusammenfassung, die freien
   *  Restbetraege der Kandidaten und die Zuordnungsliste selbst. Faellt eine
   *  weg, widersprechen sich zwei Bereiche derselben Seite. */
  const invalidateAfterAllocation = (caseId: string) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["reimbursement-cases", selectedHouseholdId] }),
      queryClient.invalidateQueries({ queryKey: ["reimbursement-summary", selectedHouseholdId] }),
      queryClient.invalidateQueries({
        queryKey: reimbursementCandidatesQueryKey(householdId, caseId),
      }),
      queryClient.invalidateQueries({
        queryKey: reimbursementAllocationsQueryKey(householdId, caseId),
      }),
    ]);

  const allocate = useMutation({
    mutationFn: (vars: { caseId: string; transactionId: string; amountMinor: number }) =>
      createAllocation(token, householdId, vars.caseId, vars.transactionId, vars.amountMinor),
    onSuccess: (_result, vars) => invalidateAfterAllocation(vars.caseId),
  });

  const removeAllocation = useMutation({
    mutationFn: (vars: { caseId: string; allocationId: string }) =>
      deleteAllocation(token, householdId, vars.caseId, vars.allocationId),
    onSuccess: (_result, vars) => invalidateAfterAllocation(vars.caseId),
  });

  const createPerson = useMutation({
    mutationFn: (displayName: string) => createReimbursementPerson(token, householdId, displayName),
    onSuccess: () => {
      setPersonName("");
      void queryClient.invalidateQueries({
        queryKey: ["reimbursement-people", selectedHouseholdId],
      });
    },
  });
  const confirmExtraction = useMutation({
    mutationFn: (data: ExtractedDocumentData) => {
      const version = casesQuery.data?.find(
        (item) => item.id === selectedDocument?.case_id,
      )?.version;
      return version == null
        ? confirmReimbursementExtraction(token, householdId, selectedDocument?.id as string, data)
        : confirmReimbursementExtraction(
            token,
            householdId,
            selectedDocument?.id as string,
            data,
            version,
          );
    },
    onSuccess: async () => {
      await Promise.all([
        invalidateAfterChange(),
        queryClient.invalidateQueries({
          queryKey: ["reimbursement-extraction", selectedHouseholdId, selectedDocument?.id],
        }),
      ]);
    },
  });
  const removeDocument = useMutation({
    mutationFn: (documentId: string) => deleteReimbursementDocument(token, householdId, documentId),
    onSuccess: async () => {
      setSelectedDocumentId(null);
      await invalidateAfterChange();
    },
  });
  const linkCase = useMutation({
    mutationFn: (vars: { caseId: string; options?: DocumentLinkOptions }) =>
      linkReimbursementDocumentCase(
        token,
        householdId,
        selectedDocument?.id as string,
        vars.caseId,
        vars.options,
      ),
    onSuccess: async () => {
      await invalidateAfterChange();
    },
  });
  const unlinkCase = useMutation({
    mutationFn: () =>
      unlinkReimbursementDocumentCase(
        token,
        householdId,
        selectedDocument?.id as string,
        itemIndex == null ? null : (effectiveData?.items?.[itemIndex]?.source_index ?? itemIndex),
      ),
    onSuccess: () => invalidateAfterChange(),
  });
  const reprocessAll = useMutation({
    mutationFn: () => reprocessAllReimbursementDocuments(token, householdId),
    onSuccess: async () => {
      setSelectedDocumentId(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: reimbursementDocumentsQueryKey(householdId) }),
        queryClient.invalidateQueries({ queryKey: ["ai-tasks", selectedHouseholdId] }),
      ]);
    },
  });
  const reprocessDocument = useMutation({
    mutationFn: (documentId: string) =>
      reprocessReimbursementDocument(token, householdId, documentId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: reimbursementDocumentsQueryKey(householdId) }),
        queryClient.invalidateQueries({ queryKey: ["ai-tasks", selectedHouseholdId] }),
      ]);
    },
  });

  const submitFirstPerson = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = personName.trim();
    if (value) createPerson.mutate(value);
  };

  const reload = () => {
    void peopleQuery.refetch();
    void casesQuery.refetch();
    void summaryQuery.refetch();
    void documentsQuery.refetch();
  };

  const upload = async (files: File[], personId: string) => {
    for (const file of files) {
      await uploadReimbursementDocument(token, householdId, personId, file);
    }
    await Promise.all([
      invalidateAfterChange(),
      queryClient.invalidateQueries({ queryKey: ["ai-tasks", selectedHouseholdId] }),
    ]);
  };

  const loading =
    peopleQuery.isLoading ||
    casesQuery.isLoading ||
    summaryQuery.isLoading ||
    documentsQuery.isLoading;
  const firstError =
    peopleQuery.error ?? casesQuery.error ?? summaryQuery.error ?? documentsQuery.error;
  const people = peopleQuery.data ?? [];

  if (!loading && !firstError && people.length === 0) {
    return (
      <section className="page reimbursement-page">
        <header className="page-header reimbursement-page-header">
          <div className="reimbursement-heading-copy">
            <span className="reimbursement-eyebrow">GESUNDHEIT &amp; ERSTATTUNG</span>
            <h1 className="page-heading">Wer soll abgerechnet werden?</h1>
            <p>
              Gesundheitsdaten bleiben zusätzlich zum Haushalt auf freigegebene Personen begrenzt.
            </p>
          </div>
        </header>
        <form className="reimbursement-onboarding" onSubmit={submitFirstPerson}>
          <div>
            <span className="reimbursement-onboarding-step">SCHRITT 1 VON 1</span>
            <h2>Erste versicherte Person anlegen</h2>
            <p>
              Der Anzeigename genügt. Medizinische Details gehören weder hierhin noch in Protokolle.
            </p>
          </div>
          <label>
            <span>Anzeigename</span>
            <input
              className="form-input"
              value={personName}
              onChange={(event) => setPersonName(event.target.value)}
              maxLength={120}
              autoFocus
              required
              placeholder="z. B. Muster"
            />
          </label>
          <button className="btn-primary" type="submit" disabled={createPerson.isPending}>
            {createPerson.isPending ? "Wird angelegt …" : "Tracker starten"}
          </button>
          {createPerson.error && <p role="alert">{errorMessage(createPerson.error)}</p>}
        </form>
      </section>
    );
  }

  const personNameFor = (personId: string) =>
    people.find((person) => person.id === personId)?.display_name ?? "Unbekannte Person";

  const sourceIndex =
    itemIndex == null ? null : (effectiveData?.items?.[itemIndex]?.source_index ?? itemIndex);
  const linkedId =
    selectedDocument?.linked_items?.find((link) => link.item_index === sourceIndex)?.case_id ??
    (itemIndex == null ? selectedDocument?.case_id : null);
  const candidateFor = (
    item: NonNullable<typeof casesQuery.data>[number],
    manual = false,
  ): ReimbursementCaseCandidate => ({
    case_id: item.id,
    person_id: item.person_id,
    person_name: item.person_name,
    provider_name: item.provider_name,
    invoice_number: item.invoice_number,
    invoice_date: item.invoice_date,
    billed_minor: item.billed_minor,
    reimbursed_minor: item.reimbursed_minor,
    currency: item.currency,
    overall_status: item.overall_status,
    version: item.version,
    expected_minor: item.expected_minor,
    score: 0,
    manual,
    evidence: [
      manual
        ? "Kein automatischer Treffer. Bitte am Original vergleichen."
        : "Bestehende Zuordnung",
    ],
  });
  const linkedCase = casesQuery.data?.find((item) => item.id === linkedId);
  const manualCandidates =
    effectiveData &&
    ["submission_receipt", "request_for_documents"].includes(effectiveData.document_type)
      ? (casesQuery.data ?? [])
          .filter((item) => item.person_id === selectedDocument?.insured_person_id)
          .slice(0, 20)
          .map((item) => candidateFor(item, true))
      : [];

  return (
    <>
      <OpenMarksPanel
        marks={marksQuery.data ?? []}
        people={people.map((person) => ({ id: person.id, name: person.display_name }))}
        onRemove={(transactionId) => removeMark.mutate(transactionId)}
        onConvert={(transactionId, insuredPersonId) =>
          convertMark.mutate({ transactionId, insuredPersonId })
        }
        busy={removeMark.isPending || convertMark.isPending}
      />
      <ReimbursementsPage
        summary={summaryQuery.data}
        cases={casesQuery.data ?? []}
        people={people.map((person) => ({ id: person.id, name: person.display_name }))}
        documents={documents}
        loading={loading}
        error={firstError ? errorMessage(firstError) : null}
        onReload={reload}
        onUpload={upload}
        selectedDocumentId={selectedDocument?.id ?? null}
        onSelectDocument={(id) => {
          setSelectedDocumentId(id);
          setSelectedItemIndex(null);
        }}
        onCreatePerson={(displayName) =>
          createPerson.mutateAsync(displayName).then(() => undefined)
        }
        createPersonPending={createPerson.isPending}
        onReprocessAllDocuments={() => reprocessAll.mutate()}
        reprocessAllPending={reprocessAll.isPending}
        selectedBankCaseId={selectedBankCaseId}
        onSelectBankCase={setSelectedBankCaseId}
        bankCandidates={bankCandidatesQuery.data ?? []}
        bankAllocations={bankAllocationsQuery.data ?? []}
        bankDetailLoading={bankCandidatesQuery.isPending || bankAllocationsQuery.isPending}
        bankBusy={allocate.isPending || removeAllocation.isPending}
        bankError={
          allocate.error
            ? errorMessage(allocate.error)
            : removeAllocation.error
              ? errorMessage(removeAllocation.error)
              : null
        }
        onAllocate={(caseId, transactionId, amountMinor) =>
          allocate.mutate({ caseId, transactionId, amountMinor })
        }
        onRemoveAllocation={(caseId, allocationId) =>
          removeAllocation.mutate({ caseId, allocationId })
        }
        documentInspector={
          selectedDocument && (
            <DocumentInspector
              key={selectedDocument.id}
              document={selectedDocument}
              personName={
                selectedDocument.insured_person_name ??
                personNameFor(selectedDocument.insured_person_id)
              }
              extraction={extractionReady ? (extractionQuery.data ?? null) : null}
              extractionLoading={extractionReady && extractionQuery.isLoading}
              extractionError={
                extractionReady && extractionQuery.error
                  ? errorMessage(extractionQuery.error)
                  : !extractionReady
                    ? "Das Dokument wird gerade ausgewertet."
                    : null
              }
              fileUrl={documentFile.url}
              fileLoading={documentFile.loading}
              fileError={documentFile.error}
              loadPage={loadDocumentPage}
              loadCasePreview={loadCasePreview}
              linkedCase={linkedCase ? candidateFor(linkedCase) : null}
              payers={payersQuery.data ?? []}
              selectedItemIndex={itemIndex}
              onSelectItem={setSelectedItemIndex}
              confirmPending={confirmExtraction.isPending}
              confirmError={confirmExtraction.error ? errorMessage(confirmExtraction.error) : null}
              reprocessPending={reprocessDocument.isPending}
              deletePending={removeDocument.isPending}
              onConfirm={(data) => confirmExtraction.mutate(data)}
              onReprocess={() => reprocessDocument.mutate(selectedDocument.id)}
              onDelete={() => removeDocument.mutate(selectedDocument.id)}
              onClose={() => setSelectedDocumentId(null)}
              caseCandidates={
                candidatesQuery.data?.length ? candidatesQuery.data : manualCandidates
              }
              caseCandidatesLoading={candidatesQuery.isLoading && selectedDocument.case_id == null}
              linkedCaseLabel={linkedId ? (linkedCase?.provider_name ?? "einem Vorgang") : null}
              linkPending={linkCase.isPending || unlinkCase.isPending}
              linkError={
                linkCase.error
                  ? reimbursementErrorMessage(linkCase.error)
                  : unlinkCase.error
                    ? reimbursementErrorMessage(unlinkCase.error)
                    : null
              }
              onUnlinkCase={() => unlinkCase.mutate()}
              onLinkCase={(caseId, options) => linkCase.mutate({ caseId, options })}
            />
          )
        }
      />
    </>
  );
}
