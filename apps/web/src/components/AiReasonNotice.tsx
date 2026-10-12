import type { AriaRole } from "react";
import { Link, useInRouterContext } from "react-router-dom";
import {
  AI_SETTINGS_PATH,
  aiLoadModelHint,
  aiReasonActions,
  aiReasonFromError,
  aiSettingsLabel,
  describeAiReason,
} from "../lib/aiReasons";
import "./AiReasonNotice.css";

type NoticeTag = "p" | "div" | "li" | "span";

interface AiReasonNoticeProps {
  /** Grund des KI-Kerns (z. B. `model_timeout`); null = nur Klartext. */
  code: string | null | undefined;
  /** Klartext des Servers; fehlt er, steht der Satz aus lib/aiReasons.ts. */
  message?: string | null;
  /** Einleitung, z. B. "Kein KI-Vorschlag verfügbar:" */
  prefix?: string;
  /** Nachsatz, z. B. "Bereits bewertete Paare bleiben nutzbar." */
  suffix?: string;
  as?: NoticeTag;
  className?: string;
  role?: AriaRole;
}

/**
 * Ein benannter KI-Grund in Klartext samt passender Aktion: Link zu
 * Einstellungen › KI, wo eine Einstellung die Ursache ist, Hinweis auf LM
 * Studio, wo das Modell fehlt. Der Schluessel steht als data-Attribut am
 * Element, nie als einzige Information im Text.
 */
export function AiReasonNotice({
  code,
  message,
  prefix,
  suffix,
  as: Tag = "p",
  className,
  role,
}: AiReasonNoticeProps) {
  const inRouter = useInRouterContext();
  const text =
    message?.trim() || (code ? describeAiReason(code) : "Die KI-Anfrage ist fehlgeschlagen.");
  const actions = aiReasonActions(code);
  const loadHint = actions.loadModel ? aiLoadModelHint(code) : null;
  const label = aiSettingsLabel(code);
  return (
    <Tag
      className={className ? `ai-reason ${className}` : "ai-reason"}
      role={role}
      data-ai-reason={code ?? undefined}
    >
      {prefix ? `${prefix} ` : null}
      {text}
      {suffix ? ` ${suffix}` : null}
      {loadHint && <span className="ai-reason__hint"> {loadHint}</span>}
      {actions.settings && (
        <>
          {" "}
          {inRouter ? (
            <Link className="ai-reason__action" to={AI_SETTINGS_PATH}>
              {label}
            </Link>
          ) : (
            <a className="ai-reason__action" href={AI_SETTINGS_PATH}>
              {label}
            </a>
          )}
        </>
      )}
    </Tag>
  );
}

interface AiErrorNoticeProps extends Omit<AiReasonNoticeProps, "code" | "message"> {
  error: unknown;
  /** Text fuer Fehler ohne KI-Grund; sonst die Meldung des Fehlers. */
  fallback?: string;
}

/**
 * Fehler einer Anfrage: mit KI-Grund ({"detail": {"code", "message"}}) als
 * AiReasonNotice, sonst als schlichter Text.
 */
export function AiErrorNotice({ error, fallback, ...rest }: AiErrorNoticeProps) {
  const reason = aiReasonFromError(error);
  if (reason) return <AiReasonNotice code={reason.code} message={reason.message} {...rest} />;
  const { prefix, suffix, as: Tag = "p", className, role } = rest;
  const text =
    fallback ?? (error instanceof Error && error.message ? error.message : "Unbekannter Fehler.");
  return (
    <Tag className={className} role={role}>
      {prefix ? `${prefix} ` : null}
      {text}
      {suffix ? ` ${suffix}` : null}
    </Tag>
  );
}
