// Kleine Markdown-Lesart fuer Antworten des KI-Felds.
//
// Lokale Modelle antworten fast immer mit Markdown (**fett**, Listen,
// Tabellen). Roh angezeigt stehen dann Sternchen und Rauten im Text. Eine
// Bibliothek gibt es im Projekt dafuer nicht, und HTML aus dem Modell darf nie
// ungeprueft in die Seite. Diese Lesart kennt deshalb nur die Formen, die in
// Finanzantworten vorkommen, und liefert ausschliesslich BEREICHE des
// Originaltexts - gerendert wird als React-Text, nie als HTML.
//
// Bereiche statt umgeschriebenen Texts: die Zahlenpruefung des Servers nennt
// Positionen im Originaltext (NumberCheckItem.start/end). Solange jede
// Textstelle ihre Originalposition behaelt, bleiben die Markierungen richtig.

/** Ein Stueck Fliesstext: Originalbereich [start, end) und seine Auszeichnung. */
export interface InlineRun {
  start: number;
  end: number;
  bold: boolean;
  code: boolean;
}

export type MarkdownBlock =
  | { kind: "paragraph"; lines: InlineRun[][] }
  | { kind: "heading"; runs: InlineRun[] }
  | { kind: "list"; ordered: boolean; items: InlineRun[][] }
  | { kind: "table"; header: InlineRun[][]; rows: InlineRun[][][] };

interface Line {
  start: number;
  end: number;
  text: string;
}

function splitLines(content: string): Line[] {
  const lines: Line[] = [];
  let start = 0;
  for (let i = 0; i <= content.length; i += 1) {
    if (i === content.length || content[i] === "\n") {
      let end = i;
      if (end > start && content[end - 1] === "\r") end -= 1;
      lines.push({ start, end, text: content.slice(start, end) });
      start = i + 1;
    }
  }
  return lines;
}

/**
 * Fliesstext zwischen start und end in Stuecke mit fett/Code zerlegen. Ein
 * `**` ohne Gegenstueck bleibt als Zeichen stehen - lieber ein sichtbares
 * Sternchen als verschluckter Text.
 */
export function parseInline(content: string, start: number, end: number): InlineRun[] {
  const runs: InlineRun[] = [];
  let cursor = start;
  let textStart = start;
  const push = (from: number, to: number, bold: boolean, code: boolean) => {
    if (to > from) runs.push({ start: from, end: to, bold, code });
  };
  while (cursor < end) {
    if (content[cursor] === "`") {
      const close = content.indexOf("`", cursor + 1);
      if (close > cursor && close < end) {
        push(textStart, cursor, false, false);
        push(cursor + 1, close, false, true);
        cursor = close + 1;
        textStart = cursor;
        continue;
      }
    }
    if (content.startsWith("**", cursor)) {
      const close = content.indexOf("**", cursor + 2);
      if (close > cursor + 2 && close + 2 <= end) {
        push(textStart, cursor, false, false);
        push(cursor + 2, close, true, false);
        cursor = close + 2;
        textStart = cursor;
        continue;
      }
    }
    cursor += 1;
  }
  push(textStart, end, false, false);
  return runs;
}

const HEADING = /^(\s{0,3})(#{1,6})\s+/;
const BULLET = /^(\s*)([-*•])\s+/;
const ORDERED = /^(\s*)(\d{1,3})[.)]\s+/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function isTableLine(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 1;
}

/** Zellen einer Tabellenzeile als Originalbereiche, ohne die Randstriche. */
function tableCells(content: string, line: Line): InlineRun[][] {
  const cells: InlineRun[][] = [];
  const first = content.indexOf("|", line.start);
  let cellStart = first + 1;
  for (let i = cellStart; i < line.end; i += 1) {
    if (content[i] === "|" && content[i - 1] !== "\\") {
      let from = cellStart;
      let to = i;
      while (from < to && content[from] === " ") from += 1;
      while (to > from && content[to - 1] === " ") to -= 1;
      cells.push(parseInline(content, from, to));
      cellStart = i + 1;
    }
  }
  return cells;
}

export function parseMarkdown(content: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const lines = splitLines(content);
  let paragraph: InlineRun[][] | null = null;
  let list: { kind: "list"; ordered: boolean; items: InlineRun[][] } | null = null;

  const closeParagraph = () => {
    if (paragraph && paragraph.length) blocks.push({ kind: "paragraph", lines: paragraph });
    paragraph = null;
  };
  const closeList = () => {
    if (list && list.items.length) blocks.push(list);
    list = null;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]!;
    if (line.text.trim() === "") {
      closeParagraph();
      closeList();
      continue;
    }

    const next = lines[index + 1];
    if (isTableLine(line.text) && next && TABLE_RULE.test(next.text)) {
      closeParagraph();
      closeList();
      const header = tableCells(content, line);
      const rows: InlineRun[][][] = [];
      index += 2;
      while (index < lines.length && isTableLine(lines[index]!.text)) {
        rows.push(tableCells(content, lines[index]!));
        index += 1;
      }
      index -= 1;
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    const heading = HEADING.exec(line.text);
    if (heading) {
      closeParagraph();
      closeList();
      blocks.push({
        kind: "heading",
        runs: parseInline(content, line.start + heading[0].length, line.end),
      });
      continue;
    }

    const bullet = BULLET.exec(line.text);
    const ordered = bullet ? null : ORDERED.exec(line.text);
    const marker = bullet ?? ordered;
    if (marker) {
      closeParagraph();
      const isOrdered = ordered !== null;
      if (!list || list.ordered !== isOrdered) {
        closeList();
        list = { kind: "list", ordered: isOrdered, items: [] };
      }
      list.items.push(parseInline(content, line.start + marker[0].length, line.end));
      continue;
    }

    closeList();
    if (!paragraph) paragraph = [];
    paragraph.push(parseInline(content, line.start, line.end));
  }
  closeParagraph();
  closeList();
  return blocks;
}
