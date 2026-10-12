"""CAMT.052/053 ISO 20022 XML Kontoauszug-Parser."""

import xml.etree.ElementTree as ET
from datetime import date, datetime

from worker.imports.canonical import (
    CanonicalTransaction,
    ImportParseError,
    parse_amount_to_minor_units,
)
from worker.imports.parsers.base import DetectionResult
from worker.imports.security import extract_single_file_from_zip


def _safe_parse_xml(xml_bytes: bytes) -> ET.Element:
    parser = ET.XMLParser()
    try:
        return ET.fromstring(xml_bytes, parser=parser)
    except ET.ParseError as exc:
        raise ImportParseError(f"ungueltiges XML-Dokument: {exc}") from exc


def _find_text(element: ET.Element, namespaces: dict[str, str], paths: list[str]) -> str | None:
    for path in paths:
        node = element.find(path, namespaces)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return None


def _extract_namespace(root: ET.Element) -> str:
    if root.tag.startswith("{"):
        return root.tag.split("}")[0][1:]
    return ""


def _extract_bank_transaction_code(
    scope: ET.Element, namespaces: dict[str, str], pfx: str
) -> str | None:
    """Liest BkTxCd (ISO-20022-Geschaeftsvorfallcode) als kurzen String aus.

    ISO staffelt den Code dreistufig (Domn/Cd, Domn/Fmly/Cd,
    Domn/Fmly/SubFmlyCd). Gespeichert wird die Slash-Form "PMNT/RDDT/ESDD":
    sie behaelt die Reihenfolge des Standards, passt in die eine Spalte
    transactions.bank_transaction_code und laesst sich vom Vertrags-Score durch
    Aufteilen an "/" verlustfrei in die Ebenen zerlegen. Fehlende Ebenen
    entfallen, statt als Platzhalter mitzulaufen, damit derselbe Code bei jedem
    Institut identisch aussieht.

    Sparkassen und Volksbanken liefern statt Domn den DK-eigenen Prtry/Cd
    ("NSTO+152"). Der wird unveraendert uebernommen: eine Uebersetzung in die
    ISO-Familien waere geraten, und der Parser soll lesen, nicht deuten.
    """
    bk_tx_cd = scope.find(f"{pfx}BkTxCd", namespaces)
    if bk_tx_cd is None:
        return None

    levels = [
        _find_text(bk_tx_cd, namespaces, [f"{pfx}Domn/{pfx}Cd"]),
        _find_text(bk_tx_cd, namespaces, [f"{pfx}Domn/{pfx}Fmly/{pfx}Cd"]),
        _find_text(bk_tx_cd, namespaces, [f"{pfx}Domn/{pfx}Fmly/{pfx}SubFmlyCd"]),
    ]
    parts = [level for level in levels if level]
    if parts:
        return "/".join(parts)
    return _find_text(bk_tx_cd, namespaces, [f"{pfx}Prtry/{pfx}Cd"])


def _parse_iso_date(raw: str) -> date:
    cleaned = raw.strip()
    if "T" in cleaned:
        cleaned = cleaned.split("T")[0]
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ImportParseError(f"ungueltiges CAMT-Datum: {raw!r}") from exc


class CamtParser:
    format_name = "camt"

    def detect(self, file_bytes: bytes, filename: str) -> DetectionResult:
        payload = file_bytes
        if filename.lower().endswith(".zip"):
            try:
                payload, _ = extract_single_file_from_zip(file_bytes)
            except Exception as exc:
                return DetectionResult(False, self.format_name, 0.0, str(exc))

        if not (payload.startswith(b"<?xml") or b"<Document" in payload[:500]):
            return DetectionResult(False, self.format_name, 0.0, "kein XML")

        try:
            root = _safe_parse_xml(payload[:2048] + b"</Document>")
        except Exception:
            if any(f"camt.0{n}".encode() in payload for n in (52, 53, 54)):
                return DetectionResult(
                    True, self.format_name, 0.9, "camt.052/053/054 Namespace gefunden"
                )
            return DetectionResult(False, self.format_name, 0.0, "kein CAMT Namespace")

        ns = _extract_namespace(root)
        if any(f"camt.0{n}" in ns for n in (52, 53, 54)):
            return DetectionResult(True, self.format_name, 0.95, f"CAMT Namespace: {ns}")
        return DetectionResult(False, self.format_name, 0.0, "anderer XML-Namespace")

    def parse(self, file_bytes: bytes) -> list[CanonicalTransaction]:
        payload = file_bytes
        if len(file_bytes) >= 4 and file_bytes[:4] == b"PK\x03\x04":
            payload, _ = extract_single_file_from_zip(file_bytes)

        root = _safe_parse_xml(payload)
        ns_uri = _extract_namespace(root)
        ns = {"camt": ns_uri} if ns_uri else {}
        pfx = "camt:" if ns_uri else ""

        entries = root.findall(f".//{pfx}Ntry", ns)
        if not entries:
            return []

        results: list[CanonicalTransaction] = []
        for ntry in entries:
            dt_str = _find_text(
                ntry, ns, [f"{pfx}BookgDt/{pfx}Dt", f"{pfx}BookgDt/{pfx}DtTm"]
            ) or _find_text(ntry, ns, [f"{pfx}ValDt/{pfx}Dt", f"{pfx}ValDt/{pfx}DtTm"])
            if not dt_str:
                continue

            booked_date = _parse_iso_date(dt_str)
            val_str = _find_text(ntry, ns, [f"{pfx}ValDt/{pfx}Dt", f"{pfx}ValDt/{pfx}DtTm"])
            value_date = _parse_iso_date(val_str) if val_str else None

            amt_node = ntry.find(f"{pfx}Amt", ns)
            if amt_node is None or not amt_node.text:
                continue
            currency = amt_node.attrib.get("Ccy", "EUR")
            raw_amt = amt_node.text.strip()

            cdt_dbt = _find_text(ntry, ns, [f"{pfx}CdtDbtInd"])
            is_credit = cdt_dbt in ("CRDT", "RCVD")
            if not is_credit and not raw_amt.startswith("-"):
                raw_amt = "-" + raw_amt

            amount_minor = parse_amount_to_minor_units(raw_amt)

            # BkTxCd steht bei manchen Instituten nur am Eintrag, bei anderen
            # nur in den Transaktionsdetails; der Eintrag hat Vorrang, weil er
            # auch bei Sammelbuchungen mit mehreren TxDtls eindeutig ist.
            bank_transaction_code = _extract_bank_transaction_code(ntry, ns, pfx)

            tx_dtls = ntry.find(f".//{pfx}TxDtls", ns)
            payee: str | None = None
            purpose: str | None = None
            ref: str | None = None

            if tx_dtls is not None:
                payee = (
                    _find_text(
                        tx_dtls,
                        ns,
                        [
                            f".//{pfx}RltdPties/{pfx}Cdtr/{pfx}Pty/{pfx}Nm",
                            f".//{pfx}RltdPties/{pfx}Cdtr/{pfx}Nm",
                            f".//{pfx}RltdPties/{pfx}Dbtr/{pfx}Pty/{pfx}Nm",
                            f".//{pfx}RltdPties/{pfx}Dbtr/{pfx}Nm",
                        ],
                    )
                )
                purpose = _find_text(
                    tx_dtls,
                    ns,
                    [
                        f".//{pfx}RmtInf/{pfx}Ustrd",
                        f".//{pfx}RmtInf/{pfx}Strd/{pfx}CdtrRefInf/{pfx}Ref",
                    ],
                )
                ref = _find_text(
                    tx_dtls,
                    ns,
                    [
                        f".//{pfx}Refs/{pfx}EndToEndId",
                        f".//{pfx}Refs/{pfx}AcctSvcrRef",
                        f".//{pfx}Refs/{pfx}TxId",
                    ],
                )
                if not bank_transaction_code:
                    bank_transaction_code = _extract_bank_transaction_code(tx_dtls, ns, pfx)

            if not ref:
                ref = _find_text(ntry, ns, [f"{pfx}NtryRef", f"{pfx}AcctSvcrRef"])

            results.append(
                CanonicalTransaction(
                    booked_date=booked_date,
                    value_date=value_date,
                    amount_minor=amount_minor,
                    currency=currency,
                    payee_raw=payee,
                    purpose=purpose,
                    external_reference=ref,
                    bank_transaction_code=bank_transaction_code,
                )
            )

        return results
