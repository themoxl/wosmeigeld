import type { ReactNode } from "react";
import type { NumberCheck } from "../../lib/assistantTypes";
import {
  numberRanges,
  numberTooltip,
  plural,
  segmentRange,
  type NumberRange,
} from "../../lib/assistantFormat";
import { parseMarkdown, type InlineRun } from "../../lib/assistantMarkdown";

interface NumberMarkedTextProps {
  content: string;
  numbers: NumberCheck | null;
}

function Runs({
  content,
  runs,
  ranges,
}: {
  content: string;
  runs: InlineRun[];
  ranges: NumberRange[];
}) {
  return (
    <>
      {runs.map((run, runIndex) => {
        const parts: ReactNode[] = segmentRange(content, ranges, run.start, run.end).map(
          (segment, index) =>
            segment.item ? (
              <mark
                key={index}
                className={`assistant-number assistant-number--${segment.item.status}`}
                title={numberTooltip(segment.item)}
                data-status={segment.item.status}
                data-sources={segment.item.sources.join(" ")}
              >
                {segment.text}
                {segment.item.status === "unbelegt" && (
                  <span className="assistant-sr-only"> (ohne Beleg)</span>
                )}
              </mark>
            ) : (
              <span key={index}>{segment.text}</span>
            ),
        );
        if (run.code) return <code key={runIndex}>{parts}</code>;
        if (run.bold) return <strong key={runIndex}>{parts}</strong>;
        return <span key={runIndex}>{parts}</span>;
      })}
    </>
  );
}

/**
 * Antworttext mit markierten Zahlen: belegt (steht in einem Werkzeugergebnis
 * des Laufs) oder unbelegt. Der Tooltip nennt die Quellen (W1, W2, ...).
 *
 * Markdown des Modells (fett, Code, Ueberschriften, Listen, Tabellen) wird
 * gelesen (lib/assistantMarkdown.ts) und als React-Text gezeigt - nie als
 * HTML. Die Zahlenmarkierung haengt an den Originalpositionen und bleibt
 * dadurch richtig.
 */
export function NumberMarkedText({ content, numbers }: NumberMarkedTextProps) {
  const ranges = numberRanges(content, numbers?.items ?? []);
  const blocks = parseMarkdown(content);
  const unverified = numbers?.unverified ?? 0;
  return (
    <div className="assistant-answer">
      <div className="assistant-answer__text">
        {blocks.map((block, index) => {
          switch (block.kind) {
            case "heading":
              return (
                <p key={index} className="assistant-md-heading">
                  <Runs content={content} runs={block.runs} ranges={ranges} />
                </p>
              );
            case "list": {
              const items = block.items.map((runs, itemIndex) => (
                <li key={itemIndex}>
                  <Runs content={content} runs={runs} ranges={ranges} />
                </li>
              ));
              return block.ordered ? (
                <ol key={index} className="assistant-md-list">
                  {items}
                </ol>
              ) : (
                <ul key={index} className="assistant-md-list">
                  {items}
                </ul>
              );
            }
            case "table":
              return (
                <div key={index} className="assistant-md-table-wrap">
                  <table className="assistant-md-table">
                    <thead>
                      <tr>
                        {block.header.map((cell, cellIndex) => (
                          <th key={cellIndex}>
                            <Runs content={content} runs={cell} ranges={ranges} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {block.rows.map((row, rowIndex) => (
                        <tr key={rowIndex}>
                          {row.map((cell, cellIndex) => (
                            <td key={cellIndex}>
                              <Runs content={content} runs={cell} ranges={ranges} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            default:
              return (
                <p key={index} className="assistant-md-paragraph">
                  {block.lines.map((runs, lineIndex) => (
                    <span key={lineIndex}>
                      {lineIndex > 0 && <br />}
                      <Runs content={content} runs={runs} ranges={ranges} />
                    </span>
                  ))}
                </p>
              );
          }
        })}
      </div>
      {numbers === null ? null : unverified > 0 ? (
        <p className="assistant-answer__check assistant-answer__check--open">
          {plural(unverified, "Zahl", "Zahlen")} ohne Beleg – markiert, bitte selbst prüfen.
        </p>
      ) : numbers.items.length > 0 ? (
        <p className="assistant-answer__check">Alle Zahlen sind durch Abfragen belegt.</p>
      ) : null}
    </div>
  );
}
